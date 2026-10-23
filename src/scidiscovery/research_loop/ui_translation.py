"""Application field translations, independent of scientific runs and budgets.

Reads never launch a model. New calls return unsupported before enqueueing. Exact source strings and canonical
generation settings are the SQLite key; scientific files are never opened here.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import uuid

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib



_RULES = "field-translation-v1"
_FAILED = "Translation did not complete. Request a retry explicitly."
_OPTIONS = {"backend", "launcher_argv", "model", "target_language", "reasoning_effort",
            "service_tier", "timeout_seconds", "enabled"}
def load_config(path):
    """Read only the optional application ``[translation]`` TOML table."""
    try:
        with Path(path).open("rb") as stream:
            value = tomllib.load(stream).get("translation", {})
    except (OSError, ValueError):
        raise ValueError("Cannot read the translation configuration file.") from None
    if not isinstance(value, dict):
        raise ValueError("Translation configuration must be a TOML table.")
    _configuration(value)
    return value


def _configuration(value):
    if not isinstance(value, dict) or set(value) - _OPTIONS:
        raise ValueError("Unsupported translation configuration fields.")
    config = dict(backend="codex", launcher_argv=None, model=None, target_language="zh-CN",
                  reasoning_effort=None, service_tier=None, timeout_seconds=120)
    config.update(value)
    if config["backend"] not in ("codex", "protocol"):
        raise ValueError("Translation backend must be codex or protocol.")
    for key, limit in (("model", 256), ("target_language", 64),
                       ("reasoning_effort", 64), ("service_tier", 64)):
        field = config[key]
        if field is None and key != "target_language":
            continue
        if (not isinstance(field, str) or not field.strip() or len(field) > limit
                or any(ord(char) < 32 for char in field)):
            raise ValueError("Translation " + key + " must be a nonempty bounded string.")
        try:
            field.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Translation settings must contain valid Unicode.") from None
    launcher = config["launcher_argv"]
    if launcher is not None and (not isinstance(launcher, list) or not launcher or len(launcher) > 64
            or any(not isinstance(arg, str) or not arg or len(arg) > 4096 or "\0" in arg for arg in launcher)):
        raise ValueError("Translation launcher_argv must be a nonempty bounded argument list.")
    if config["backend"] == "protocol" and launcher is None:
        raise ValueError("Translation protocol backend requires launcher_argv.")
    try:
        for argument in launcher or []:
            argument.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Translation settings must contain valid Unicode.") from None
    seconds = config["timeout_seconds"]
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or not 1 <= seconds <= 300):
        raise ValueError("Translation timeout_seconds must be between 1 and 300.")
    config.setdefault("enabled", config["model"] is not None)
    if type(config["enabled"]) is not bool:
        raise ValueError("Translation enabled must be a boolean.")
    if config["enabled"] and config["model"] is None:
        raise ValueError("Translation model is required when enabled.")
    return config


def _profile(config):
    return json.dumps(dict(rules=_RULES, backend=config["backend"],
        launcher_argv=config["launcher_argv"] or ["codex"], model=config["model"],
        target_language=config["target_language"], reasoning_effort=config["reasoning_effort"],
        service_tier=config["service_tier"]), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _texts(values):
    if not isinstance(values, (list, tuple)) or len(values) > 128:
        raise ValueError("Translation texts must be a list with at most 128 strings.")
    if any(not isinstance(value, str) or len(value) > 12000 for value in values):
        raise ValueError("Each translation field must be a string with at most 12000 characters.")
    try:
        size = sum(len(value.encode("utf-8")) for value in values)
    except UnicodeEncodeError:
        raise ValueError("Translation fields must contain valid Unicode.") from None
    if size > 180000:
        raise ValueError("Translation fields exceed the 180000 byte request limit.")
    return list(values)


class TranslationService:
    """Durable translations below an explicitly supplied application directory."""

    def __init__(self, directory, *, config=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._settings_path = self.directory / "translation-config.json"
        try:
            saved = json.loads(self._settings_path.read_text("utf-8")) if self._settings_path.exists() else {}
        except (OSError, ValueError):
            raise ValueError("Saved translation settings could not be read.") from None
        if not isinstance(saved, dict) or (config is not None and not isinstance(config, dict)):
            raise ValueError("Translation settings must be an object.")
        self._config = _configuration({**saved, **(config or {})})
        self._config_generation = uuid.uuid4().hex
        if config is not None:
            self._save_config(self._config)
        self._condition = threading.Condition(threading.RLock())
        self._closed = False
        database = self.directory / "translations.sqlite3"
        self._db = sqlite3.connect(database, check_same_thread=False)
        os.chmod(database, 0o600)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS translations (
                profile TEXT NOT NULL, source TEXT NOT NULL, translation TEXT,
                error TEXT, translated_at REAL, PRIMARY KEY (profile, source));
            CREATE TABLE IF NOT EXISTS translation_invocations (
                id INTEGER PRIMARY KEY, profile TEXT NOT NULL, created REAL NOT NULL,
                status TEXT NOT NULL, usage TEXT);
        """)
        if "translated_at" not in {row[1] for row in self._db.execute("PRAGMA table_info(translations)")}:
            self._db.execute("ALTER TABLE translations ADD COLUMN translated_at REAL")
        self._db.commit()

    def settings(self):
        with self._condition:
            value = {key: value for key, value in self._config.items() if key != "launcher_argv"}
            value["supported"] = False
            value["unsupported_reason"] = "当前后端未提供可验证的执行前禁工具合同；新翻译调用不可用。"
            value["configured"] = self._config["model"] is not None
            value["launcher_configured"] = self._config["launcher_argv"] is not None
            value["config_generation"] = self._config_generation
            return value

    def configure(self, mapping):
        if not isinstance(mapping, dict):
            raise ValueError("Translation settings must be an object.")
        with self._condition:
            self._require_open()
            merged = {**self._config, **mapping}
            if self._config["model"] is None and mapping.get("model") is not None and "enabled" not in mapping:
                merged["enabled"] = True
            config = _configuration(merged)
            self._save_config(config)
            self._config = config
            self._config_generation = uuid.uuid4().hex
            return self.settings()

    def _save_config(self, config):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix="translation-config-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(config, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._settings_path)
        except OSError:
            raise ValueError("Translation settings could not be saved.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _require_open(self):
        if self._closed:
            raise ValueError("Translation service is closed.")

    def lookup(self, texts):
        values = _texts(texts)
        with self._condition:
            self._require_open()
            return self._lookup(values)

    def _lookup(self, values):
        profile = _profile(self._config)
        entries = []
        counts = dict(cached=0, pending=0, failed=0, missing=0)
        for source in values:
            try:
                row = self._db.execute("SELECT translation, error FROM translations WHERE profile=? AND source=?",
                                       (profile, source)).fetchone()
            except sqlite3.Error:
                raise ValueError("Translation cache could not be read.") from None
            entry = dict(source=source, translation=None, status="missing")
            if row and row[0] is not None:
                entry.update(translation=row[0], status="cached")
            elif row:
                entry.update(status="failed", error=row[1] or _FAILED)
            counts[entry["status"]] += 1
            entries.append(entry)
        return dict(target_language=self._config["target_language"],
                    configured=self._config["model"] is not None, config_generation=self._config_generation,
                    entries=entries, counts=counts)

    def start(self, texts):
        values = _texts(texts)
        with self._condition:
            self._require_open()
            if not self._config["enabled"] or self._config["model"] is None:
                raise ValueError("Configure and enable an application translation model first.")
            # Neither current backend has a verified pre-tool-execution deny
            # contract. Do not enqueue, select a backend, or start a process.
            value = self._lookup(values)
            return dict(value, status='unsupported', reason='当前翻译后端无法在工具执行前禁用全部工具；仅可读取缓存译文与原文。')

    def close(self):
        with self._condition:
            if not self._closed:
                self._closed = True
                self._db.close()
