"""Instance-scoped browser grants, separate from scientific approval tokens."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from http.cookies import CookieError, SimpleCookie
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class BrowserAccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class BrowserAccess:
    instance_id: str
    scopes: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime


class BrowserAccessService:
    """A grant comes only from the existing local-management browser flow.

    The UI boot identity invalidates old browser sessions on restart. Archive
    restore can additionally require a grant issued after its storage epoch.
    Neither a grant nor its scopes is a scientific decision.
    """

    def __init__(self, *, secret: bytes, ui_session_id: str) -> None:
        if type(secret) is not bytes or len(secret) < 32 or not ui_session_id:
            raise ValueError("browser access requires the configured local UI identity")
        self._key = hashlib.sha256(b"scidiscovery.instance-browser.v1\0" + secret
            + b"\0" + ui_session_id.encode("utf-8")).digest()

    def issue(self, instance_id: str, *, maintenance: bool = False,
              now: datetime | None = None,
              lifetime: timedelta = timedelta(hours=4)) -> str:
        if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 256:
            raise ValueError("browser grant instance is invalid")
        if not timedelta(0) < lifetime <= timedelta(hours=24):
            raise ValueError("browser grant lifetime is invalid")
        current = _utc(now)
        value = {"version": 1, "instance_id": instance_id,
            "scopes": ["read", "maintenance"] if maintenance else ["read"],
            "issued_at": current.isoformat(), "expires_at": (current+lifetime).isoformat()}
        nonce = os.urandom(12)
        sealed = AESGCM(self._key).encrypt(nonce,
            json.dumps(value, separators=(",", ":"), sort_keys=True).encode(), _AAD)
        return base64.urlsafe_b64encode(nonce+sealed).rstrip(b"=").decode("ascii")

    def verify(self, token: str, *, instance_id: str, scope: str = "read",
               now: datetime | None = None,
               issued_after: datetime | None = None) -> BrowserAccess:
        if scope not in {"read", "maintenance"}:
            raise ValueError("unknown browser access scope")
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 2048:
                raise ValueError("invalid grant length")
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            value = json.loads(AESGCM(self._key).decrypt(raw[:12], raw[12:], _AAD))
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError("invalid grant version")
            scopes = value["scopes"]
            if scopes not in (["read"], ["read", "maintenance"]):
                raise ValueError("invalid grant scopes")
            issued = _timestamp(value["issued_at"])
            expires = _timestamp(value["expires_at"])
            if value["instance_id"] != instance_id or scope not in scopes:
                raise ValueError("grant does not authorize this instance and action")
            if not issued <= _utc(now) < expires:
                raise ValueError("grant has expired or is not yet valid")
            if issued_after is not None and issued <= _utc(issued_after):
                raise ValueError("grant predates instance restoration")
            return BrowserAccess(instance_id, tuple(scopes), issued, expires)
        except (InvalidTag, ValueError, KeyError, TypeError, UnicodeError) as error:
            raise BrowserAccessDenied("实例浏览凭据无效或已过期，请重新打开本地管理入口。") from error

    def from_cookie(self, header: str | None, *, instance_id: str,
                    scope: str = "read", **values) -> BrowserAccess:
        try:
            cookies = SimpleCookie()
            cookies.load(header or "")
            item = cookies.get(cookie_name(instance_id))
            token = item.value if item is not None else ""
        except CookieError as error:
            raise BrowserAccessDenied("实例浏览凭据无效。") from error
        return self.verify(token, instance_id=instance_id, scope=scope, **values)


_AAD = b"scidiscovery.instance-browser.v1"


def management_cookie(token: str, *, max_age: int) -> str:
    cookies = SimpleCookie()
    cookies["scid_management"] = token
    for key, value in {"path": "/", "httponly": True, "samesite": "Strict", "max-age": max(0, max_age)}.items():
        cookies["scid_management"][key] = value
    return cookies.output(header="").strip()


def management_token(header: str | None) -> str | None:
    try:
        cookies = SimpleCookie()
        cookies.load(header or "")
        item = cookies.get("scid_management")
        return item.value if item else None
    except CookieError:
        return None


def cookie_name(instance_id: str) -> str:
    return "scid_view_" + base64.urlsafe_b64encode(instance_id.encode("utf-8")).rstrip(b"=").decode("ascii")


def access_cookie(instance_id: str, token: str, *, max_age: int = 4*60*60) -> str:
    cookies = SimpleCookie()
    name = cookie_name(instance_id)
    cookies[name] = token
    cookies[name]["path"] = "/"
    cookies[name]["httponly"] = True
    cookies[name]["samesite"] = "Strict"
    cookies[name]["max-age"] = max_age
    return cookies.output(header="").strip()


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("browser grant time must include a timezone")
    return current.astimezone(timezone.utc)


def _timestamp(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
