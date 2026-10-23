#!/usr/bin/env python3
"""Run a pinned, verified updater outside an old workspace. Standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

TOOL_FILES = (
    ".workbuddy/skills/lx-update/scripts/update_fog.py",
    ".workbuddy/skills/lx-update/scripts/release_migrations.py",
    "tools/brand_presets.py",
)
OFFICIAL = {"pxiaolei/FOG", "pxiaolei/lxworkspace"}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def git(root, *args):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(["git", *args], cwd=root, env=env, text=True,
                            encoding="utf-8", errors="replace", stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ValueError("Git操作失败；未回显可能包含凭据的远端错误")
    return result.stdout.strip()


def inspect_workspace(repo):
    root = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    if root != repo.resolve() or not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise ValueError("请指定普通工作区Git仓根目录，不接受worktree或符号链接")
    if git(root, "branch", "--show-current") != "main" or git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("工作区必须是干净的main分支；不自动清理或合并")
    profile = json.loads((root / "config/distribution.json").read_text(encoding="utf-8"))
    repository = profile.get("repository")
    if repository not in OFFICIAL:
        raise ValueError("工作区未绑定官方分发仓")
    origin = git(root, "remote", "get-url", "origin")
    accepted = {f"https://github.com/{repository}", f"https://github.com/{repository}.git",
                f"git@github.com:{repository}.git", f"ssh://git@github.com/{repository}.git"}
    if origin not in accepted:
        raise ValueError("origin与官方工作区身份不一致")
    return repository, origin


def run_bootstrap(repo, commit, action, confirmed=None):
    if not FULL_SHA.fullmatch(commit) or action not in {"check", "apply"}:
        raise ValueError("必须指定固定的40位发布commit及check/apply")
    if action == "apply" and confirmed != commit:
        raise ValueError("apply需明确确认同一完整commit；未运行更新")
    repository, origin = inspect_workspace(repo)
    with tempfile.TemporaryDirectory(prefix="lx-update-bootstrap-") as tmp:
        staged = Path(tmp)
        git(staged, "init")
        git(staged, "-c", "fetch.fsckObjects=true", "fetch", "--no-tags", origin, "refs/heads/main")
        if git(staged, "rev-parse", "FETCH_HEAD") != commit:
            raise ValueError("发布commit已变化或不可用，请重新检查版本后确认")
        git(staged, "-c", "core.autocrlf=false", "checkout", "--detach", commit)
        profile = json.loads((staged / "config/distribution.json").read_text(encoding="utf-8"))
        release = json.loads((staged / "config/workspace-release.json").read_text(encoding="utf-8"))
        if profile.get("repository") != repository or release.get("repository") != repository:
            raise ValueError("固定版本仓库身份不匹配")
        files = json.loads((staged / "config/source-lock.json").read_text(encoding="utf-8")).get("files", {})
        for relative in TOOL_FILES:
            path = staged / relative
            parts = Path(relative).parts
            linked = any((staged / Path(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1))
            if linked or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != files.get(relative):
                raise ValueError("新版更新工具缺失或来源锁hash不符: " + relative)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        command = [sys.executable, "-B", str(staged / TOOL_FILES[0]), "--repo", str(repo.resolve()), action]
        if action == "apply":
            command += ["--confirmed", confirmed]
        code = subprocess.run(command, env=env, cwd=staged, check=False).returncode
        if code == 0 and action == "apply":
            if git(repo, "rev-parse", "HEAD") != commit or git(repo, "rev-parse", "origin/main") != commit:
                raise ValueError("新版工具返回成功但版本读回不一致；保留现场，不自动回滚")
            if git(repo, "status", "--porcelain", "--untracked-files=all"):
                raise ValueError("更新后工作区不干净；保留现场，不自动回滚")
        return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True, help="维护方给出的完整固定发布commit")
    parser.add_argument("action", choices=("check", "apply"))
    parser.add_argument("--confirmed")
    args = parser.parse_args(argv)
    try:
        return run_bootstrap(args.repo, args.commit.lower(), args.action, args.confirmed)
    except ValueError as exc:
        print("引导未完成：" + str(exc), file=sys.stderr)
        return 1
    except (OSError, TypeError, KeyError):
        print("引导未完成：请核对固定版本、来源锁、官方身份与本地状态；未自动重试或回滚。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
