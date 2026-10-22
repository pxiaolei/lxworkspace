import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "config_wizard.py"
spec = importlib.util.spec_from_file_location("config_wizard", SCRIPT)
wizard = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = wizard
spec.loader.exec_module(wizard)


class InitExecutionGateTests(unittest.TestCase):
    def test_default_init_workspace_is_preview_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {"lx_zhutichaibiao": {"work_dir": "workspace/custom"}}
            with patch.object(wizard, "find_project_root", return_value=root), patch.object(
                wizard, "load_yaml", return_value=config
            ):
                result = wizard.main(["init-workspace"])

            self.assertEqual(result, 0)
            self.assertFalse((root / "workspace").exists())

    def test_default_apply_does_not_write_report_or_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / ".lx-init-report.md"
            report.write_text("keep", encoding="utf-8")
            config = {"init": {"report_path": ".lx-init-report.md"}}
            with patch.object(wizard, "find_project_root", return_value=root), patch.object(
                wizard, "load_yaml", return_value=config
            ), patch.object(wizard, "run_check", return_value=0):
                result = wizard.main(["apply"])

            self.assertEqual(result, 0)
            self.assertEqual(report.read_text(encoding="utf-8"), "keep")
            self.assertFalse((root / "workspace").exists())

    def test_dry_run_and_confirmed_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            wizard.main(["init-workspace", "--dry-run", "--confirmed"])

    def test_confirmed_apply_refuses_existing_report_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / ".lx-init-report.md"
            report.write_text("keep", encoding="utf-8")
            config = {"init": {"report_path": ".lx-init-report.md"}}
            with patch.object(wizard, "find_project_root", return_value=root), patch.object(
                wizard, "load_yaml", return_value=config
            ), patch.object(wizard, "run_check", return_value=0), patch.object(
                wizard, "init_workspace"
            ) as init_workspace:
                result = wizard.main(["apply", "--confirmed"])

            self.assertEqual(result, 2)
            self.assertEqual(report.read_text(encoding="utf-8"), "keep")
            init_workspace.assert_not_called()

    def test_confirmed_apply_refuses_report_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "outside.md"
            config = {"init": {"report_path": str(outside)}}
            with patch.object(wizard, "find_project_root", return_value=root), patch.object(
                wizard, "load_yaml", return_value=config
            ), patch.object(wizard, "run_check", return_value=0), patch.object(
                wizard, "init_workspace"
            ) as init_workspace:
                result = wizard.main(["apply", "--confirmed", "--overwrite"])

            self.assertEqual(result, 2)
            self.assertFalse(outside.exists())
            init_workspace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
