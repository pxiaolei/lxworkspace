import importlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
FIXTURE = ROOT / 'tests/fixtures/mayday_activity.yaml'


def require(test, name):
    test.assertTrue((ROOT / 'scripts' / f'{name}.py').is_file(), f'{name} is not implemented')
    return importlib.import_module(name)
