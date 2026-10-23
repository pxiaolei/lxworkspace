"""Real Git upgrades: declarations may retire managed code, never local data."""
import contextlib
import hashlib
import io
import json
import unittest
from unittest.mock import patch

import test_update_fog as base

git = base.git
update_fog = base.update_fog


class ReleaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.f = base.FogUpdateSafetyTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.old_hash = hashlib.sha256(b"v1\n").hexdigest()
        config = self.f.publisher / "config"
        config.mkdir()
        self.lock({"shared.txt": self.old_hash})
        self.f.commit_and_push("legacy source lock")
        git(self.f.colleague, "pull", "--ff-only")

    def lock(self, files):
        (self.f.publisher / "config/source-lock.json").write_text(json.dumps({"files": files}))

    def release(self, retire=(), version="2026.10.23.1", protocol=1):
        value = {"schema_version": 1, "version": version,
                 "repository": update_fog.OFFICIAL_REPOSITORY,
                 "minimum_updater_protocol": protocol,
                 "migrations": [{"id": "fixture-rename", "retire": list(retire)}] if retire else []}
        (self.f.publisher / "config/workspace-release.json").write_text(json.dumps(value))

    def rename(self, old_hash=None, replacement="renamed.txt"):
        git(self.f.publisher, "mv", "shared.txt", "renamed.txt")
        self.lock({"renamed.txt": self.old_hash})
        self.release([{"path": "shared.txt", "old_sha256": [old_hash or self.old_hash],
                       "replacement": replacement}])
        return self.f.commit_and_push("declared rename")

    def plan(self):
        with self.f.official_origin_allowed():
            return update_fog.build_plan(self.f.colleague)

    def apply(self, commit):
        with self.f.official_origin_allowed(), contextlib.redirect_stdout(io.StringIO()):
            return update_fog.main(["--repo", str(self.f.colleague), "apply", "--confirmed", commit])

    def test_declared_rename_can_upgrade_legacy_and_preserves_local_data(self):
        commit = self.rename()
        config = self.f.colleague / "config/fog_config.yaml"
        config.write_bytes(b"fictional-account-only\r\n")
        business = self.f.colleague / "workspace/private.txt"
        business.write_bytes(b"private fixture")
        before = self.f.repository_snapshot()
        self.assertFalse(self.plan().blockers)
        self.assertEqual(self.f.repository_snapshot(), before)
        self.assertEqual(self.apply(commit), 0)
        self.assertFalse((self.f.colleague / "shared.txt").exists())
        self.assertEqual((self.f.colleague / "renamed.txt").read_bytes(), b"v1\n")
        self.assertEqual(config.read_bytes(), b"fictional-account-only\r\n")
        self.assertEqual(business.read_bytes(), b"private fixture")
        self.assertEqual(git(self.f.colleague, "rev-parse", "HEAD").stdout.strip(), commit)
        self.assertFalse(self.plan().blockers)

    def test_declared_retirement_without_replacement_can_be_confirmed(self):
        git(self.f.publisher, "rm", "shared.txt")
        self.lock({})
        self.release([{"path": "shared.txt", "old_sha256": [self.old_hash]}])
        commit = self.f.commit_and_push("declared retirement")
        self.assertFalse(self.plan().blockers)
        self.assertEqual(self.apply(commit), 0)

    def test_unknown_old_hash_blocks_without_mutating_local_repo(self):
        commit = self.rename(old_hash="0" * 64)
        before = self.f.repository_snapshot()
        self.assertTrue(self.plan().blockers)
        self.assertEqual(self.apply(commit), 1)
        self.assertEqual(self.f.repository_snapshot(), before)

    def test_missing_replacement_blocks(self):
        self.rename(replacement="missing.txt")
        self.assertTrue(self.plan().blockers)

    def test_replacement_lock_hash_drift_blocks(self):
        self.rename()
        self.lock({"renamed.txt": "0" * 64})
        self.f.commit_and_push("incorrect target lock")
        self.assertTrue(self.plan().blockers)

    def test_undeclared_remote_deletion_still_blocks(self):
        git(self.f.publisher, "rm", "shared.txt")
        self.release()
        self.f.commit_and_push("undeclared retirement")
        self.assertTrue(any("未授权删除" in b for b in self.plan().blockers))

    def test_future_protocol_blocks_even_without_any_deletions(self):
        self.release(protocol=999)
        self.f.commit_and_push("future protocol")
        self.assertTrue(self.plan().blockers)

    def test_version_downgrade_in_forward_commit_blocks(self):
        self.release(version="2026.10.23.2")
        self.f.commit_and_push("version two")
        git(self.f.colleague, "pull", "--ff-only")
        self.release(version="2026.10.23.1")
        self.f.commit_and_push("bad downgrade")
        self.assertTrue(self.plan().blockers)

    def test_skipping_intermediate_release_keeps_declared_rename_valid(self):
        self.rename()
        (self.f.publisher / "renamed.txt").write_bytes(b"v3\n")
        self.lock({"renamed.txt": hashlib.sha256(b"v3\n").hexdigest()})
        self.release(version="2026.10.23.3", retire=[{
            "path": "shared.txt", "old_sha256": [self.old_hash], "replacement": "renamed.txt"}])
        commit = self.f.commit_and_push("version three")
        self.assertFalse(self.plan().blockers)
        self.assertEqual(self.apply(commit), 0)
        self.assertEqual((self.f.colleague / "renamed.txt").read_bytes(), b"v3\n")

    def test_invalid_migration_path_and_duplicate_declaration_block(self):
        bad = {"path": "../private.txt", "old_sha256": [self.old_hash]}
        self.release([bad])
        self.f.commit_and_push("invalid path")
        self.assertTrue(self.plan().blockers)
        self.release([{"path": "shared.txt", "old_sha256": [self.old_hash]}] * 2)
        self.f.commit_and_push("duplicate retirement")
        self.assertTrue(self.plan().blockers)

    def test_same_version_cannot_publish_new_code(self):
        self.release()
        self.f.commit_and_push("version one")
        git(self.f.colleague, "pull", "--ff-only")
        self.f.publish_update()
        self.assertTrue(self.plan().blockers)

    def test_protected_retirement_is_rejected_even_if_not_currently_deleted(self):
        self.release([{"path": "config/fog_config.yaml", "old_sha256": [self.old_hash]}])
        self.f.commit_and_push("protected declaration")
        self.assertTrue(self.plan().blockers)

    def test_retiring_a_skill_stops_if_old_directory_has_extra_local_material(self):
        old = ".workbuddy/skills/fixture-old/SKILL.md"
        new = ".workbuddy/skills/fixture-new/SKILL.md"
        p = self.f.publisher / old
        p.parent.mkdir(parents=True)
        p.write_bytes(b"old fixture")
        sha = hashlib.sha256(b"old fixture").hexdigest()
        self.lock({"shared.txt": self.old_hash, old: sha})
        with (self.f.publisher / ".gitignore").open("a") as handle:
            handle.write("*.local\n")
        self.f.commit_and_push("managed old skill")
        git(self.f.colleague, "pull", "--ff-only")
        local = self.f.colleague / ".workbuddy/skills/fixture-old/input.local"
        local.write_bytes(b"local-owned")
        (self.f.publisher / new).parent.mkdir(parents=True)
        git(self.f.publisher, "mv", old, new)
        self.lock({"shared.txt": self.old_hash, new: sha})
        self.release([{"path": old, "old_sha256": [sha], "replacement": new}])
        commit = self.f.commit_and_push("retire whole old skill")
        before = self.f.repository_snapshot()
        self.assertTrue(self.plan().blockers)
        self.assertEqual(self.apply(commit), 1)
        self.assertEqual(local.read_bytes(), b"local-owned")
        self.assertEqual(self.f.repository_snapshot(), before)

    def test_failed_head_readback_does_not_report_success_or_reset(self):
        commit = self.rename()
        original = update_fog.git_output

        def broken_readback(repo, *args):
            value = original(repo, *args)
            if repo.resolve() == self.f.colleague.resolve() and args == ("rev-parse", "HEAD") and value == commit:
                return "0" * 40  # Simulate a failed independent VCS readback, after a real merge.
            return value

        with patch.object(update_fog, "git_output", side_effect=broken_readback):
            self.assertEqual(self.apply(commit), 1)
        self.assertEqual(git(self.f.colleague, "rev-parse", "HEAD").stdout.strip(), commit)
        self.assertTrue((self.f.colleague / "renamed.txt").is_file())

    def test_windows_unsafe_paths_are_rejected_before_git_update(self):
        from release_migrations import parse_release
        for path in ("tools\\bad.py", "tools/NUL.txt", "tools/trailing. ", "tools/../private.py"):
            with self.subTest(path=path):
                self.release([{"path": path, "old_sha256": [self.old_hash]}])
                text = (self.f.publisher / "config/workspace-release.json").read_text()
                with self.assertRaises(ValueError):
                    parse_release(text, update_fog.OFFICIAL_REPOSITORY)

    def exercise_rename_chain(self, install_intermediate):
        intermediate = self.rename()
        if install_intermediate:
            self.assertEqual(self.apply(intermediate), 0)
        git(self.f.publisher, "mv", "renamed.txt", "current.txt")
        self.lock({"current.txt": self.old_hash})
        self.release(version="2026.10.23.3", retire=[
            {"path": "shared.txt", "old_sha256": [self.old_hash], "replacement": "renamed.txt"},
            {"path": "renamed.txt", "old_sha256": [self.old_hash], "replacement": "current.txt"}])
        commit = self.f.commit_and_push("second rename")
        self.assertFalse(self.plan().blockers)
        self.assertEqual(self.apply(commit), 0)
        self.assertEqual((self.f.colleague / "current.txt").read_bytes(), b"v1\n")

    def test_chained_renames_support_skipping_the_intermediate_version(self):
        self.exercise_rename_chain(False)

    def test_chained_renames_support_an_already_installed_intermediate_version(self):
        self.exercise_rename_chain(True)

    def test_cyclic_migration_declarations_are_rejected(self):
        self.release(retire=[
            {"path": "shared.txt", "old_sha256": [self.old_hash], "replacement": "loop.txt"},
            {"path": "loop.txt", "old_sha256": [self.old_hash], "replacement": "shared.txt"}])
        self.f.commit_and_push("invalid cycle")
        self.assertTrue(self.plan().blockers)


if __name__ == "__main__":
    unittest.main()
