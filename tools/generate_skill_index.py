#!/usr/bin/env python3
"""Compatibility entry; implementation: tools/project/generate_skill_index.py."""
from importlib import util
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_NAME = "_p_fog_generate_skill_index"
_module = sys.modules.get(_NAME)
if _module is None:
    _spec = util.spec_from_file_location(_NAME, _ROOT / 'tools/project/generate_skill_index.py')
    _module = util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)
if __name__ == "__main__":
    raise SystemExit(_module.main())
else:
    sys.modules[__name__] = _module
