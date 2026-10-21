import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"


def load_script(module_name: str, filename: str):
    path = SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


runtime = load_script("lx_haibao_check_runtime_test", "check_runtime.py")
poster = load_script("lx_haibao_run_poster_batch_test", "run_poster_batch.py")
common = load_script("lx_haibao_common_test", "common.py")


class BrandRegistryTest(unittest.TestCase):
    def test_every_brand_config_is_registered_and_loadable(self):
        brands_dir = SKILL_ROOT / "brands"
        expected = {
            path.stem
            for path in brands_dir.glob("*.yaml")
            if path.name != "brands.yaml"
        }
        loaded = {brand["brand_id"] for brand in common.load_brands(SKILL_ROOT)}
        self.assertEqual(expected, loaded)


class RuntimeInstallGateTest(unittest.TestCase):
    def test_install_without_confirmation_runs_no_subprocess_or_install_step(self):
        with patch.object(sys, "argv", ["check_runtime.py", "--install"]), patch.object(
            runtime, "run_command"
        ) as run_command, patch.object(runtime, "create_venv") as create_venv, patch.object(
            runtime, "install_requirements"
        ) as install_requirements:
            exit_code = runtime.main()

        self.assertEqual(exit_code, 2)
        run_command.assert_not_called()
        create_venv.assert_not_called()
        install_requirements.assert_not_called()

    def test_confirmed_install_keeps_existing_install_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = Path(tmp) / ".venv"
            runtime_python = venv_dir / "bin" / "python"
            runtime_python.parent.mkdir(parents=True)
            runtime_python.touch()
            with patch.object(
                sys,
                "argv",
                ["check_runtime.py", "--install", "--confirmed", "--venv", str(venv_dir)],
            ), patch.object(runtime, "ensure_bootstrap_python") as ensure_bootstrap, patch.object(
                runtime, "create_venv"
            ) as create_venv, patch.object(runtime, "install_requirements") as install_requirements, patch.object(
                runtime, "check_imports", return_value=True
            ), patch.object(runtime, "check_skill", return_value=True):
                exit_code = runtime.main()

        self.assertEqual(exit_code, 0)
        ensure_bootstrap.assert_called_once()
        create_venv.assert_called_once()
        install_requirements.assert_called_once_with(runtime_python.resolve(), upgrade_pip=False)


