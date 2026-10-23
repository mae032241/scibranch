"""Domain-neutral primitives shared by SciDiscovery schemas."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION: Final = 1
Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    ),
]
Sha256 = Annotated[
    str,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


class FrozenDict(dict[_Key, _Value]):
    """A JSON-serializable mapping with no public mutation operations."""

    __slots__ = ()

    @staticmethod
    def _immutable(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("FrozenDict is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __hash__(self) -> int:
        return hash(frozenset(self.items()))


class SchemaModel(BaseModel):
    """Strict, immutable base for versioned wire schemas."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    schema_version: Literal[1] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _freeze_nested_values(self) -> SchemaModel:
        for field_name in type(self).model_fields:
            object.__setattr__(
                self,
                field_name,
                _deep_freeze(getattr(self, field_name)),
            )
        return self

    def canonical_json(self) -> bytes:
        return canonical_json(self)

    @property
    def content_hash(self) -> str:
        return canonical_sha256(self)


def canonical_json(value: Any) -> bytes:
    """Encode a strictly JSON-compatible value as deterministic UTF-8 bytes."""

    normalized = _normalize_json(value)
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Hash the canonical JSON representation of a value."""

    return hashlib.sha256(canonical_json(value)).hexdigest()


def _deep_freeze(value: Any, active: set[int] | None = None) -> Any:
    """Freeze JSON collections and reject values without canonical JSON form."""

    if isinstance(value, SchemaModel):
        return value
    if isinstance(value, BaseModel):
        raise ValueError("nested models must inherit SchemaModel")

    if active is None:
        active = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic collections are not supported by SchemaModel")
        active.add(identity)
        try:
            frozen: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError(
                        "SchemaModel mapping keys must be strings for canonical JSON"
                    )
                frozen[key] = _deep_freeze(item, active)
            return FrozenDict(frozen)
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic collections are not supported by SchemaModel")
        active.add(identity)
        try:
            return tuple(_deep_freeze(item, active) for item in value)
        finally:
            active.remove(identity)
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("SchemaModel floats must be finite for canonical JSON")
        return value
    raise ValueError(
        "SchemaModel values must be JSON-compatible; got "
        f"{type(value).__name__}"
    )


def _normalize_json(value: Any, active: set[int] | None = None) -> Any:
    """Return built-in JSON containers without applying lossy coercions."""

    if active is None:
        active = set()

    if isinstance(value, SchemaModel):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic values are not valid JSON")
        active.add(identity)
        try:
            return {
                field_name: _normalize_json(getattr(value, field_name), active)
                for field_name in type(value).model_fields
            }
        finally:
            active.remove(identity)
    if isinstance(value, BaseModel):
        raise TypeError(
            "canonical JSON only supports BaseModel values that inherit SchemaModel"
        )

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic values are not valid JSON")
        active.add(identity)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError(
                        "canonical JSON requires mapping keys to be strings; "
                        f"got {type(key).__name__}"
                    )
                normalized[key] = _normalize_json(item, active)
            return normalized
        finally:
            active.remove(identity)

    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic values are not valid JSON")
        active.add(identity)
        try:
            return [_normalize_json(item, active) for item in value]
        finally:
            active.remove(identity)

    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Out of range float values are not JSON compliant")
        return value

    raise TypeError(
        "canonical JSON does not support values of type "
        f"{type(value).__name__}"
    )
