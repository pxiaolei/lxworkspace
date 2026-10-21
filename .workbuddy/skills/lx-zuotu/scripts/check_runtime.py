#!/usr/bin/env python3
"""Compatibility entry: delegate to partner lx-init without a second venv."""
import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--install" in args and "--confirmed" not in args:
        print("FAIL: 安装需confirmed及当前preview_hash；未安装。")
        return 2
    source = ROOT / ".workbuddy/skills/lx-init/scripts/init_workspace.py"
    spec = importlib.util.spec_from_file_location("partner_zuotu_init", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
