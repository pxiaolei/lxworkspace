#!/usr/bin/env python3
"""Offline allowlist, local-data and shared-file integrity checks."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

from brand_presets import load_manifest, verify_files

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SKILLS = {"lx_shujuku", "lx-yidongfenxi", "lx-haibao", "lx-wenda", "lx-kaquan", "lx-diaozhang", "lx-zuotu", "lx-init", "lx-update", "lxx_share"}
WORKSPACE_DOCS = {"workspace/README.md", "workspace/08端内宣传图/README.md"}


def check(root=ROOT):
    errors = []
    attributes = root / ".gitattributes"
    if not attributes.is_file() or "* -text" not in attributes.read_text(encoding="utf-8").splitlines():
        errors.append("缺少跨平台原始字节保护，Windows 换行转换会破坏公共 hash")
    skills = root / ".workbuddy/skills"
    actual = {p.parent.name for p in skills.glob("*/SKILL.md")}
    if actual != EXPECTED_SKILLS:
        errors.append("Skill 清单不等于首版 10 项")
    lock = json.loads((root / "config/source-lock.json").read_text(encoding="utf-8"))
    try:
        presets = load_manifest(root)
        errors.extend(verify_files(root, presets))
        if not set(presets["files"]).issubset(lock["files"]):
            errors.append("公开品牌文件未全部纳入公共真源 hash")
    except (ValueError, OSError, TypeError, AttributeError):
        presets = {"files": {}}
        errors.append("公开品牌清单无效或缺失")
    for relative, expected in lock["files"].items():
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append("公共真源文件 hash 漂移: " + relative)
    shared = {p.name for p in (skills / "lxx_share").glob("*.py")}
    if shared != {"__init__.py", "fog_config.py"}:
        errors.append("lxx_share 超出最小配置闭包")
    forbidden_imports = {"pymysql", "database", "data_reader", "evidence_adapter", "obsidian_event_adapter", "excel_utils", "openpyxl", "hhdata_metrics"}
    for file in skills.rglob("*.py"):
        if any(part in {".venv", "__pycache__"} for part in file.parts):
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        imports = [alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        imports += [node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
        forbidden = set(imports) & forbidden_imports
        if file.relative_to(skills).parts[0] == "lx-kaquan":
            forbidden.discard("openpyxl")
        if forbidden:
            errors.append("私人依赖进入分发: " + str(file.relative_to(root)))
    inventory = json.loads((root / "contracts/security/actions.json").read_text(encoding="utf-8"))
    if root == ROOT:
        from generate_workbuddy_entries import main
        if main(["--check"]):
            errors.append("WorkBuddy 薄入口漂移")
    for action in inventory["actions"]:
        if not (root / action["entrypoint"]).is_file():
            errors.append("动作入口缺失: " + action["entrypoint"])
        if action["risk"] in {"R2", "R3", "R4"} and not all(action.get(k) for k in ("preview", "confirmation", "negative_test")):
            errors.append("动作确认契约缺失: " + action["id"])
        if action["risk"] in {"R3", "R4"} and not action.get("readback"):
            errors.append("动作读回契约缺失: " + action["id"])
    if (root / ".git").exists():
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split('\0')
        for relative in paths:
            if not relative:
                continue
            path = Path(relative)
            protected = (relative.startswith("workspace/") and path.name != ".gitkeep" and relative not in WORKSPACE_DOCS) or (relative.startswith(".workbuddy/skills/lx-haibao/brands/") and path.suffix == ".yaml") or (relative.startswith(".workbuddy/skills/lx-haibao/brand-assets/") and path.name != "README.md") or relative in {"config/fog_config.yaml", "config/lx-wenda.json", ".workbuddy/skills/lx-wenda/config.json", ".workbuddy/skills/lx-wenda/_user_meta.json"} or path.name.startswith(".env")
            if protected and relative not in presets["files"]:
                errors.append("真实本地资料被 Git 跟踪: " + relative)
    return errors


if __name__ == "__main__":
    errors = check()
    print('\n'.join(errors) if errors else "distribution boundary: pass (10 skills; shared hashes; no private imports; protected local data)")
    raise SystemExit(bool(errors))
