"""Small public projections. Scientific originals are never rewritten."""
from __future__ import annotations

from pathlib import Path
import re

from . import backends, contracts

_CREDENTIAL = re.compile(r'''(?ix)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|client[_-]?secret|private[_-]?key|credentials?|token|password|secret|authorization|cookie)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|[^\s,;}]+)''')
_AUTH_SCHEME = re.compile(r'''(?i)\b(Bearer|Basic)\s+[^\s"'<>]+''')
_COOKIE_HEADER = re.compile(r'(?im)^(\s*(?:cookie|set-cookie)\s*:\s*)[^\r\n]+')
_TOKEN = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{15,})\b')
_PRIVATE_KEY = re.compile(r'-----BEGIN [^-\n]*PRIVATE KEY-----.*?(?:-----END [^-\n]*PRIVATE KEY-----|$)', re.S)


def cleaner(root, settings=None):
    """Build once per bounded read; do not traverse native homes or sessions."""
    root = Path(root).resolve()
    settings = contracts.settings(root) if settings is None else settings
    private = {str(root)}
    # Workspace/input error rendering may have no accepted launcher yet.
    try:home=backends.provider(settings.get('native_backend',backends.DEFAULT_BACKEND)).default_home()
    except ValueError:home=None
    if home is not None:private.add(str(home))
    for value in ((settings.get('resume_policy') or {}).get('native_home'),
                  (settings.get('ui') or {}).get('approval_secret_file')):
        if isinstance(value, str) and value:
            private.add(value)
    # Known host-private declarations may contain nested absolute paths, argv or
    # credential-shaped values. Public science roots remain legitimate content.
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and re.search(r'(?i)token|password|secret|api.?key', key):
                    if item: private.add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
        elif isinstance(value, str) and value.startswith('/'):
            private.add(value)
    for key in ('resume_policy', 'ui', 'native_options', 'native_launcher_argv', 'history_observer', 'delivery_policy'):
        collect(settings.get(key))
    private = sorted(private, key=len, reverse=True)

    def clean(text, limit=16384):
        text = str(text)
        for path in private:
            text = text.replace(path, '[private]')
        text = _PRIVATE_KEY.sub('[credential]', text)
        # Strip schemes before the field matcher can consume just "Bearer"
        # and leave its actual token behind. Keep quoted JSON values atomic.
        text = _AUTH_SCHEME.sub(lambda match: match[1] + ' [credential]', text)
        text = _COOKIE_HEADER.sub(lambda match: match[1] + '[credential]', text)
        text = _CREDENTIAL.sub(lambda match: match[1] + '[credential]', text)
        text = _TOKEN.sub('[credential]', text)
        text = ''.join(character for character in text if character in '\n\t' or ord(character) >= 32)
        return text[:limit]
    return clean


def event(value, clean):
    """Second strict allowlist after the backend's typed event projection."""
    kind = value.get('type')
    if kind in {'session.started', 'turn.completed'}:
        return dict(type=kind)
    if kind in {'message', 'thinking'} and isinstance(value.get('text'), str):
        if not value['text'].strip(): return None
        text = clean(value['text'])
        if not text.strip(): return None
        truncated=value.get('text_truncated') is True or len(value['text'])>16384
        if truncated:text+='\n[显示片段；完整文本保留在原始记录中。]'
        return dict(type=kind, text=text, omitted_characters=max(0, len(value['text']) - 16384),
            **(dict(text_truncated=True) if truncated else {}))
    if kind == 'tool' and isinstance(value.get('tool'), str) and value.get('status') in {'started', 'updated', 'completed', 'failed'}:
        return dict(type=kind, tool=clean(value['tool'], 128), status=value['status'])
    if kind == 'usage' and isinstance(value.get('usage'), dict):
        keys = ('input_tokens', 'cached_input_tokens', 'output_tokens')
        usage = value['usage']
        if all(type(usage.get(key)) is int and usage[key] >= 0 for key in keys):
            return dict(type=kind, usage={key: usage[key] for key in keys})
    return None


def settings_view(settings):
    """Actual frozen effective settings with host bindings omitted, never defaults."""
    keys = ('handoff_protocol', 'native_backend', 'model', 'reasoning_effort', 'service_tier',
            'role_profiles', 'native_options', 'max_tokens', 'max_seconds', 'max_steps',
            'node_budget_policy', 'role_budget_policy', 'search_policy', 'correction_policy',
            'experiment_mode', 'portfolio_policy', 'max_output_item_bytes', 'max_output_total_bytes')
    value = {key: settings[key] for key in keys if key in settings}
    options = value.get('native_options')
    if isinstance(options, dict):
        value['native_options'] = backends.selected(settings).public_options(options)
    value['ui'] = {key: item for key, item in settings.get('ui', {}).items() if key in {'port', 'open_browser'}}
    value['ui']['approval_secret_source_configured'] = bool(settings.get('ui', {}).get('approval_secret_file'))
    value['native_permissions'] = backends.permission_summary(settings)
    capabilities = settings.get('native_capabilities')
    if isinstance(capabilities, dict):
        value['native_capabilities'] = {key: capabilities[key] for key in
            ('response_schema_transport', 'session_resume', 'complete_native_authorship') if key in capabilities}
    value['source'] = 'frozen_instance'
    return value
