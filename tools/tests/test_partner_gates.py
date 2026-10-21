import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    file = ROOT / relative
    sys.path.insert(0, str(file.parent))
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


query = load("partner_query_test", ".workbuddy/skills/lx_shujuku/scripts/partner_query.py")
analysis = load("partner_analysis_test", ".workbuddy/skills/lx-yidongfenxi/scripts/run_partner_analysis.py")
init = load("partner_init_test", ".workbuddy/skills/lx-init/scripts/init_workspace.py")
update = load("partner_update_test", ".workbuddy/skills/lx-update/scripts/update_workspace.py")
dispatcher = load("partner_dispatcher_test", "tools/run_skill.py")


class PartnerGatesTest(unittest.TestCase):
    def test_dispatcher_does_not_inherit_other_workspace_credentials(self):
        from unittest.mock import MagicMock
        process = MagicMock(returncode=0)
        with patch.dict(dispatcher.os.environ, {"MAXKB_TOKEN": "fixture-other-account", "MAXKB_CONFIG": "other-project.json", "KIE_API_KEY": "fixture-other-key", "FOG_CONFIG_PATH": "other-project.yaml"}), patch.object(dispatcher.shutil, "which", return_value="node"), patch.object(dispatcher.subprocess, "run", return_value=process) as run:
            self.assertEqual(dispatcher.main(["wenda", "测试问题"]), 0)
        env = run.call_args.kwargs["env"]
        self.assertNotIn("MAXKB_TOKEN", env)
        self.assertNotIn("KIE_API_KEY", env)
        self.assertEqual(env["MAXKB_CONFIG"], str(ROOT / "config/lx-wenda.json"))
        self.assertEqual(env["FOG_CONFIG_PATH"], str(ROOT / "config/fog_config.yaml"))

    def test_query_preview_and_stale_confirmation_do_not_create_client_or_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(query, "DataReportingClient", side_effect=AssertionError("client called")), contextlib.redirect_stdout(io.StringIO()):
            out = Path(tmp) / "new/result.json"
            args = ["--sql", "SELECT id FROM honghu_detail_data WHERE date_day='2026-10-07' ORDER BY id", "--question", "测试", "--output", str(out)]
            self.assertEqual(query.main(args), 0)
            with self.assertRaises(SystemExit):
                query.main([*args, "--confirmed", "--expected-preview-hash", "stale"])
            self.assertFalse(out.parent.exists())

    def test_missing_periods_and_preview_never_reach_company_client(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(query, "DataReportingClient", side_effect=AssertionError("client called")), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                analysis.main([])
            out = Path(tmp) / "new"
            args = ["--period-type", "daily", "--start", "2026-10-07", "--end", "2026-10-07", "--compare-start", "2026-09-30", "--compare-end", "2026-09-30", "--output-dir", str(out)]
            self.assertEqual(analysis.main(args), 0)
            with self.assertRaises(SystemExit):
                analysis.main([*args, "--confirmed", "--expected-preview-hash", "stale"])
            self.assertFalse(out.exists())

    def test_init_unconfirmed_or_stale_runs_no_installer(self):
        with patch.object(init.subprocess, "run", side_effect=AssertionError("installer called")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(init.main(["--install"]), 0)
            with self.assertRaises(SystemExit):
                init.main(["--install", "--confirmed", "--expected-preview-hash", "stale"])

    def test_update_protects_partner_local_data_and_rejects_deletions(self):
        for path in ("config/lx-wenda.json", "config/fog_config.yaml", ".workbuddy/skills/lx-haibao/brands/foo.yaml", ".workbuddy/skills/lx-haibao/brand-assets/foo/logo.png", "workspace/04数据分析/result.json", ".workbuddy/skills/lx-haibao/.venv/Scripts/python.exe"):
            self.assertTrue(update.is_protected_path(path), path)
        self.assertFalse(update.is_protected_path(".workbuddy/skills/lx-haibao/brands/brand.yaml.example"))
        self.assertTrue(update.is_protected_path(".workbuddy/skills/lx-haibao/brand-assets/custom/README.md"))
        self.assertFalse(update.is_protected_path(".workbuddy/skills/lx-haibao/brand-assets/README.md"))
        self.assertFalse(update.is_allowed_remote_deletion(".workbuddy/skills/lxx_share/database.py"))
        with self.assertRaises(RuntimeError):
            update.validate_official_origin("https://github.com/pxiaolei/FOG")

    def test_stale_update_confirmation_does_not_fetch_or_merge_target(self):
        plan = update.UpdatePlan("a" * 40, "b" * 40, "fast-forward-ready", (), ())
        with patch.object(update, "build_plan", return_value=plan), patch.object(update, "git_output", side_effect=AssertionError("target modified")), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                update.apply_update(ROOT, "c" * 40)

    def test_configuration_library_has_no_private_lazy_exports(self):
        sys.path.insert(0, str(ROOT / ".workbuddy/skills"))
        import lxx_share
        self.assertFalse(hasattr(lxx_share, "__getattr__"))
        self.assertFalse(hasattr(lxx_share, "database"))
        self.assertFalse(hasattr(lxx_share, "excel_utils"))

    def test_confirmed_query_writes_and_reads_back_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "query/result.json"
            args = ["--sql", "SELECT id FROM honghu_detail_data WHERE date_day='2026-10-07' ORDER BY id", "--question", "测试问题", "--output", str(output)]
            preview = io.StringIO()
            with contextlib.redirect_stdout(preview):
                query.main(args)
            expected = json.loads(preview.getvalue())["preview_hash"]
            from unittest.mock import MagicMock
            client = MagicMock()
            evidence = {"is_complete": True, "rows": [{"id": 1, "品牌": "测试品牌"}], "row_count": 1}
            client.execute_full_audited.return_value = evidence
            with patch.object(query, "DataReportingClient", return_value=client), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(query.main([*args, "--confirmed", "--expected-preview-hash", expected]), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), evidence)

    def test_confirmed_analysis_runs_company_reader_and_fixed_profile(self):
        from unittest.mock import MagicMock
        from company_data_reader import SOURCE_FIELDS
        import lx_shujuku
        import lxx_share.fog_config
        def package(day, completed):
            row = {**{field: 10 for field in SOURCE_FIELDS}, "id": 1, "date_day": day, "brand_name": "测试品牌", "city_name": "测试市", "completed_order_count": completed}
            return {"is_complete": True, "possible_truncation": False, "rows": [row], "row_count": 1, "total_rows": 1, "verification": {"passed": True, "count_before": 1, "count_after": 1}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(lxx_share.fog_config, "get_section", return_value={}):
            output = Path(tmp) / "new-analysis"
            args = ["--period-type", "daily", "--start", "2026-10-07", "--end", "2026-10-07", "--compare-start", "2026-09-30", "--compare-end", "2026-09-30", "--output-dir", str(output)]
            preview = io.StringIO()
            with contextlib.redirect_stdout(preview):
                analysis.main(args)
            expected = json.loads(preview.getvalue())["preview_hash"]
            client = MagicMock()
            client.execute_full_audited.side_effect = [package("2026-10-07", 120), package("2026-09-30", 100)]
            with patch.object(lx_shujuku, "DataReportingClient", return_value=client), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(analysis.main([*args, "--confirmed", "--expected-preview-hash", expected]), 0)
            result = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(result["meta"]["profile"], "partner_basic")
            self.assertEqual(len(result["results"]), 1)
            self.assertEqual(client.execute_full_audited.call_count, 2)
            self.assertTrue((output / "analysis.md").is_file())


if __name__ == "__main__":
    unittest.main()
