"""Validate the exact public poster-brand file list (standard library only)."""
import hashlib
import json
import re
from pathlib import Path

MANIFEST = "contracts/partner-brand-presets.json"
PREFIX = ".workbuddy/skills/lx-haibao/"
ID = re.compile(r"[a-z][a-z0-9_]*")
HASH = re.compile(r"[0-9a-f]{64}")


def parse_manifest(text):
    value = json.loads(text)
    if value.get("version") != 1:
        raise ValueError("不支持的公开品牌清单版本")
    brands, files = value.get("brands"), value.get("files")
    if not isinstance(brands, list) or not brands or any(not isinstance(b, str) or not ID.fullmatch(b) for b in brands) or len(set(brands)) != len(brands):
        raise ValueError("公开品牌 ID 清单无效")
    if not isinstance(files, dict):
        raise ValueError("公开品牌文件清单无效")
    allowed = {PREFIX + "brands/brands.yaml"}
    for brand in brands:
        allowed.add(PREFIX + f"brands/{brand}.yaml")
        allowed.add(PREFIX + f"brand-assets/{brand}/qr.png")
        logos = {PREFIX + f"brand-assets/{brand}/logo.{ext}" for ext in ("png", "jpg", "jpeg", "webp")}
        selected = logos.intersection(files)
        if len(selected) != 1:
            raise ValueError("每个公开品牌必须精确指定一个 Logo")
        allowed.update(selected)
    if set(files) != allowed or any(not isinstance(h, str) or not HASH.fullmatch(h) for h in files.values()):
        raise ValueError("公开品牌清单包含缺项、非法路径或无效 hash")
    return value


def load_manifest(root, optional=False):
    path = Path(root) / MANIFEST
    if optional and not path.exists():
        return {"version": 1, "brands": [], "files": {}}
    return parse_manifest(path.read_text(encoding="utf-8"))


def verify_files(root, manifest):
    errors = []
    root = Path(root).resolve()
    for relative, expected in manifest["files"].items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in path.parents if p != root.parent):
            errors.append("公开品牌路径越界或符号链接: " + relative)
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append("公开品牌文件缺失或 hash 不符: " + relative)
    return errors
