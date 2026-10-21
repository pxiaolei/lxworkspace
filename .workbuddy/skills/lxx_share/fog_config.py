"""FOG project-level configuration helpers.

All shared Skills should read user-specific settings from
config/fog_config.yaml instead of per-Skill assets/config.yaml files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import yaml


def find_project_root(start: Path | str | None = None) -> Path:
    """Find the FOG project root by walking upward from start."""
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "config" / "fog_config.yaml").exists():
            return candidate
        if (candidate / ".workbuddy").is_dir() and (candidate / "config").is_dir():
            return candidate
    return Path.cwd().resolve()


def fog_config_path(start: Path | str | None = None) -> Path:
    """Return the active fog_config.yaml path."""
    env_value = os.environ.get("FOG_CONFIG_PATH")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return find_project_root(start) / "config" / "fog_config.yaml"


def personal_config_path(start: Path | str | None = None) -> Path:
    """Return the local-only personal_config.yaml path."""
    env_value = os.environ.get("FOG_PERSONAL_CONFIG_PATH")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return find_project_root(start) / "config" / "personal_config.yaml"


def load_fog_config(start: Path | str | None = None, required: bool = False) -> dict[str, Any]:
    """Load config/fog_config.yaml."""
    path = fog_config_path(start)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"FOG 统一配置不存在: {path}")
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def load_personal_config(start: Path | str | None = None, required: bool = False) -> dict[str, Any]:
    """Load config/personal_config.yaml."""
    path = personal_config_path(start)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"FOG 个人配置不存在: {path}")
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def save_fog_config(data: dict[str, Any], start: Path | str | None = None) -> Path:
    """Write config/fog_config.yaml with local-only permissions."""
    path = fog_config_path(start)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def save_personal_config(data: dict[str, Any], start: Path | str | None = None) -> Path:
    """Write config/personal_config.yaml with local-only permissions."""
    path = personal_config_path(start)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def get_section(name: str, start: Path | str | None = None) -> dict[str, Any]:
    """Return one top-level section from fog_config.yaml."""
    section = load_fog_config(start).get(name, {})
    return section if isinstance(section, dict) else {}


def get_personal_section(name: str, start: Path | str | None = None) -> dict[str, Any]:
    """Return one top-level section from personal_config.yaml."""
    section = load_personal_config(start).get(name, {})
    return section if isinstance(section, dict) else {}


def get_nested(data: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    """Read a nested key path from a mapping."""
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def set_nested(data: dict[str, Any], keys: Iterable[str], value: Any) -> None:
    """Write a nested key path into a mapping."""
    path = list(keys)
    if not path:
        return
    current = data
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


def merge_missing(base: Any, defaults: Any) -> Any:
    """Recursively add missing default keys without overriding existing values."""
    if isinstance(base, dict) and isinstance(defaults, dict):
        merged = dict(base)
        for key, value in defaults.items():
            if key in merged:
                merged[key] = merge_missing(merged[key], value)
            else:
                merged[key] = value
        return merged
    return base


def resolve_project_path(value: str | Path | None, start: Path | str | None = None, default: str = "") -> Path:
    """Resolve a path relative to the FOG project root."""
    root = find_project_root(start)
    raw = str(value or default)
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return root / path
