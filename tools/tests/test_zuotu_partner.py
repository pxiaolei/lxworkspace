"""Check the partner entry and shared runtime without credentials or API calls."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".workbuddy/skills/lx-zuotu"
FIXTURE = SKILL / "tests/fixtures/mayday_activity.yaml"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


init = load("zuotu_partner_init", ROOT / ".workbuddy/skills/lx-init/scripts/init_workspace.py")
runtime = load("zuotu_partner_runtime", SKILL / "scripts/check_runtime.py")
update = load("zuotu_partner_update", ROOT / ".workbuddy/skills/lx-update/scripts/update_workspace.py")


class ZuotuPartnerTests(unittest.TestCase):
    def snapshot(self):
        return {str(p.relative_to(ROOT)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in ROOT.rglob("*") if p.is_file()}

    def run_entry(self, args, wrapper=None):
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHON_BIN=sys.executable,
                   KIE_API_KEY="fixture-from-other-workspace")
        command = (["sh", str(SKILL / wrapper)] if wrapper else
                   [sys.executable, "-B", str(ROOT / "tools/run_skill.py"), "zuotu"])
        return subprocess.run([*command, *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8")

    def test_dry_run_scopes_use_the_bundle_without_creating_any_files(self):
        before = self.snapshot()
        for selection, count in [([], 8), (["--types", "popup"], 2), (["--sizes", "884x1178"], 1)]:
            with self.subTest(selection=selection):
                result = self.run_entry(["--dry-run", "--file", str(FIXTURE), *selection])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"计划 {count} 张", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_unconfirmed_sample_and_batch_do_not_create_outputs(self):
        before = self.snapshot()
        for selection in [[], ["--sample-only"]]:
            result = self.run_entry(["--file", str(FIXTURE), *selection])
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_check_does_not_inherit_other_workspace_key(self):
        before = self.snapshot()
        result = self.run_entry(["--check"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        status = json.loads(result.stdout)
        self.assertFalse(status["api_key_configured"])
        self.assertTrue(status["model_compatible"])
        self.assertEqual(status["reference_count"], 14)
        self.assertEqual(self.snapshot(), before)

    def test_shell_wrappers_forward_to_existing_project_entry(self):
        before = self.snapshot()
        result = self.run_entry(["--dry-run", "--file", str(FIXTURE), "--types", "popup"], "zuotu.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("计划 2 张", result.stdout)
        result = self.run_entry([], "check_runtime.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.split("\ncheck only;")[0])["venv"], str(init.VENV))
        self.assertEqual(self.snapshot(), before)

    def test_new_requirement_changes_invalidate_install_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            requirement = Path(tmp) / "zuotu-requirements.txt"
            requirement.write_bytes(init.ZUOTU_REQUIREMENTS.read_bytes())
            with patch.object(init, "ZUOTU_REQUIREMENTS", requirement), patch.object(init.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                expected = hashlib.sha256(json.dumps(init.build_plan(), sort_keys=True).encode()).hexdigest()
                requirement.write_text("requests\nPillow\nPyYAML\n# updated\n", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    init.main(["--install", "--confirmed", "--expected-preview-hash", expected])
                run.assert_not_called()

    def test_confirmed_install_uses_one_venv_and_all_requirement_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp).resolve() / "existing-project-venv"
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True)
            python.write_text("fixture; never executed", encoding="utf-8")
            with patch.object(init, "VENV", venv), patch.object(init.shutil, "which", return_value="fixture"), patch.object(init.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                expected = hashlib.sha256(json.dumps(init.build_plan(), sort_keys=True).encode()).hexdigest()
                self.assertEqual(init.main(["--install", "--confirmed", "--expected-preview-hash", expected]), 0)
            self.assertEqual(run.call_count, 2)
            install, readback = [call.args[0] for call in run.call_args_list]
            self.assertEqual(install[0], str(python))
            self.assertEqual(install.count("-r"), 3)
            for requirement in [init.REQUIREMENTS, init.CARD_REQUIREMENTS, init.ZUOTU_REQUIREMENTS]:
                self.assertIn(str(requirement), install)
            self.assertEqual(readback[0], str(python))
            self.assertIn("openpyxl", readback[-1])
            self.assertFalse((SKILL / ".venv").exists())

    def test_runtime_compatibility_entry_cannot_install_without_preview_hash(self):
        with patch.object(subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.main(["--install"]), 2)
            with self.assertRaises(SystemExit):
                runtime.main(["--install", "--confirmed"])
            run.assert_not_called()

    def test_only_fixed_workspace_readmes_are_updateable(self):
        for path in ["workspace/08端内宣传图/活动名/输入/input.txt", "workspace/08端内宣传图/活动名/manifest.json", "workspace/08端内宣传图/活动名/README.md", "workspace/08端内宣传图/活动名/产出图/a.png"]:
            self.assertTrue(update.is_protected_path(path), path)
        for path in ["workspace/README.md", "workspace/08端内宣传图/README.md", "workspace/08端内宣传图/.gitkeep"]:
            self.assertFalse(update.is_protected_path(path), path)


if __name__ == "__main__":
    unittest.main()
