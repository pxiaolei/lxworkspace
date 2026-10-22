"""初始化 FOG workspace 目录。"""

from __future__ import annotations

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))


DEFAULT_DIRS = [
    "workspace/01主体拆表",
    "workspace/01主体拆表/输入",
    "workspace/01主体拆表/输出",
    "workspace/01主体拆表/原表存档",
    "workspace/01主体拆表/处理日志",
    "workspace/03数据报表/日报",
    "workspace/03数据报表/周报",
    "workspace/03数据报表/月报",
    "workspace/03数据报表/其他",
    "workspace/04数据分析",
]


def workspace_dirs(config: dict, project_root: Path) -> list[Path]:
    """根据配置返回需要创建的 workspace 目录。"""
    from utils import resolve_project_path
    dirs = [resolve_project_path(project_root, value) for value in DEFAULT_DIRS]
    zhutichaibiao = config.get("lx_zhutichaibiao", {})
    work_dir = zhutichaibiao.get("work_dir")
    if work_dir:
        base = resolve_project_path(project_root, work_dir)
        dirs.extend([
            base,
            base / "输入",
            base / "输出",
            base / "原表存档",
            base / "处理日志",
        ])
    if config.get("enabled_skills", {}).get("lx_dapanribao"):
        dailyreport = config.get("lx_dapanribao", {})
        output_dir = dailyreport.get("output_dir", "workspace/03数据报表/日报")
        dirs.append(resolve_project_path(project_root, output_dir))
    if config.get("enabled_skills", {}).get("lx_haibao"):
        haibao = config.get("lx_haibao", {})
        for key, default in (
            ("output_dir", "workspace/09端外海报图/产出图"),
            ("meta_dir", "workspace/09端外海报图/元数据"),
            ("tmp_dir", "workspace/09端外海报图/临时图"),
        ):
            dirs.append(resolve_project_path(project_root, haibao.get(key, default)))
    unique: list[Path] = []
    seen = set()
    for path in dirs:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def init_workspace(config: dict, project_root: Path, dry_run: bool = False) -> list[WriteResult]:
    """创建 workspace 目录和 .gitkeep。"""
    from utils import WriteResult
    results: list[WriteResult] = []
    for path in workspace_dirs(config, project_root):
        gitkeep = path / ".gitkeep"
        if dry_run:
            results.append(WriteResult(path=path, action="dry-run", message="预览创建目录"))
            continue
        path.mkdir(parents=True, exist_ok=True)
        gitkeep.touch(exist_ok=True)
        results.append(WriteResult(path=path, action="created", message="目录已存在或已创建"))
    return results



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


def requirement_files():
    profile = ROOT / "config/distribution.json"
    enabled = set(json.loads(profile.read_text(encoding="utf-8"))["skills"]) if profile.is_file() else None
    pairs = (("lx-haibao", REQUIREMENTS), ("lx-kaquan", CARD_REQUIREMENTS), ("lx-zuotu", ZUOTU_REQUIREMENTS))
    result = [path for skill, path in pairs if path.is_file() and (enabled is None or skill in enabled)]
    # Other distributed tools (for example FOG Excel skills) retain their own dependency files.
    if enabled is not None:
        for skill in sorted(enabled - {"lx-haibao", "lx-kaquan", "lx-zuotu", "lx-init"}):
            path = ROOT / ".workbuddy/skills" / skill / "requirements.txt"
            if path.is_file() and path not in result:
                result.append(path)
    return result


def optional_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def build_plan():
    return {"root": str(ROOT), "python": sys.executable, "python_version": list(sys.version_info[:3]), "python_supported": sys.version_info >= (3, 10),
            "git_available": bool(shutil.which("git")), "node_available": bool(shutil.which("node")),
            "venv": str(VENV), "venv_exists": VENV.exists(), "requirements_sha256": optional_hash(REQUIREMENTS),
            "card_requirements": str(CARD_REQUIREMENTS), "card_requirements_sha256": optional_hash(CARD_REQUIREMENTS),
            "zuotu_requirements": str(ZUOTU_REQUIREMENTS), "zuotu_requirements_sha256": optional_hash(ZUOTU_REQUIREMENTS),
            "requirements": [str(path) for path in requirement_files()],
            "requirement_hashes": {str(path): optional_hash(path) for path in requirement_files()},
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
    arguments = [item for path in requirement_files() for item in ("-r", str(path))]
    subprocess.run([str(python), "-m", "pip", "install", *arguments], check=True)
    dependency_text = "\n".join(path.read_text(encoding="utf-8").lower() for path in requirement_files())
    imports = "requests, PIL, zxingcpp, yaml"
    imports += ", openpyxl" if "openpyxl" in dependency_text else ""
    imports += ", pandas" if "pandas" in dependency_text else ""
    subprocess.run([str(python), "-B", "-c", "import " + imports + "; print('runtime imports verified')"], check=True)
    print("runtime installed and read back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
