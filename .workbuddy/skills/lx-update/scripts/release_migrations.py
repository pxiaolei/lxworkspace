"""Pure-data release validation; no filesystem writes or migration execution."""
import json
import re
from pathlib import PurePosixPath

RELEASE_PATH = "config/workspace-release.json"
LOCK_PATH = "config/source-lock.json"
UPDATER_PROTOCOL = 1
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.[1-9]\d*$")


def safe_release_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value:
        raise ValueError("迁移路径无效")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != value or ".git" in path.parts:
        raise ValueError("迁移路径不是规范相对工程路径")
    reserved = {"con", "prn", "aux", "nul"} | {prefix + str(i) for prefix in ("com", "lpt") for i in range(1, 10)}
    if any(part.endswith((".", " ")) or part.split(".")[0].casefold() in reserved
           or any(ord(c) < 32 or c in '<>"|?*' for c in part) for part in path.parts):
        raise ValueError("迁移路径不兼容Windows")
    return value


def parse_release(text, repository):
    if text is None:
        return {"version": "legacy", "migrations": []}
    data = json.loads(text)
    keys = {"schema_version", "version", "repository", "minimum_updater_protocol", "migrations"}
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError("发布清单字段不完整或包含未知字段")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("发布清单schema不支持，需要新版引导工具")
    minimum = data["minimum_updater_protocol"]
    if type(minimum) is not int or not 1 <= minimum <= UPDATER_PROTOCOL:
        raise ValueError("更新协议不支持，需要新版引导工具")
    if data["repository"] != repository:
        raise ValueError("发布清单仓库身份不匹配")
    if not isinstance(data["version"], str) or not VERSION.fullmatch(data["version"]):
        raise ValueError("发布版本须为YYYY.MM.DD.N")
    if not isinstance(data["migrations"], list):
        raise ValueError("迁移声明必须是数组")
    ids, paths, links = set(), set(), {}
    for migration in data["migrations"]:
        if not isinstance(migration, dict) or set(migration) != {"id", "retire"}:
            raise ValueError("迁移只能声明id和retire，不接受执行命令")
        mid = migration["id"]
        if not isinstance(mid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", mid) or mid in ids:
            raise ValueError("迁移id无效或重复")
        ids.add(mid)
        if not isinstance(migration["retire"], list) or not migration["retire"]:
            raise ValueError("迁移必须声明精确退役文件")
        for entry in migration["retire"]:
            if not isinstance(entry, dict) or set(entry) not in ({"path", "old_sha256"}, {"path", "old_sha256", "replacement"}):
                raise ValueError("退役声明字段无效")
            path = safe_release_path(entry["path"])
            if path in paths:
                raise ValueError("退役路径重复")
            paths.add(path)
            hashes = entry["old_sha256"]
            if not isinstance(hashes, list) or not hashes or any(not isinstance(h, str) or not SHA256.fullmatch(h) for h in hashes):
                raise ValueError("退役旧hash无效")
            if "replacement" in entry:
                replacement = safe_release_path(entry["replacement"])
                if replacement == path:
                    raise ValueError("替代路径不能与旧路径相同")
            links[path] = entry.get("replacement")
    for path in links:
        resolve_replacement(path, links)
    return data


def resolve_replacement(path, links):
    seen = {path}
    replacement = links.get(path)
    while replacement in links:
        if replacement in seen:
            raise ValueError("迁移替代路径存在循环")
        seen.add(replacement)
        replacement = links[replacement]
    return replacement


def parse_source_lock(text):
    if text is None:
        return {}
    data = json.loads(text)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise ValueError("来源锁缺少files")
    for path, sha in files.items():
        safe_release_path(path)
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise ValueError("来源锁hash无效")
    return files


def approved_retirements(release, deleted, old_files, new_files, blob_sha256, protected):
    approved = {}
    links = {e["path"]: e.get("replacement") for m in release["migrations"] for e in m["retire"]}
    for migration in release["migrations"]:
        for entry in migration["retire"]:
            path = entry["path"]
            replacement = resolve_replacement(path, links)
            if protected(path) or (replacement and protected(replacement)):
                raise ValueError("迁移涉及受保护路径: " + path)
            if path not in deleted:
                continue  # Already retired in an intermediate version.
            old_hash = old_files.get(path)
            if old_hash not in entry["old_sha256"] or blob_sha256("old", path) != old_hash:
                raise ValueError("退役旧文件不属于来源锁或hash不匹配: " + path)
            if path in new_files:
                raise ValueError("已退役路径仍出现在新来源锁: " + path)
            if replacement and (replacement not in new_files or blob_sha256("new", replacement) != new_files[replacement]):
                raise ValueError("替代文件缺失或与新来源锁不符: " + replacement)
            approved[path] = replacement
    return approved
