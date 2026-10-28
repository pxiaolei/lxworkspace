#!/usr/bin/env python3
"""Generate or verify the canonical project Skill index."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / ".workbuddy" / "skills"
DEFAULT_OUTPUT = ROOT / "SKILLS.md"


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    display_name: str
    implicit: bool
    relative_path: str


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _read_frontmatter(path: Path, text: Optional[str] = None) -> dict[str, str]:
    text = path.read_text(encoding="utf-8") if text is None else text
    if not text.startswith("---\n"):
        raise ValueError(f"缺少 YAML frontmatter: {path}")
    try:
        block = text.split("---\n", 2)[1]
    except IndexError as exc:
        raise ValueError(f"frontmatter 未闭合: {path}") from exc
    values: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) or ":" not in line:
            raise ValueError(f"frontmatter 只允许单行顶层键值: {path}: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        if key in values:
            raise ValueError(f"frontmatter 键重复: {path}: {key}")
        values[key] = _unquote(value)
    if set(values) != {"name", "description"}:
        raise ValueError(f"frontmatter 必须且只能包含 name/description: {path}: {sorted(values)}")
    return values


def _quoted_yaml_value(text: str, key: str, path: Path) -> str:
    matches = re.findall(rf'^\s*{re.escape(key)}:\s*(.+?)\s*$', text, flags=re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"openai.yaml 的 {key} 数量应为 1，实际为 {len(matches)}: {path}")
    raw = matches[0].strip()
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        raise ValueError(f"openai.yaml 的 {key} 必须使用双引号: {path}")
    return _unquote(raw)


def _read_openai_yaml(path: Path, skill_name: str, text: Optional[str] = None) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8") if text is None else text
    implicit = re.findall(r'^\s*allow_implicit_invocation:\s*(true|false)\s*$', text, flags=re.MULTILINE)
    if len(implicit) != 1:
        raise ValueError(f"openai.yaml 的 allow_implicit_invocation 数量应为 1，实际为 {len(implicit)}: {path}")
    display_name = _quoted_yaml_value(text, "display_name", path)
    short_description = _quoted_yaml_value(text, "short_description", path)
    default_prompt = _quoted_yaml_value(text, "default_prompt", path)
    if not display_name or not short_description:
        raise ValueError(f"openai.yaml 的显示名和短描述不能为空: {path}")
    if f"${skill_name}" not in default_prompt:
        raise ValueError(f"openai.yaml 的 default_prompt 必须显式包含 ${skill_name}: {path}")
    return display_name, implicit[0] == "true"


def _validate_execution_contract(path: Path, text: Optional[str] = None) -> None:
    text = path.read_text(encoding="utf-8") if text is None else text
    required = ["## 执行契约", "### 输入", "### 输出", "### 验收"]
    for heading in required:
        count = sum(1 for line in text.splitlines() if line.strip() == heading)
        if count != 1:
            raise ValueError(f"{path} 的 {heading} 数量应为 1，实际为 {count}")


def collect_skills() -> list[SkillRecord]:
    records: list[SkillRecord] = []
    seen: set[str] = set()
    for skill_file in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        frontmatter = _read_frontmatter(skill_file)
        name = frontmatter["name"]
        if name in seen:
            raise ValueError(f"Skill 名称重复: {name}")
        seen.add(name)
        _validate_execution_contract(skill_file)
        display_name, implicit = _read_openai_yaml(skill_file.parent / "agents" / "openai.yaml", name)
        records.append(SkillRecord(
            name=name,
            description=frontmatter["description"],
            display_name=display_name,
            implicit=implicit,
            relative_path=skill_file.relative_to(ROOT).as_posix(),
        ))
    return sorted(records, key=lambda item: item.name.casefold())


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def render_index(records: list[SkillRecord], title: Optional[str] = None) -> str:
    explicit_title = title is not None
    title = title or ROOT.name
    profile = ROOT / "config/distribution.json"
    if not explicit_title and profile.is_file():
        repository = json.loads(profile.read_text(encoding="utf-8")).get("repository")
        if repository in {"pxiaolei/FOG", "pxiaolei/lxworkspace"}:
            title = repository.split("/")[1]
    lines = [
        f"# {title} Skill 索引",
        "",
        "> 本文件由 `python3 tools/generate_skill_index.py` 根据真实目录生成；不要手工维护 Skill 清单。具体流程、输入输出和安全边界以各 Skill 的 `SKILL.md` 为准。",
        "",
        f"当前共 {len(records)} 个 Skill。`显式` 表示 `allow_implicit_invocation: false`，必须由用户明确点名或确认后使用。",
        "",
        "| Skill | 显示名 | 调用策略 | 入口 | 描述 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for record in records:
        policy = "可隐式路由" if record.implicit else "显式"
        lines.append(
            f"| `{_cell(record.name)}` | {_cell(record.display_name)} | {policy} | "
            f"[`SKILL.md`]({_cell(record.relative_path)}) | {_cell(record.description)} |"
        )
    lines.extend([
        "",
        "## 校验",
        "",
        "```bash",
        "python3 tools/generate_skill_index.py --check",
        "```",
        "",
        "该命令同时校验：Skill 名不重复、frontmatter 精确为 `name`/`description`、执行契约结构唯一、`agents/openai.yaml` 元数据和调用策略完整，以及本索引未漂移。",
        "",
    ])
    return "\n".join(lines)


def render_mapped_index(root: Path, files: dict[str, str], repository: str, payloads: Optional[dict[str, bytes]] = None) -> str:
    """Validate and render a distribution's mapped Skills without writing a tree."""
    records = []
    seen = set()
    for destination, source in sorted(files.items()):
        path = Path(destination)
        if len(path.parts) != 4 or path.parts[:2] != (".workbuddy", "skills") or path.name != "SKILL.md":
            continue
        text = payloads[destination].decode("utf-8") if payloads is not None else None
        frontmatter = _read_frontmatter(root / source, text)
        name = frontmatter["name"]
        if name != path.parent.name or name in seen:
            raise ValueError("分发 Skill 名称重复或目录不符")
        seen.add(name)
        _validate_execution_contract(root / source, text)
        agent = (path.parent / "agents/openai.yaml").as_posix()
        if agent not in files:
            raise ValueError("分发 Skill 缺少 agents/openai.yaml")
        agent_text = payloads[agent].decode("utf-8") if payloads is not None else None
        display, implicit = _read_openai_yaml(root / files[agent], name, agent_text)
        records.append(SkillRecord(name, frontmatter["description"], display, implicit, destination))
    return render_index(sorted(records, key=lambda item: item.name.casefold()), repository.split("/")[-1])


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成或检查 p-fog Skill 索引")
    parser.add_argument("--check", action="store_true", help="只检查 SKILLS.md 是否与真实目录一致")
    parser.add_argument("--stdout", action="store_true", help="将生成内容输出到 stdout，不写文件")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出路径")
    args = parser.parse_args(argv)

    try:
        content = render_index(collect_skills())
    except (OSError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    if args.stdout:
        print(content, end="")
        return 0
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
            print(f"[FAIL] Skill 索引漂移: {args.output}", file=sys.stderr)
            return 1
        print(f"[PASS] Skill 索引与 {len(collect_skills())} 个真实 Skill 一致")
        return 0

    args.output.write_text(content, encoding="utf-8")
    print(f"[PASS] 已生成 {args.output}")
    return 0
