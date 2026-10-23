"""Bootstrap runs the real staged updater against a local Git remote."""
import contextlib
import hashlib
import io
import json
import os
import shlex
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_update_fog as base
from tools import bootstrap_update as bootstrap


class BootstrapUpdateTests(unittest.TestCase):
    def setUp(self):
        self.f = base.FogUpdateSafetyTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.source = Path(__file__).resolve().parents[4]
        self.official = "git@github.com:pxiaolei/FOG.git"
        # Transport adapter only: official URL validation stays real, fetches use local upload-pack.
        ssh = self.f.root / "local_ssh.py"
        real_git = shutil.which("git")
        ssh.write_text("import os\nos.execv(" + repr(real_git) + ", " +
                       repr([real_git, "upload-pack", str(self.f.remote)]) + ")\n")
        self.environment = {
            "GIT_SSH_COMMAND": shlex.join([sys.executable, str(ssh)]),
            "GIT_SSH_VARIANT": "ssh", "PYTHONDONTWRITEBYTECODE": "1"}
        profile = self.f.publisher / "config/distribution.json"
        profile.parent.mkdir()
        profile.write_text(json.dumps({"repository": "pxiaolei/FOG"}))
        self.f.commit_and_push("old identity")
        base.git(self.f.colleague, "pull", "--ff-only")
        base.git(self.f.colleague, "remote", "set-url", "origin", self.official)
        files = {}
        for rel in bootstrap.TOOL_FILES:
            data = (self.source / rel).read_bytes()
            dest = self.f.publisher / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            files[rel] = hashlib.sha256(data).hexdigest()
        (self.f.publisher / "config/source-lock.json").write_text(json.dumps({"files": files}))
        (self.f.publisher / "config/workspace-release.json").write_text(json.dumps({
            "schema_version": 1, "version": "2026.10.23.1", "repository": "pxiaolei/FOG",
            "minimum_updater_protocol": 1, "migrations": []}))
        self.commit = self.f.commit_and_push("new updater release")

    def run_bootstrap(self, action="check", commit=None, confirmation=None):
        args = ["--repo", str(self.f.colleague), "--commit", commit or self.commit, action]
        if confirmation is not None:
            args += ["--confirmed", confirmation]
        with patch.dict(os.environ, self.environment), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return bootstrap.main(args)

    def test_check_uses_new_tool_without_changing_old_workspace(self):
        before = self.f.repository_snapshot()
        self.assertEqual(self.run_bootstrap(), 0)
        self.assertEqual(self.f.repository_snapshot(), before)

    def test_wrong_or_missing_confirmation_does_not_change_workspace(self):
        before = self.f.repository_snapshot()
        self.assertNotEqual(self.run_bootstrap("apply", confirmation="0" * 40), 0)
        self.assertNotEqual(self.run_bootstrap("apply"), 0)
        self.assertEqual(self.f.repository_snapshot(), before)

    def test_unavailable_fixed_commit_does_not_change_workspace(self):
        before = self.f.repository_snapshot()
        self.assertNotEqual(self.run_bootstrap(commit="0" * 40), 0)
        self.assertEqual(self.f.repository_snapshot(), before)

    def test_tool_hash_mismatch_is_rejected_before_execution(self):
        lock = self.f.publisher / "config/source-lock.json"
        value = json.loads(lock.read_text())
        value["files"][bootstrap.TOOL_FILES[0]] = "0" * 64
        lock.write_text(json.dumps(value))
        self.commit = self.f.commit_and_push("bad tool hash")
        before = self.f.repository_snapshot()
        self.assertNotEqual(self.run_bootstrap(), 0)
        self.assertEqual(self.f.repository_snapshot(), before)

    def test_confirmed_bootstrap_upgrades_and_preserves_ignored_files(self):
        config = self.f.colleague / "config/fog_config.yaml"
        config.write_bytes(b"fictional local account\r\n")
        business = self.f.colleague / "workspace/keep.txt"
        business.write_bytes(b"local document")
        self.assertEqual(self.run_bootstrap("apply", confirmation=self.commit), 0)
        self.assertEqual(base.git(self.f.colleague, "rev-parse", "HEAD").stdout.strip(), self.commit)
        self.assertEqual(config.read_bytes(), b"fictional local account\r\n")
        self.assertEqual(business.read_bytes(), b"local document")

    def test_delegate_success_without_upgrading_is_rejected_by_readback(self):
        script = self.f.publisher / bootstrap.TOOL_FILES[0]
        script.write_bytes(b"raise SystemExit(0)\n")
        lock = self.f.publisher / "config/source-lock.json"
        value = json.loads(lock.read_text())
        value["files"][bootstrap.TOOL_FILES[0]] = hashlib.sha256(script.read_bytes()).hexdigest()
        lock.write_text(json.dumps(value))
        self.commit = self.f.commit_and_push("broken delegate reports success")
        before = self.f.repository_snapshot()
        self.assertNotEqual(self.run_bootstrap("apply", confirmation=self.commit), 0)
        self.assertEqual(self.f.repository_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
