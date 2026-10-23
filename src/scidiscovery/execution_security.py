"""Pure execution decision vocabulary and local host receipt primitives."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit


def execution_approval_option_id(decision: str) -> str:
    return {'accept': 'authorize_execution',
            'accept_with_exception': 'authorize_execution_with_exception',
            'reject': 'reject_execution', 'revise': 'revise_execution'}[decision]


def decision_authorizes(kind, selected, options, *, legacy=False):
    allowed = {execution_approval_option_id('accept'), execution_approval_option_id('accept_with_exception')}
    if legacy:
        allowed.add('approve')
    return kind == 'execution_authorization' and selected in allowed and selected in options


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def request_digest(value):
    """Bind an exact approval or side-effect retry request; never read payload files."""
    return hashlib.sha256(canonical(value)).hexdigest()


def receipt_hmac(secret: bytes, payload: bytes) -> str:
    if type(secret) is not bytes or len(secret) < 32:
        raise ValueError('Execution receipt requires the configured host secret')
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def token(secret, *parts):
    return receipt_hmac(secret, canonical(['scidiscovery.execution-authority.v1', *parts]))


def secret_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def read_secret_file(path, *, label, error_type=ValueError):
    source = Path(path).expanduser().absolute()
    try:
        metadata = os.lstat(source)
    except FileNotFoundError as error:
        raise error_type(f'{label} secret file does not exist') from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise error_type(f'{label} secret must be a regular non-symlink file')
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise error_type(f'{label} secret file must have mode 0600')
    # Refuse a symlink substituted between the metadata check and open.
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if ((opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                or not stat.S_ISREG(opened.st_mode) or stat.S_IMODE(opened.st_mode) & 0o077):
            raise error_type(f'{label} secret file changed while opening')
        raw = stream.read()
    if len(raw) < 32:
        raise error_type(f'{label} secret must contain at least 32 bytes')
    return raw


def require_same_origin(host, origin, expected):
    """No forwarded-host inference; the loopback listener supplies expected."""
    parsed = urlsplit(origin or '')
    if (host != expected or parsed.scheme != 'http' or parsed.netloc != expected
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise PermissionError('Exact local Host and Origin required')
