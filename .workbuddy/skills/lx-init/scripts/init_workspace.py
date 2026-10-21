#!/usr/bin/env python3
"""Inspect or install project-only runtime; never fill credentials or change MCP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
VENV = ROOT / ".workbuddy/skills/lx-haibao/.venv"
REQUIREMENTS = ROOT / ".workbuddy/skills/lx-haibao/assets/runtime/requirements.txt"
CARD_REQUIREMENTS = ROOT / ".workbuddy/skills/lx-kaquan/requirements.txt"
ZUOTU_REQUIREMENTS = ROOT / ".workbuddy/skills/lx-zuotu/assets/runtime/requirements.txt"


def build_plan():
    return {"root": str(ROOT), "python": sys.executable, "python_version": list(sys.version_info[:3]), "python_supported": sys.version_info >= (3, 10),
            "git_available": bool(shutil.which("git")), "node_available": bool(shutil.which("node")),
            "venv": str(VENV), "venv_exists": VENV.exists(), "requirements_sha256": hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest(),
            "card_requirements": str(CARD_REQUIREMENTS), "card_requirements_sha256": hashlib.sha256(CARD_REQUIREMENTS.read_bytes()).hexdigest(),
            "zuotu_requirements": str(ZUOTU_REQUIREMENTS), "zuotu_requirements_sha256": hashlib.sha256(ZUOTU_REQUIREMENTS.read_bytes()).hexdigest(),
            "config_exists": {name: (ROOT / "config" / name).is_file() for name in ("fog_config.yaml", "lx-wenda.json")},
            "install_scope": "only project lx-haibao/.venv; no global install or credential writes"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--expected-preview-hash")
    args = parser.parse_args(argv)
    plan = build_plan()
    preview_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    print(json.dumps({**plan, "preview_hash": preview_hash}, ensure_ascii=False, indent=2))
    if not args.install or not args.confirmed:
        print("check only; no installs, reports, config files or API calls")
        return 0
    if args.expected_preview_hash != preview_hash:
        parser.error("确认 hash 不一致，未安装")
    if sys.version_info < (3, 10) or not plan["git_available"] or not plan["node_available"]:
        parser.error("需要 Python >=3.10、Node.js 和 Git。请先运行 python tools/run_skill.py init-runtime 补齐这三样；本命令只安装项目虚拟环境")
    if VENV.is_symlink() or any(p.is_symlink() for p in VENV.parents):
        parser.error("虚拟环境路径不能包含符号链接")
    if not VENV.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        parser.error("现有虚拟环境不完整，请联系我方处理；不删除/覆盖")
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS), "-r", str(CARD_REQUIREMENTS), "-r", str(ZUOTU_REQUIREMENTS)], check=True)
    subprocess.run([str(python), "-B", "-c", "import requests, PIL, zxingcpp, yaml, openpyxl; print('runtime imports verified')"], check=True)
    print("runtime installed and read back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
