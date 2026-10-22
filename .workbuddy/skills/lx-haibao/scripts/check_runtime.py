#!/usr/bin/env python3
"""Check/install the common workspace runtime, never a second image environment."""
import importlib.util
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]
def load_initializer():
    file = ROOT / ".workbuddy/skills/lx-init/scripts/init_workspace.py"
    spec = importlib.util.spec_from_file_location("image_workspace_init", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--install" in args and "--confirmed" not in args:
        print("FAIL: 安装需confirmed及当前preview_hash；未安装。")
        return 2
    return load_initializer().main(args)
if __name__ == "__main__":
    raise SystemExit(main())
