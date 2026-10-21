#!/usr/bin/env python3
"""Check and fast-forward a partner lxworkspace clone without touching local data."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tools"))
from brand_presets import MANIFEST, parse_manifest


OFFICIAL_REPOSITORY = "pxiaolei/lxworkspace"
UPDATE_BRANCH = "main"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_IGNORE_RULES = {
    ".env*",
    "config/fog_config.yaml",
    "config/personal_config.yaml",
    "config/database.yaml",
    "config/lx-wenda.json",
    ".workbuddy/skills/lx-haibao/brands/*.yaml",
    ".workbuddy/skills/lx-haibao/brand-assets/**",
    ".workbuddy/skills/*/assets/config.yaml",
    "workspace/**",
}
ALLOWED_REMOTE_DELETE_ROOTS = ()
ALLOWED_REMOTE_DELETE_PATHS = set()

@dataclass(frozen=True)
class ChangedPath:
    status: str
    path: str


@dataclass(frozen=True)
class UpdatePlan:
    local_commit: str
    remote_commit: str
    relation: str
    changes: tuple[ChangedPath, ...]
    blockers: tuple[str, ...]


def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def git_output(cwd: Path, *args: str) -> str:
    result = run_git(cwd, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or f"git {' '.join(args)} 执行失败")
    return result.stdout.strip()


def find_repo_root(start: Path) -> Path:
    probe = start.resolve()
    if probe.is_file():
        probe = probe.parent
    result = run_git(probe, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise RuntimeError("当前路径不在 lxworkspace Git 仓库中")
    root = Path(result.stdout.strip()).resolve()
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise RuntimeError("只允许普通 lxworkspace clone；不接受 worktree、符号链接或非标准 Git 根目录")
    return root


def normalize_github_repository(url: str) -> Optional[str]:
    value = url.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    patterns = (
        r"https://github\.com/([^/]+/[^/]+)$",
        r"git@github\.com:([^/]+/[^/]+)$",
        r"ssh://git@github\.com/([^/]+/[^/]+)$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def validate_official_origin(origin_url: str) -> None:
    repository = normalize_github_repository(origin_url)
    if repository is None or repository.casefold() != OFFICIAL_REPOSITORY.casefold():
        raise RuntimeError(f"origin 必须是官方共享仓 {OFFICIAL_REPOSITORY}；为避免泄露凭证，不回显当前 URL")


def ensure_clean_main(repo: Path) -> tuple[str, str]:
    branch = git_output(repo, "branch", "--show-current")
    if branch != UPDATE_BRANCH:
        raise RuntimeError(f"只允许更新 {UPDATE_BRANCH} 分支，当前分支为: {branch or 'detached HEAD'}")
    status = git_output(repo, "status", "--porcelain", "--untracked-files=all")
    if status:
        paths = []
        for line in status.splitlines()[:20]:
            paths.append(line[3:] if len(line) > 3 else line)
        suffix = "" if len(status.splitlines()) <= 20 else " 等"
        raise RuntimeError("本地存在未提交或未追踪改动，停止更新: " + ", ".join(paths) + suffix)
    head = git_output(repo, "rev-parse", "HEAD")
    origin_url = git_output(repo, "remote", "get-url", "origin")
    validate_official_origin(origin_url)
    return head, origin_url


def is_protected_path(raw_path: str, managed_brands=()) -> bool:
    path = PurePosixPath(raw_path)
    parts = path.parts
    lower_parts = tuple(part.casefold() for part in parts)
    name = path.name.casefold()
    lowered = raw_path.casefold()

    exact = {
        "config/fog_config.yaml",
        "config/fog_config.yaml.bak",
        "config/personal_config.yaml",
        "config/personal_config.yaml.bak",
        "config/database.yaml",
        "config/lx-wenda.json",
        ".workbuddy/skills/lx-wenda/config.json",
        ".workbuddy/skills/lx-wenda/_user_meta.json",
        "config/rds.md",
        ".lx-init-report.md",
        ".fog-update-report.md",
    }
    if lowered.startswith(".workbuddy/skills/lx-haibao/brand-assets/") and raw_path != ".workbuddy/skills/lx-haibao/brand-assets/README.md":
        return raw_path not in managed_brands
    if lowered.startswith(".workbuddy/skills/lx-haibao/brands/") and path.suffix.casefold() == ".yaml":
        return raw_path not in managed_brands
    if lowered in exact:
        return True
    if name.startswith(".env") and name != ".env.example":
        return True
    if parts and parts[0].casefold() == "workspace" and name != ".gitkeep" and raw_path not in {"workspace/README.md", "workspace/08端内宣传图/README.md"}:
        return True
    if lower_parts[:2] in {
        (".workbuddy", "memory"),
        (".workbuddy", "scripts"),
    }:
        return True
    if any(part in {".venv", "node_modules", "__pycache__", "dist", "build"} for part in lower_parts):
        return True
    if name in {"config.yaml", "credentials", "secrets", ".token_cache", ".ds_store"} and "assets" in lower_parts:
        return True
    if any(part in {"cache", "query_runs", "output", "outputs"} for part in lower_parts):
        return True
    if "cache" in name and "assets" in lower_parts:
        return True
    if path.suffix.casefold() in {".zip", ".tmp", ".log", ".pyc", ".pyo", ".bak"}:
        return True
    return False


def is_allowed_remote_deletion(raw_path: str) -> bool:
    path = PurePosixPath(raw_path)
    if raw_path in ALLOWED_REMOTE_DELETE_PATHS:
        return True
    return any(path == root or root in path.parents for root in ALLOWED_REMOTE_DELETE_ROOTS)


def parse_name_status(raw: str) -> tuple[ChangedPath, ...]:
    if not raw:
        return ()
    fields = raw.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if index >= len(fields):
            raise RuntimeError("无法解析远端变更列表")
        path = fields[index]
        index += 1
        changes.append(ChangedPath(status=status, path=path))
    return tuple(changes)


def inspect_remote(repo: Path, local_commit: str, origin_url: str) -> UpdatePlan:
    with tempfile.TemporaryDirectory(prefix="lx-update-") as tmp:
        mirror = Path(tmp) / "inspection.git"
        git_output(Path(tmp), "init", "--bare", str(mirror))
        git_output(mirror, "fetch", "--no-tags", str(repo), f"{local_commit}:refs/heads/local")
        git_output(mirror, "fetch", "--no-tags", origin_url, f"refs/heads/{UPDATE_BRANCH}:refs/heads/remote")
        remote_commit = git_output(mirror, "rev-parse", "refs/heads/remote")

        if local_commit == remote_commit:
            relation = "up-to-date"
        elif run_git(mirror, "merge-base", "--is-ancestor", local_commit, remote_commit).returncode == 0:
            relation = "fast-forward-ready"
        elif run_git(mirror, "merge-base", "--is-ancestor", remote_commit, local_commit).returncode == 0:
            relation = "local-ahead"
        else:
            relation = "diverged"

        raw_changes = git_output(
            mirror,
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            local_commit,
            remote_commit,
        )
        changes = parse_name_status(raw_changes)
        blockers = []
        if relation in {"local-ahead", "diverged"}:
            blockers.append(f"本地与远端关系为 {relation}，不能自动更新")
        deleted = [
            change.path
            for change in changes
            if change.status.startswith("D") and not is_allowed_remote_deletion(change.path)
        ]
        if deleted:
            blockers.append("远端更新包含未授权删除: " + ", ".join(deleted[:20]))

        tree = git_output(mirror, "ls-tree", "-r", "-z", remote_commit)
        managed_brands = {}
        manifest_result = run_git(mirror, "show", f"{remote_commit}:{MANIFEST}")
        if manifest_result.returncode == 0:
            try:
                managed_brands = parse_manifest(manifest_result.stdout)["files"]
                for path, expected in managed_brands.items():
                    blob = subprocess.run(["git", "show", f"{remote_commit}:{path}"], cwd=mirror, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                    if blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != expected:
                        blockers.append("远端公开品牌文件缺失或 hash 不符: " + path)
            except (ValueError, TypeError, AttributeError) as exc:
                blockers.append("远端公开品牌清单无效: " + type(exc).__name__)
        for entry in tree.split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            mode, object_type, _ = metadata.split(" ", 2)
            if mode == "120000" or object_type == "commit":
                blockers.append(f"远端包含 Windows/安全边界不接受的符号链接或 submodule: {path}")
            if is_protected_path(path, managed_brands):
                blockers.append(f"远端跟踪了本地受保护路径: {path}")

        ignore_result = run_git(mirror, "show", f"{remote_commit}:.gitignore")
        if ignore_result.returncode != 0:
            blockers.append("远端缺少 .gitignore，无法证明本地配置和业务文件受保护")
        else:
            ignore_rules = {
                line.strip()
                for line in ignore_result.stdout.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            }
            missing_rules = sorted(REQUIRED_IGNORE_RULES - ignore_rules)
            if missing_rules:
                blockers.append("远端 .gitignore 缺少必要保护规则: " + ", ".join(missing_rules))

        return UpdatePlan(
            local_commit=local_commit,
            remote_commit=remote_commit,
            relation=relation,
            changes=changes,
            blockers=tuple(dict.fromkeys(blockers)),
        )


def build_plan(repo: Path) -> UpdatePlan:
    local_commit, origin_url = ensure_clean_main(repo)
    plan = inspect_remote(repo, local_commit, origin_url)
    blockers = list(plan.blockers)
    for change in plan.changes:
        if not change.status.startswith("A"):
            continue
        destination = repo / Path(*PurePosixPath(change.path).parts)
        tracked = run_git(repo, "ls-files", "--error-unmatch", "--", change.path).returncode == 0
        if not tracked and (destination.exists() or destination.is_symlink()):
            blockers.append(f"远端新增文件会与本地未跟踪或 ignored 路径冲突: {change.path}")
    return UpdatePlan(
        local_commit=plan.local_commit,
        remote_commit=plan.remote_commit,
        relation=plan.relation,
        changes=plan.changes,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def print_plan(plan: UpdatePlan) -> None:
    status = "blocked" if plan.blockers else plan.relation
    print(f"status: {status}")
    print(f"local_commit: {plan.local_commit}")
    print(f"remote_commit: {plan.remote_commit}")
    print(f"relation: {plan.relation}")
    print(f"changes: {len(plan.changes)}")
    for change in plan.changes:
        print(f"  {change.status} {change.path}")
    for blocker in plan.blockers:
        print(f"BLOCKED: {blocker}")


def run_validation(repo: Path, command: tuple[str, ...], label: str) -> bool:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        list(command),
        cwd=repo,
        text=True,
        encoding="utf-8",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(f"{label}: {'pass' if result.returncode == 0 else 'needs-attention'}")
    if result.stdout.strip():
        print(result.stdout.rstrip())
    return result.returncode == 0


def apply_update(repo: Path, expected_commit: str) -> int:
    if not FULL_SHA.fullmatch(expected_commit):
        raise RuntimeError("--confirmed 必须是 check 输出的 40 位完整 remote_commit")
    plan = build_plan(repo)
    print_plan(plan)
    if plan.blockers:
        raise RuntimeError("安全检查未通过，未更新")
    if expected_commit != plan.remote_commit:
        raise RuntimeError("确认 commit 与当前远端不一致；重新运行 check 后再确认")
    if plan.relation == "up-to-date":
        print("result: up-to-date")
        return 0
    if plan.relation != "fast-forward-ready":
        raise RuntimeError(f"当前关系不允许 fast-forward: {plan.relation}")

    before_head, _ = ensure_clean_main(repo)
    if before_head != plan.local_commit:
        raise RuntimeError("检查后本地 HEAD 已变化；重新运行 check")
    remote_ref = f"refs/remotes/origin/{UPDATE_BRANCH}"
    git_output(repo, "fetch", "--no-tags", "origin", f"refs/heads/{UPDATE_BRANCH}")
    fetched = git_output(repo, "rev-parse", "FETCH_HEAD")
    if fetched != expected_commit:
        raise RuntimeError("拉取时远端 commit 已变化；未修改工作树，请重新运行 check")
    git_output(repo, "-c", "merge.autoStash=false", "merge", "--ff-only", expected_commit)

    actual = git_output(repo, "rev-parse", "HEAD")
    if actual != expected_commit:
        raise RuntimeError("更新后 HEAD 读回不等于确认 commit")
    git_output(repo, "update-ref", remote_ref, actual)
    if git_output(repo, "rev-parse", remote_ref) != actual:
        raise RuntimeError("更新后 origin/main 与 HEAD 未对齐")
    if git_output(repo, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("更新后 Git 工作区不干净")

    index_ok = run_validation(
        repo,
        (sys.executable, "-B", "tools/generate_skill_index.py", "--check"),
        "skill_index_check",
    )
    config_ok = run_validation(repo, (sys.executable, "-B", "tools/check_distribution.py"), "config_check")
    print(f"updated_head: {actual}")
    if index_ok:
        print(f"result: {'applied' if config_ok else 'applied-with-warnings'}")
        return 0
    print("result: applied-with-validation-failure")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安全检查并快进同事本地 lxworkspace")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="lxworkspace Git 仓路径，默认当前目录")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="只读检查本地状态和远端更新")
    apply_parser = subparsers.add_parser("apply", help="快进到已确认的远端 commit")
    apply_parser.add_argument("--confirmed", required=True, metavar="COMMIT", help="check 输出的完整 remote_commit")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo = find_repo_root(args.repo)
        if args.command == "check":
            plan = build_plan(repo)
            print_plan(plan)
            return 1 if plan.blockers else 0
        return apply_update(repo, args.confirmed.lower())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
