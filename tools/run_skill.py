#!/usr/bin/env python3
"""Project-local skill dispatcher, including the process-local MaxKB config path."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".workbuddy/skills"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "diaozhang":
        argv[0] = "tiaozhang"  # Compatibility with previously documented commands.
    scripts = {"init": "lx-init/scripts/init_workspace.py", "init-runtime": "lx-init/scripts/bootstrap_runtime.py", "init-config": "lx-init/scripts/write_local_config.py", "update": "lx-update/scripts/update_fog.py", "database": "lx_shujuku/scripts/partner_query.py", "anomaly": "lx-yidong/scripts/run_partner_analysis.py", "poster": "lx-haibao/scripts/run_poster_batch.py", "kaquan": "lx-kaquan/scripts/generate_cards.py", "tiaozhang": "lx-tiaozhang/scripts/generate_adjustment.py", "zuotu": "lx-zuotu/scripts/run_zuotu_batch.py"}
    if not argv or argv[0] not in set(scripts) | {"wenda", "wenda-selftest"}:
        print("Usage: python tools/run_skill.py init|init-runtime|init-config|update|database|anomaly|poster|wenda|kaquan|tiaozhang|zuotu [arguments]")
        return 1
    skill = argv.pop(0)
    entry = SKILLS / ("lx-wenda/ask.js" if skill.startswith("wenda") else scripts[skill])
    if not entry.is_file():
        print("当前工作区未提供这项能力，请使用已安装的 Skill。")
        return 1
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # A partner workspace must never inherit another project's service account.
    for name in list(env):
        if name.startswith(("MAXKB_", "KIE_", "APIMART_", "IMAGE_PROVIDER_", "POSTER_OUTPUT_", "POSTER_META_", "POSTER_TMP_", "POSTER_LOG_")) or name in {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            env.pop(name, None)
    env["FOG_CONFIG_PATH"] = str(ROOT / "config/fog_config.yaml")
    env["FOG_PERSONAL_CONFIG_PATH"] = str(ROOT / "config/personal_config.yaml")
    if skill.startswith("wenda"):
        if any(arg == "--config" or arg.startswith("--config=") for arg in argv):
            print("项目问答入口只使用本工作区配置；维护配置请使用直接 CLI/MCP 入口")
            return 1
        node = shutil.which("node")
        if not node:
            print("缺少 Node.js。请运行 python tools/run_skill.py init-runtime 补齐后重试")
            return 1
        env["MAXKB_WORKSPACE_ROOT"] = str(ROOT)
        command = [node, str(SKILLS / "lx-wenda" / ("selftest.js" if skill == "wenda-selftest" else "ask.js"))]
    else:
        venv = SKILLS / "lx-haibao/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python = sys.executable if skill in {"init-runtime", "init-config"} else str(venv) if venv.is_file() else sys.executable
        command = [python, "-B", str(SKILLS / scripts[skill])]
    return subprocess.run([*command, *argv], cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
