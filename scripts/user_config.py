#!/usr/bin/env python3
"""Resolve chaochun-subtitle settings with backward-compatible legacy fallbacks.

Preferred identity: ``~/.config/chaochun-subtitle/`` and ``CHAOCHUN_SUBTITLE_*``.

Lookup order for config files (API key, config.json, glossary.json):
1. New env vars (``CHAOCHUN_SUBTITLE_*``)
2. New files under ``~/.config/chaochun-subtitle/``
3. Legacy ``OIL_SUBTITLE_*`` / ``SCREEN_STUDIO_EDITOR_*`` env
4. Legacy ``~/.config/oil-subtitle/`` (then screen-studio-editor for config.json)
5. Legacy ``~/.bailian/config.json`` for API key only
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PREFERRED_CONFIG = Path.home() / ".config" / "chaochun-subtitle" / "config.json"
PREFERRED_API_KEY_FILE = (
    Path.home() / ".config" / "chaochun-subtitle" / "dashscope_api_key"
)
PREFERRED_GLOSSARY = Path.home() / ".config" / "chaochun-subtitle" / "glossary.json"
LEGACY_OIL_CONFIG = Path.home() / ".config" / "oil-subtitle" / "config.json"
LEGACY_OIL_API_KEY_FILE = (
    Path.home() / ".config" / "oil-subtitle" / "dashscope_api_key"
)
LEGACY_OIL_GLOSSARY = Path.home() / ".config" / "oil-subtitle" / "glossary.json"
LEGACY_BAILIAN_CONFIG = Path.home() / ".bailian" / "config.json"
LEGACY_CONFIG = (
    Path.home() / ".config" / "screen-studio-editor" / "config.json"
)


def env_value(*names: str, legacy_name: str | None = None) -> str:
    ordered = [name for name in names if name]
    if legacy_name:
        ordered.append(legacy_name)
    for name in ordered:
        value = os.environ.get(name)
        if value is None:
            continue
        stripped = str(value).strip()
        if stripped:
            return stripped
    return ""


def namespaced_env(suffix: str) -> str:
    """Read CHAOCHUN_SUBTITLE_* first, then oil-subtitle / screen-studio-editor."""
    return env_value(
        f"CHAOCHUN_SUBTITLE_{suffix}",
        f"OIL_SUBTITLE_{suffix}",
        f"SCREEN_STUDIO_EDITOR_{suffix}",
    )


def _unique_paths(*paths: Path) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _resolve_user_file(
    *,
    preferred: Path,
    legacy_paths: tuple[Path, ...],
    env_suffix: str,
) -> Path:
    configured = env_value(f"CHAOCHUN_SUBTITLE_{env_suffix}")
    if configured:
        return Path(configured).expanduser()
    if preferred.exists():
        return preferred
    configured = env_value(
        f"OIL_SUBTITLE_{env_suffix}",
        f"SCREEN_STUDIO_EDITOR_{env_suffix}",
    )
    if configured:
        return Path(configured).expanduser()
    for path in legacy_paths:
        if path.exists():
            return path
    return preferred


def config_path() -> Path:
    return _resolve_user_file(
        preferred=PREFERRED_CONFIG,
        legacy_paths=(LEGACY_OIL_CONFIG, LEGACY_CONFIG),
        env_suffix="CONFIG",
    )


def load_user_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid chaochun-subtitle config: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"chaochun-subtitle config must be a JSON object: {path}")
    return payload


def resolve_progress_enabled(override: bool | None = None) -> bool:
    """Resolve the chapter progress switch; enabled is the safe default."""
    if override is not None:
        return bool(override)

    configured_env = namespaced_env("PROGRESS_ENABLED")
    config = load_user_config()
    subtitle_config = config.get("subtitles") or {}
    if not isinstance(subtitle_config, dict):
        raise RuntimeError(
            "subtitles must be a JSON object in the chaochun-subtitle config"
        )
    value = (
        configured_env
        if configured_env
        else subtitle_config.get("progress_enabled", True)
    )
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("subtitles.progress_enabled must be true or false")


def optional_user_path(
    config: dict[str, Any],
    key: str,
    env_name: str,
    *more_env_names: str,
    legacy_env_name: str | None = None,
) -> Path | None:
    value = env_value(
        env_name, *more_env_names, legacy_name=legacy_env_name
    ) or str(config.get(key) or "").strip()
    return Path(value).expanduser() if value else None


def resolve_glossary_path(override: str | Path | None = None) -> Path:
    """Resolve the shared personal glossary, with a usable default path."""
    if override:
        return Path(override).expanduser()
    configured = env_value("CHAOCHUN_SUBTITLE_GLOSSARY")
    if configured:
        return Path(configured).expanduser()
    from_config = str(load_user_config().get("glossary") or "").strip()
    if from_config:
        return Path(from_config).expanduser()
    return _resolve_user_file(
        preferred=PREFERRED_GLOSSARY,
        legacy_paths=(LEGACY_OIL_GLOSSARY,),
        env_suffix="GLOSSARY",
    )


def dashscope_api_key_file() -> Path:
    """Preferred path for saving the DashScope API key."""
    configured = env_value("CHAOCHUN_SUBTITLE_API_KEY_FILE")
    return Path(configured).expanduser() if configured else PREFERRED_API_KEY_FILE


def _candidate_api_key_files() -> list[Path]:
    new_env = env_value("CHAOCHUN_SUBTITLE_API_KEY_FILE")
    legacy_env = env_value(
        "OIL_SUBTITLE_API_KEY_FILE", "SCREEN_STUDIO_EDITOR_API_KEY_FILE"
    )
    return _unique_paths(
        *(
            [Path(new_env).expanduser()]
            if new_env
            else []
        ),
        PREFERRED_API_KEY_FILE,
        *([Path(legacy_env).expanduser()] if legacy_env else []),
        LEGACY_OIL_API_KEY_FILE,
    )


def _read_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def legacy_bailian_api_key() -> str:
    try:
        payload = json.loads(LEGACY_BAILIAN_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("api_key") or "").strip() if isinstance(payload, dict) else ""


def load_dashscope_api_key(*, required: bool = True) -> str:
    key = env_value("DASHSCOPE_API_KEY")
    if not key:
        for path in _candidate_api_key_files():
            key = _read_secret(path)
            if key:
                break
    if not key:
        key = legacy_bailian_api_key()
    if not key and required:
        raise RuntimeError(
            "DashScope API key is not configured. Run "
            "`.venv/bin/python3 scripts/configure_api_key.py`."
        )
    return key


def save_dashscope_api_key(key: str, path: Path | None = None) -> Path:
    key = str(key or "").strip()
    if not key:
        raise ValueError("DashScope API key must not be empty")
    target = (path or dashscope_api_key_file()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(key + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)
    return target


def migrate_legacy_dashscope_api_key_file() -> Path | None:
    """Copy ``~/.config/oil-subtitle/dashscope_api_key`` to the preferred path."""
    target = dashscope_api_key_file()
    if _read_secret(target):
        return None
    key = _read_secret(LEGACY_OIL_API_KEY_FILE)
    if not key:
        return None
    try:
        if LEGACY_OIL_API_KEY_FILE.resolve() == target.expanduser().resolve():
            return None
    except OSError:
        pass
    return save_dashscope_api_key(key, target)


def default_vocabulary_cache_path() -> Path:
    cache_root = Path(
        os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    ).expanduser()
    preferred = cache_root / "chaochun-subtitle" / "vocabulary-cache.json"
    legacy = cache_root / "oil-subtitle" / "vocabulary-cache.json"
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy
