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
    if not argv or argv[0] not in {"init", "update", "database", "anomaly", "poster", "wenda", "wenda-selftest", "kaquan", "diaozhang", "zuotu"}:
        print("Usage: python tools/run_skill.py init|update|database|anomaly|poster|wenda|kaquan|diaozhang|zuotu [arguments]")
        return 1
    skill = argv.pop(0)
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
        node = shutil.which("node")
        if not node:
            print("缺少 Node.js，请联系我方安装现有受支持运行环境")
            return 1
        env["MAXKB_CONFIG"] = str(ROOT / "config/lx-wenda.json")
        command = [node, str(SKILLS / "lx-wenda" / ("selftest.js" if skill == "wenda-selftest" else "ask.js"))]
    else:
        relative = {"init": "lx-init/scripts/init_workspace.py", "update": "lx-update/scripts/update_workspace.py", "database": "lx_shujuku/scripts/partner_query.py", "anomaly": "lx-yidongfenxi/scripts/run_partner_analysis.py", "poster": "lx-haibao/scripts/run_poster_batch.py", "kaquan": "lx-kaquan/scripts/generate_cards.py", "diaozhang": "lx-diaozhang/scripts/generate_adjustment.py", "zuotu": "lx-zuotu/scripts/run_zuotu_batch.py"}[skill]
        venv = SKILLS / "lx-haibao/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python = str(venv) if venv.is_file() else sys.executable
        command = [python, "-B", str(SKILLS / relative)]
    return subprocess.run([*command, *argv], cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
