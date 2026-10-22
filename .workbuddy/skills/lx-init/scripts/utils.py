"""lx-init 通用工具。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class WriteResult:
    path: Path
    action: str
    message: str


def find_project_root(start: Path | None = None) -> Path:
    """查找 FOG 项目根目录。"""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".workbuddy").is_dir() and (candidate / "config").is_dir():
            return candidate
    raise RuntimeError("未找到 FOG 项目根目录（需包含 .workbuddy/ 和 config/）")


def load_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 文件。"""
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"配置文件格式不是 YAML 映射: {path}")
    return data


def dump_yaml(path: Path, data: dict[str, Any], dry_run: bool = False) -> WriteResult:
    """写 YAML 文件。"""
    if dry_run:
        return WriteResult(path=path, action="dry-run", message="预览写入")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    path.chmod(0o600)
    return WriteResult(path=path, action="written", message="已写入")


def safe_write_yaml(
    path: Path,
    data: dict[str, Any],
    project_root: Path,
    dry_run: bool = False,
    force: bool = False,
) -> WriteResult:
    """安全写入 YAML：默认不覆盖已有真实配置。"""
    if path.exists() and not force:
        return WriteResult(path=path, action="skipped", message="已存在，未覆盖")
    if dry_run:
        action = "dry-run-overwrite" if path.exists() else "dry-run"
        return WriteResult(path=path, action=action, message="预览写入")
    if path.exists() and force:
        backup_path = backup_file(path, project_root)
        result = dump_yaml(path, data, dry_run=False)
        result.message = f"已覆盖，原文件备份到 {backup_path}"
        return result
    return dump_yaml(path, data, dry_run=False)


def backup_file(path: Path, project_root: Path) -> Path:
    """备份单个文件到 .fog_backup。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    relative = path.resolve().relative_to(project_root.resolve())
    backup_path = project_root / ".fog_backup" / timestamp / relative
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return backup_path


def resolve_project_path(project_root: Path, value: str) -> Path:
    """把配置中的相对路径解析到项目根目录下。"""
    if not value:
        return project_root
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root / path


def rel(path: Path, project_root: Path) -> str:
    """返回项目相对路径；失败时返回绝对路径。"""
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)
