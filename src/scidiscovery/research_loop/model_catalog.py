"""Cached edit-time suggestions supplied by the selected native Backend.

Exact typed declarations remain authoritative. These optional, read-only lists
never participate in preparation, freezing, execution or scientific review.
"""
from __future__ import annotations

import copy
import json
import threading
import time

from . import backends

TTL_SECONDS = 300
_CACHE = {}
_LOCK = threading.Lock()


def unavailable(reason):
    return dict(models=[], source=None, fetched_at=None, error=reason)


def load(backend, *, launcher=None, native=None, base=None):
    """Query only this provider, caching its exact declared/native context."""
    try:
        provider = backends.provider(backend)
        context = provider.model_catalog_context(launcher=launcher, native=native, base=base)
        key = (provider, json.dumps(context, sort_keys=True, ensure_ascii=True, allow_nan=False))
        now = time.monotonic()
        with _LOCK:
            cached = _CACHE.get(key)
            if cached is not None and now - cached[0] < TTL_SECONDS:
                return copy.deepcopy(cached[1])
        value = provider.model_catalog(context)
    except (OSError, ValueError, TypeError) as error:
        return unavailable('模型目录暂不可用，请填写精确模型 ID：' + str(error))
    # A refresh must be able to observe metadata repaired since the last load.
    # Keep the TTL only for populated, successful native observations.
    if value.get('models') and not value.get('error'):
        with _LOCK:
            _CACHE[key] = (now, copy.deepcopy(value))
    return value


def load_all(names, **context):
    """Explicit callers may request several providers; forms request only one."""
    return {name: load(name, **context) for name in names}
