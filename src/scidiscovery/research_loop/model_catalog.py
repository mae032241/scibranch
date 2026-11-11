"""Edit-time model choices offered by installed backends.

This is a form convenience only. Selecting an entry writes the same exact model
identity the user could have typed; preparation, freezing and every execution
check continue to validate that exact value and never consult this catalog.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

TTL_SECONDS = 300
PI_TIMEOUT_SECONDS = 15
PI_THINKING_ORDER = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
_CACHE = {}
_LOCK = threading.Lock()


def _unavailable(reason):
    return dict(models=[], source=None, fetched_at=None, error=reason)


def _codex():
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    path = home / 'models_cache.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return _unavailable('未找到 Codex 模型目录（' + str(path) + '）；可运行一次 codex 后刷新。')
    except (OSError, ValueError) as error:
        return _unavailable('Codex 模型目录不可读：' + str(error))
    models = []
    for item in value.get('models', []) if isinstance(value, dict) else []:
        if not isinstance(item, dict) or not isinstance(item.get('slug'), str):
            continue
        if item.get('visibility') not in (None, 'list'):
            continue
        efforts = [level.get('effort') for level in item.get('supported_reasoning_levels') or []
                   if isinstance(level, dict) and isinstance(level.get('effort'), str)]
        tiers = [tier.get('id') for tier in item.get('service_tiers') or []
                 if isinstance(tier, dict) and isinstance(tier.get('id'), str)]
        models.append(dict(id=item['slug'], label=str(item.get('display_name') or item['slug']),
            efforts=efforts, default_effort=item.get('default_reasoning_level'),
            tiers=['default', *[tier for tier in tiers if tier != 'default']]))
    return dict(models=models, source=str(path), fetched_at=value.get('fetched_at') if isinstance(value, dict) else None,
                error=None if models else 'Codex 模型目录为空。')


def _pi():
    executable = shutil.which('pi')
    if executable is None:
        return _unavailable('未找到 pi 命令；请手动填写 provider/model-id。')
    try:
        completed = subprocess.run([executable, '--list-models'], capture_output=True, text=True,
            timeout=PI_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return _unavailable('读取 Pi 模型列表失败：' + str(error))
    if completed.returncode != 0:
        return _unavailable('pi --list-models 退出码 ' + str(completed.returncode))
    lines = [line.split() for line in completed.stdout.splitlines() if line.strip()]
    if not lines or lines[0][:2] != ['provider', 'model']:
        return _unavailable('无法识别 pi --list-models 的输出格式。')
    header = lines[0]
    thinking = header.index('thinking') if 'thinking' in header else None
    models = []
    for row in lines[1:]:
        if len(row) < 2:
            continue
        supports = thinking is not None and len(row) > thinking and row[thinking] == 'yes'
        identity = row[0] + '/' + row[1]
        models.append(dict(id=identity, label=identity,
            efforts=list(PI_THINKING_ORDER) if supports else ['off'], default_effort=None, tiers=[]))
    return dict(models=models, source='pi --list-models',
                fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                error=None if models else 'Pi 没有列出可用模型。')


_PRODUCERS = dict(codex=_codex, pi=_pi)


def load(backend):
    """Return a cached catalog for one backend; failures degrade to free text."""
    producer = _PRODUCERS.get(backend)
    if producer is None:
        return _unavailable('此后端不提供模型目录；请填写精确模型 ID。')
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(backend)
        if cached is not None and now - cached[0] < TTL_SECONDS:
            return cached[1]
    value = producer()
    with _LOCK:
        _CACHE[backend] = (now, value)
    return value


def load_all(names):
    return {name: load(name) for name in names}