class PosterSafetyGateTest(unittest.TestCase):
    def assert_no_persistent_setup(self, log_dir, file_handler, output_dirs):
        log_dir.assert_not_called()
        file_handler.assert_not_called()
        output_dirs.assert_not_called()

    def test_local_check_creates_no_log_or_output_directory(self):
        with patch.object(sys, "argv", ["run_poster_batch.py", "--check"]), patch.object(
            poster, "run_check", return_value={"ok": True}
        ), patch.object(poster, "print_check"), patch.object(poster, "log_dir") as log_dir, patch.object(
            poster.logging, "FileHandler"
        ) as file_handler, patch.object(poster, "output_dirs") as output_dirs:
            exit_code = poster.main()

        self.assertEqual(exit_code, 0)
        self.assert_no_persistent_setup(log_dir, file_handler, output_dirs)

    def test_brand_lock_check_creates_no_log_or_output_directory(self):
        with patch.object(sys, "argv", ["run_poster_batch.py", "--check-brand-locks"]), patch.object(
            poster, "run_brand_locks_check", return_value={"ok": True}
        ), patch.object(poster, "print_brand_locks_check"), patch.object(poster, "log_dir") as log_dir, patch.object(
            poster.logging, "FileHandler"
        ) as file_handler, patch.object(poster, "output_dirs") as output_dirs:
            exit_code = poster.main()

        self.assertEqual(exit_code, 0)
        self.assert_no_persistent_setup(log_dir, file_handler, output_dirs)

    def test_dry_run_creates_no_log_or_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = Path(tmp) / "安安用车-上海市.txt"
            txt_path.write_text("【免佣】\n上海市活动", encoding="utf-8")
            brand = {"brand_id": "anan", "canonical_name": "安安用车"}
            template = {"template_id": "template01", "display_name": "模板一"}
            row = {
                "status": "supported",
                "brand_id": "anan",
                "brand": "安安用车",
                "path": str(txt_path),
                "city": "上海市",
            }
            with patch.object(
                sys, "argv", ["run_poster_batch.py", "--dry-run", "--file", str(txt_path)]
            ), patch.object(poster, "collect_input_paths", return_value=[txt_path]), patch.object(
                poster, "load_brands", return_value=[brand]
            ), patch.object(poster, "select_template", return_value=template), patch.object(
                poster, "build_rows", return_value=[row]
            ), patch.object(poster, "select_template_for_brand", return_value=template), patch.object(
                poster, "attach_confirmation_materials"
            ), patch.object(poster, "print_dry_run"), patch.object(poster, "log_dir") as log_dir, patch.object(
                poster.logging, "FileHandler"
            ) as file_handler, patch.object(poster, "output_dirs") as output_dirs:
                exit_code = poster.main()

        self.assertEqual(exit_code, 0)
        self.assert_no_persistent_setup(log_dir, file_handler, output_dirs)

    def test_unconfirmed_generation_rejects_before_path_or_log_setup(self):
        with patch.object(sys, "argv", ["run_poster_batch.py", "--file", "activity.txt"]), patch.object(
            poster, "collect_input_paths"
        ) as collect_input_paths, patch.object(poster, "log_dir") as log_dir, patch.object(
            poster.logging, "FileHandler"
        ) as file_handler, patch.object(poster, "output_dirs") as output_dirs:
            exit_code = poster.main()

        self.assertEqual(exit_code, 2)
        collect_input_paths.assert_not_called()
        self.assert_no_persistent_setup(log_dir, file_handler, output_dirs)

    def test_provider_check_requires_distinct_network_confirmation(self):
        check_providers = MagicMock(return_value={"ok": True})
        fake_client = types.SimpleNamespace(check_providers=check_providers)
        for argv in (
            ["run_poster_batch.py", "--check-providers"],
            ["run_poster_batch.py", "--check-providers", "--confirmed"],
        ):
            with self.subTest(argv=argv), patch.object(sys, "argv", argv), patch.dict(
                sys.modules, {"image2_client": fake_client}
            ), patch.object(poster, "log_dir") as log_dir, patch.object(
                poster.logging, "FileHandler"
            ) as file_handler, patch.object(poster, "output_dirs") as output_dirs:
                exit_code = poster.main()

            self.assertEqual(exit_code, 2)
            self.assert_no_persistent_setup(log_dir, file_handler, output_dirs)
        check_providers.assert_not_called()

    def test_confirmed_network_provider_check_calls_network_without_persistence(self):
        check_providers = MagicMock(return_value={"ok": True})
        fake_client = types.SimpleNamespace(check_providers=check_providers)
        with patch.object(
            sys,
            "argv",
            ["run_poster_batch.py", "--check-providers", "--confirmed-network"],
        ), patch.dict(sys.modules, {"image2_client": fake_client}), patch.object(
            poster, "print_provider_check"
        ), patch.object(poster, "log_dir") as log_dir, patch.object(
            poster.logging, "FileHandler"
        ) as file_handler, patch.object(poster, "output_dirs") as output_dirs:
            exit_code = poster.main()

        self.assertEqual(exit_code, 0)
        check_providers.assert_called_once_with()
        self.assert_no_persistent_setup(log_dir, file_handler, output_dirs)

    def test_confirmed_generation_keeps_persistent_setup(self):
        require_api_key = MagicMock()
        fake_client = types.SimpleNamespace(require_api_key=require_api_key)
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = Path(tmp) / "unknown-brand.txt"
            txt_path.write_text("上海市活动", encoding="utf-8")
            template = {"template_id": "template01", "display_name": "模板一"}
            unsupported = {
                "status": "unsupported",
                "path": str(txt_path),
                "reason": "file name did not match any configured brand",
            }
            with patch.object(
                sys,
                "argv",
                ["run_poster_batch.py", "--confirmed", "--file", str(txt_path)],
            ), patch.dict(sys.modules, {"image2_client": fake_client}), patch.object(
                poster, "collect_input_paths", return_value=[txt_path]
            ), patch.object(poster, "load_brands", return_value=[]), patch.object(
                poster, "select_template", return_value=template
            ), patch.object(poster, "build_rows", return_value=[unsupported]), patch.object(
                poster, "import_errors", return_value=[]
            ), patch.object(poster, "configure_logging") as configure_logging, patch.object(
                poster,
                "output_dirs",
                return_value=(Path(tmp) / "out", Path(tmp) / "meta", Path(tmp) / "tmp"),
            ) as output_dirs:
                exit_code = poster.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            configure_logging.call_args_list,
            [call(persistent=False), call(persistent=True)],
        )
        require_api_key.assert_called_once_with()
        output_dirs.assert_called_once()


if __name__ == "__main__":
    unittest.main()
