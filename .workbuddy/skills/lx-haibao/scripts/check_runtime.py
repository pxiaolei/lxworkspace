#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
DEFAULT_VENV_DIR = SKILL_ROOT / ".venv"
REQUIREMENTS_PATH = SKILL_ROOT / "assets" / "runtime" / "requirements.txt"
RUN_POSTER_BATCH = SCRIPT_DIR / "run_poster_batch.py"
REQUIRED_IMPORTS = {
    "requests": "requests",
    "Pillow": "PIL",
    "zxing-cpp": "zxingcpp",
    "PyYAML": "yaml",
}
MIN_PYTHON = (3, 10)


def command_text(cmd: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(cmd))
    return shlex.join(str(item) for item in cmd)


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def run_command(cmd: Sequence[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"$ {command_text(cmd)}")
    result = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONNOUSERSITE": "1"},
        check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败，exit_code={result.returncode}: {command_text(cmd)}")
    return result


def ensure_bootstrap_python(python: Path) -> None:
    result = run_command([str(python), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"], check=True)
    version_text = (result.stdout or "").strip().splitlines()[-1]
    major, minor, *_ = (int(part) for part in version_text.split("."))
    if (major, minor) < MIN_PYTHON:
        minimum = ".".join(str(part) for part in MIN_PYTHON)
        raise RuntimeError(f"Python 版本过低：{version_text}，需要 >= {minimum}")


def create_venv(venv_dir: Path, python: Path) -> None:
    if venv_python(venv_dir).is_file():
        print(f"OK: 已存在 venv: {venv_dir}")
        return
    print(f"创建 lx-haibao 本地 venv: {venv_dir}")
    run_command([str(python), "-m", "venv", str(venv_dir)], check=True)


def install_requirements(python: Path, *, upgrade_pip: bool) -> None:
    if upgrade_pip:
        run_command([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    run_command([str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)], check=True)


def check_imports(python: Path) -> bool:
    code = "\n".join(
        [
            "import importlib",
            f"mods = {REQUIRED_IMPORTS!r}",
            "failed = []",
            "for package, module in mods.items():",
            "    try:",
            "        importlib.import_module(module)",
            "        print(f'{package}: OK')",
            "    except Exception as exc:",
            "        failed.append(f'{package}: {exc}')",
            "        print(f'{package}: FAIL ({exc})')",
            "raise SystemExit(1 if failed else 0)",
        ]
    )
    result = run_command([str(python), "-c", code], check=False)
    return result.returncode == 0


def check_skill(python: Path) -> bool:
    result = run_command([str(python), str(RUN_POSTER_BATCH), "--check"], cwd=SKILL_ROOT, check=False)
    return result.returncode == 0


def check_runtime_launcher() -> str:
    return r".\check_runtime.cmd" if os.name == "nt" else "./check_runtime.sh"


def haibao_launcher() -> str:
    return r".\haibao.cmd" if os.name == "nt" else "./haibao.sh"


def print_next_commands() -> None:
    runner = haibao_launcher()
    print()
    print("后续请在 lx-haibao 目录使用平台入口脚本运行，不要给全局 Python 安装依赖：")
    print(f"- check: {runner} --check")
    print(f"- dry-run: {runner} --dry-run --file <活动TXT路径>")
    print(f"- confirmed: {runner} --confirmed --file <活动TXT路径>")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or validate the lx-haibao skill-local Python venv. "
            "Dependencies are installed into .venv, not into global Python."
        )
    )
    parser.add_argument("--install", action="store_true", help="Create .venv if missing and install requirements into it.")
    parser.add_argument("--confirmed", action="store_true", help="Confirm the R4 local environment change requested by --install.")
    parser.add_argument("--venv", default=str(DEFAULT_VENV_DIR), help="Skill-local venv directory. Default: lx-haibao/.venv")
    parser.add_argument("--python", default=sys.executable, help="Bootstrap Python used only to create the venv when --install is set.")
    parser.add_argument("--upgrade-pip", action="store_true", help="Upgrade pip inside the venv before installing requirements.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.install and not args.confirmed:
        print("FAIL: --install 会创建本地 venv 并运行 pip；请追加 --confirmed。")
        return 2

    venv_dir = Path(args.venv).expanduser().resolve()
    bootstrap_python = Path(args.python).expanduser().resolve()
    runtime_python = venv_python(venv_dir)

    print("lx-haibao runtime check")
    print(f"platform={platform.platform()}")
    print(f"skill_root={SKILL_ROOT}")
    print(f"venv={venv_dir}")
    print(f"bootstrap_python={bootstrap_python}")
    print()

    try:
        if args.install:
            ensure_bootstrap_python(bootstrap_python)
            create_venv(venv_dir, bootstrap_python)
            install_requirements(runtime_python, upgrade_pip=args.upgrade_pip)
        elif not runtime_python.is_file():
            print(f"FAIL: 未找到 venv Python: {runtime_python}")
            print("请先运行：")
            print(f"  {check_runtime_launcher()} --install --confirmed")
            return 1

        if not runtime_python.is_file():
            print(f"FAIL: venv Python 不存在: {runtime_python}")
            return 1

        imports_ok = check_imports(runtime_python)
        skill_ok = check_skill(runtime_python)
        if not imports_ok or not skill_ok:
            print()
            print("FAIL: lx-haibao 运行时检查未通过。")
            print("依赖只应安装到 skill 本地 .venv；不要使用 sudo pip 或全局 pip 修复。")
            return 1

        print()
        print("OK: lx-haibao 本地运行时可用。")
        print_next_commands()
        return 0
    except RuntimeError as exc:
        print()
        print(f"FAIL: {exc}")
        print("依赖只应安装到 skill 本地 .venv；不要使用 sudo pip 或全局 pip 修复。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
