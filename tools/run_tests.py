#!/usr/bin/env python3
"""Run each test closure in its own process, with no real service credentials."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for name in ("MAXKB_TOKEN", "MAXKB_BASE", "MAXKB_CONFIG", "FOG_CONFIG_PATH", "FOG_PERSONAL_CONFIG_PATH"):
        env.pop(name, None)
    commands = [[sys.executable, "-B", "-m", "unittest", "discover", "-s", ".workbuddy/skills/lx-yidongfenxi/tests"],
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", ".workbuddy/skills/lx-haibao/tests"],
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", ".workbuddy/skills/lx-zuotu/tests"],
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", ".workbuddy/skills/lx-kaquan/tests"],
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", ".workbuddy/skills/lx-diaozhang/tests"],
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/tests"],
                ["node", "--test", ".workbuddy/skills/lx-wenda/test_safety.js"]]
    return max(subprocess.run(command, cwd=ROOT, env=env).returncode for command in commands)


if __name__ == "__main__":
    raise SystemExit(main())
