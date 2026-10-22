import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_fog.py"
SPEC = importlib.util.spec_from_file_location("update_fog", SCRIPT)
update_fog = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = update_fog
SPEC.loader.exec_module(update_fog)


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class FogUpdateSafetyTests(unittest.TestCase):
    def test_existing_fog_brand_files_and_partner_custom_assets_keep_their_boundaries(self):
        path = ".workbuddy/skills/lx-haibao/brand-assets/example/logo.png"
        with patch.object(update_fog, "OFFICIAL_REPOSITORY", "pxiaolei/FOG"):
            self.assertFalse(update_fog.is_protected_path(path))
        with patch.object(update_fog, "OFFICIAL_REPOSITORY", "pxiaolei/lxworkspace"):
            self.assertTrue(update_fog.is_protected_path(path))
            self.assertFalse(update_fog.is_protected_path(path, {path: "fixture"}))

    def test_skill_contract_explicitly_promises_token_and_api_key_preservation(self):
        skill_text = (SCRIPT.parents[1] / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("token", skill_text)
        self.assertIn("API Key", skill_text)
        self.assertIn("逐字段保留", skill_text)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", str(self.remote))
        self.publisher = self.root / "publisher"
        git(self.root, "clone", str(self.remote), str(self.publisher))
        git(self.publisher, "checkout", "-b", "main")
        self.write_base_files()
        self.commit_and_push("initial")
        self.colleague = self.root / "FOG"
        git(self.root, "clone", "--branch", "main", str(self.remote), str(self.colleague))

    def tearDown(self):
        self.tmp.cleanup()

    def write_base_files(self):
        (self.publisher / ".gitignore").write_text(
            "\n".join(
                sorted(update_fog.REQUIRED_IGNORE_RULES) + [
                    ".env*",
                    "!.env.example",
                    "config/fog_config.yaml",
                    "config/personal_config.yaml",
                    "config/database.yaml",
                    ".workbuddy/skills/*/assets/config.yaml",
                    "workspace/**",
                    "!workspace/.gitkeep",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (self.publisher / "tools").mkdir()
        (self.publisher / "tools" / "generate_skill_index.py").write_text(
            "raise SystemExit(0)\n", encoding="utf-8"
        )
        (self.publisher / "tools" / "fog.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        (self.publisher / "workspace").mkdir()
        (self.publisher / "workspace" / ".gitkeep").write_text("", encoding="utf-8")
        retired = self.publisher / ".workbuddy" / "skills" / "lx-zhoubao"
        retired.mkdir(parents=True)
        (retired / "SKILL.md").write_text("retired\n", encoding="utf-8")
        (self.publisher / "shared.txt").write_text("v1\n", encoding="utf-8")

    def commit_and_push(self, message: str) -> str:
        git(self.publisher, "add", ".")
        git(
            self.publisher,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            message,
        )
        git(self.publisher, "push", "origin", "main")
        return git(self.publisher, "rev-parse", "HEAD").stdout.strip()

    def publish_update(self) -> str:
        (self.publisher / "shared.txt").write_text("v2\n", encoding="utf-8")
        return self.commit_and_push("update")

    def repository_snapshot(self):
        return {
            "head": git(self.colleague, "rev-parse", "HEAD").stdout,
            "refs": git(self.colleague, "for-each-ref", "--format=%(refname) %(objectname)").stdout,
            "status": git(self.colleague, "status", "--porcelain", "--untracked-files=all").stdout,
            "shared": (self.colleague / "shared.txt").read_bytes(),
        }

    def official_origin_allowed(self):
        return patch.object(update_fog, "validate_official_origin", return_value=None)

    def test_check_does_not_mutate_head_refs_index_or_worktree(self):
        self.publish_update()
        before = self.repository_snapshot()
        with self.official_origin_allowed():
            self.assertEqual(0, update_fog.main(["--repo", str(self.colleague), "check"]))
        self.assertEqual(before, self.repository_snapshot())

    def test_dirty_or_untracked_worktree_blocks_before_remote_inspection(self):
        (self.colleague / "local-note.txt").write_text("mine\n", encoding="utf-8")
        with self.official_origin_allowed(), patch.object(
            update_fog, "inspect_remote", side_effect=AssertionError("must stop before network inspection")
        ):
            self.assertEqual(1, update_fog.main(["--repo", str(self.colleague), "check"]))

    def test_apply_requires_exact_checked_commit_and_preserves_token_api_fields(self):
        expected = self.publish_update()
        config = self.colleague / "config" / "fog_config.yaml"
        config.parent.mkdir()
        config.write_text(
            "services:\n"
            "  feishu_token: keep-feishu-token\n"
            "  image_api_key: keep-image-api-key\n"
            "  nested:\n"
            "    access_token: keep-access-token\n",
            encoding="utf-8",
        )
        business = self.colleague / "workspace" / "orders.xlsx"
        business.write_bytes(b"private-business-data")
        config_before = config.read_bytes()
        business_before = business.read_bytes()

        with self.official_origin_allowed():
            self.assertEqual(
                0,
                update_fog.main(
                    ["--repo", str(self.colleague), "apply", "--confirmed", expected]
                ),
            )
        self.assertEqual(expected, git(self.colleague, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual(expected, git(self.colleague, "rev-parse", "origin/main").stdout.strip())
        self.assertEqual(config_before, config.read_bytes())
        self.assertEqual(business_before, business.read_bytes())
        self.assertEqual("", git(self.colleague, "status", "--porcelain", "--untracked-files=all").stdout)

    def test_wrong_confirmation_leaves_head_and_worktree_unchanged(self):
        self.publish_update()
        before = self.repository_snapshot()
        wrong = "0" * 40
        with self.official_origin_allowed():
            self.assertEqual(
                1,
                update_fog.main(
                    ["--repo", str(self.colleague), "apply", "--confirmed", wrong]
                ),
            )
        self.assertEqual(before, self.repository_snapshot())

    def test_remote_deletion_is_blocked(self):
        git(self.publisher, "rm", "shared.txt")
        self.commit_and_push("delete")
        with self.official_origin_allowed():
            plan = update_fog.build_plan(self.colleague)
        self.assertTrue(any("包含未授权删除" in blocker for blocker in plan.blockers))

    def test_remote_cannot_rebind_workspace_identity(self):
        import json
        config = self.publisher / "config"
        config.mkdir()
        other = "pxiaolei/FOG" if update_fog.OFFICIAL_REPOSITORY == "pxiaolei/lxworkspace" else "pxiaolei/lxworkspace"
        (config / "distribution.json").write_text(json.dumps({"repository": other}))
        self.commit_and_push("invalid workspace identity")
        before = self.repository_snapshot()
        with self.official_origin_allowed():
            plan = update_fog.build_plan(self.colleague)
        self.assertTrue(any("身份" in value for value in plan.blockers))
        self.assertEqual(self.repository_snapshot(), before)

    def test_allowlisted_retired_skill_deletion_can_be_confirmed_without_touching_config(self):
        git(self.publisher, "rm", "-r", ".workbuddy/skills/lx-zhoubao")
        expected = self.commit_and_push("retire shared skill")
        config = self.colleague / "config" / "fog_config.yaml"
        config.parent.mkdir()
        config.write_text(
            "services:\n"
            "  feishu_token: keep-feishu-token\n"
            "  image_api_key: keep-image-api-key\n",
            encoding="utf-8",
        )
        before = config.read_bytes()

        with self.official_origin_allowed():
            plan = update_fog.build_plan(self.colleague)
            if update_fog.OFFICIAL_REPOSITORY == "pxiaolei/lxworkspace":
                self.assertTrue(any("未授权删除" in value for value in plan.blockers))
                self.assertEqual(before, config.read_bytes())
                self.assertTrue((self.colleague / ".workbuddy/skills/lx-zhoubao").exists())
                return
            self.assertFalse(plan.blockers)
            self.assertEqual(
                0,
                update_fog.main(
                    ["--repo", str(self.colleague), "apply", "--confirmed", expected]
                ),
            )

        self.assertFalse((self.colleague / ".workbuddy/skills/lx-zhoubao").exists())
        self.assertEqual(before, config.read_bytes())

    def test_remote_protected_file_is_blocked(self):
        (self.publisher / "config").mkdir()
        (self.publisher / "config" / "fog_config.yaml").write_text("leaked: true\n", encoding="utf-8")
        git(self.publisher, "add", "-f", "config/fog_config.yaml")
        self.commit_and_push("bad config")
        with self.official_origin_allowed():
            plan = update_fog.build_plan(self.colleague)
        self.assertTrue(any("受保护路径" in blocker for blocker in plan.blockers))

    def test_remote_missing_ignore_rule_is_blocked(self):
        ignore = self.publisher / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8").replace("workspace/**\n", ""), encoding="utf-8")
        self.commit_and_push("weaken ignore")
        with self.official_origin_allowed():
            plan = update_fog.build_plan(self.colleague)
        self.assertTrue(any("缺少必要保护规则" in blocker for blocker in plan.blockers))

    def test_remote_addition_conflicting_with_local_ignored_file_is_blocked(self):
        ignore = self.publisher / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") + "local-only.txt\n", encoding="utf-8")
        self.commit_and_push("share ignore rule")
        git(self.colleague, "pull", "--ff-only")
        (self.colleague / "local-only.txt").write_text("mine\n", encoding="utf-8")

        (self.publisher / "local-only.txt").write_text("remote\n", encoding="utf-8")
        git(self.publisher, "add", "-f", "local-only.txt")
        self.commit_and_push("add colliding file")
        with self.official_origin_allowed():
            plan = update_fog.build_plan(self.colleague)
        self.assertTrue(any("ignored 路径冲突" in blocker for blocker in plan.blockers))


if __name__ == "__main__":
    unittest.main()
