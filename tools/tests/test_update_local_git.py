import contextlib
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("partner_git_update", ROOT / ".workbuddy/skills/lx-update/scripts/update_fog.py")
update = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = update
spec.loader.exec_module(update)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


class LocalGitUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.publisher = self.root / "publisher"
        self.publisher.mkdir()
        git(self.publisher, "init", "-b", "main")
        (self.publisher / ".gitignore").write_text('\n'.join(sorted(update.REQUIRED_IGNORE_RULES)) + '\n')
        (self.publisher / "shared.txt").write_text("version 1")
        (self.publisher / "tools").mkdir()
        for filename in ("generate_skill_index.py", "check_distribution.py"):
            (self.publisher / "tools" / filename).write_text("raise SystemExit(0)\n")
        self.commit("initial")
        self.partner = self.root / "partner"
        git(self.root, "clone", str(self.publisher), str(self.partner))

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, message):
        git(self.publisher, "add", ".gitignore", "shared.txt", "tools")
        for folder in ("contracts", ".workbuddy"):
            if (self.publisher / folder).exists():
                git(self.publisher, "add", "--", folder)
        git(self.publisher, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
        return git(self.publisher, "rev-parse", "HEAD")

    def snapshot(self):
        return (git(self.partner, "rev-parse", "HEAD"), git(self.partner, "for-each-ref"), git(self.partner, "status", "--porcelain"), (self.partner / "shared.txt").read_bytes())

    def publish_brand(self, content=b"fixture-logo"):
        prefix = ".workbuddy/skills/lx-haibao/"
        files = {prefix + "brands/brands.yaml": b"brands: [halo]", prefix + "brands/halo.yaml": b"fixture-config", prefix + "brand-assets/halo/logo.png": content, prefix + "brand-assets/halo/qr.png": b"fixture-qr"}
        for relative, data in files.items():
            path = self.publisher / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = {"version": 1, "brands": ["halo"], "files": {p: hashlib.sha256(data).hexdigest() for p, data in files.items()}}
        path = self.publisher / "contracts/partner-brand-presets.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with (self.publisher / ".gitignore").open("a") as out:
            out.write("!.workbuddy/skills/lx-haibao/brand-assets/halo/\n")
            out.write(''.join("!" + p + "\n" for p in files))
        return self.commit("publish brand")

    def test_official_brand_add_and_update_preserve_custom_assets(self):
        custom = self.partner / ".workbuddy/skills/lx-haibao/brand-assets/custom/qr.png"
        custom.parent.mkdir(parents=True)
        custom.write_bytes(b"local-custom")
        for data in (b"logo-v1", b"logo-v2"):
            commit = self.publish_brand(data)
            before = self.snapshot()
            with patch.object(update, "validate_official_origin"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(update.main(["--repo", str(self.partner), "check"]), 0)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(update.main(["--repo", str(self.partner), "apply", "--confirmed", commit]), 0)
            self.assertEqual((self.partner / ".workbuddy/skills/lx-haibao/brand-assets/halo/logo.png").read_bytes(), data)
            self.assertEqual(custom.read_bytes(), b"local-custom")

    def test_new_official_brand_collision_stops_without_overwriting_ignored_file(self):
        local = self.partner / ".workbuddy/skills/lx-haibao/brand-assets/halo/logo.png"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"local-owned")
        self.publish_brand()
        before = self.snapshot()
        with patch.object(update, "validate_official_origin"):
            plan = update.build_plan(self.partner)
        self.assertTrue(any("冲突" in b for b in plan.blockers))
        self.assertEqual(local.read_bytes(), b"local-owned")
        self.assertEqual(self.snapshot(), before)

    def test_brand_hash_mismatch_or_invalid_manifest_stops_without_writes(self):
        self.publish_brand()
        manifest_path = self.publisher / "contracts/partner-brand-presets.json"
        original = json.loads(manifest_path.read_text())
        for mode in ("hash", "private-path"):
            value = json.loads(json.dumps(original))
            if mode == "hash":
                value["files"][".workbuddy/skills/lx-haibao/brand-assets/halo/logo.png"] = "0" * 64
            else:
                value["files"]["config/fog_config.yaml"] = "0" * 64
            manifest_path.write_text(json.dumps(value))
            self.commit(mode)
            before = self.snapshot()
            with patch.object(update, "validate_official_origin"):
                self.assertTrue(update.build_plan(self.partner).blockers)
            self.assertEqual(self.snapshot(), before)

    def test_dirty_official_brand_stops_before_remote_inspection(self):
        commit = self.publish_brand()
        with patch.object(update, "validate_official_origin"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update.main(["--repo", str(self.partner), "apply", "--confirmed", commit]), 0)
        path = self.partner / ".workbuddy/skills/lx-haibao/brand-assets/halo/logo.png"
        path.write_bytes(b"local-edit")
        with patch.object(update, "validate_official_origin"), patch.object(update, "inspect_remote", side_effect=AssertionError("network reached")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update.main(["--repo", str(self.partner), "check"]), 1)
        self.assertEqual(path.read_bytes(), b"local-edit")

    def test_check_leaves_target_unchanged_and_apply_preserves_all_local_data(self):
        (self.publisher / "shared.txt").write_text("version 2")
        commit = self.commit("update")
        local_files = ("config/fog_config.yaml", "config/lx-wenda.json", ".workbuddy/skills/lx-haibao/brands/foo.yaml", ".workbuddy/skills/lx-haibao/brand-assets/foo/qr.png", "workspace/04数据分析/result.json")
        for relative in local_files:
            path = self.partner / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture-local-value")
        before = self.snapshot()
        with patch.object(update, "validate_official_origin"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update.main(["--repo", str(self.partner), "check"]), 0)
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(update.main(["--repo", str(self.partner), "apply", "--confirmed", commit]), 0)
        self.assertEqual(git(self.partner, "rev-parse", "HEAD"), commit)
        for relative in local_files:
            self.assertEqual((self.partner / relative).read_bytes(), b"fixture-local-value")

    def test_dirty_target_stops_before_remote_inspection(self):
        (self.partner / "shared.txt").write_text("local edit")
        before = self.snapshot()
        with patch.object(update, "validate_official_origin"), patch.object(update, "inspect_remote", side_effect=AssertionError("network reached")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(update.main(["--repo", str(self.partner), "check"]), 1)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
