#!/usr/bin/env python3
"""Generate thin WorkBuddy loaders without duplicating executable skills."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALIASES = {"lx-shujuku": "lx_shujuku", **{name: name for name in ("lx-yidongfenxi", "lx-haibao", "lx-wenda", "lx-kaquan", "lx-diaozhang", "lx-zuotu", "lx-init", "lx-update")}}


def content(alias, canonical):
    source = ROOT / ".workbuddy/skills" / canonical / "SKILL.md"
    text = source.read_text(encoding="utf-8")
    description = next(line.split(':', 1)[1].strip() for line in text.split('---', 2)[1].splitlines() if line.startswith('description:'))
    return f'''---
name: {alias}
description: {description}
---

# WorkBuddy 项目加载入口

这只是加载入口。执行前完整读取项目根目录的 `.workbuddy/skills/{canonical}/SKILL.md`，遵守其中输入、输出、预览、确认和验收契约，以及根 `AGENTS.md`。

所有脚本仍通过项目根目录的 `tools/run_skill.py` 执行；不从当前适配目录推断资源路径，不复制脚本、品牌资料或配置。路由不代表获得执行授权。
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    errors = []
    entries = ROOT / ".codebuddy/skills"
    unexpected = {path.parent.name for path in entries.glob("*/SKILL.md")} - set(ALIASES)
    if unexpected:
        errors.extend(sorted(unexpected))
    for alias, canonical in ALIASES.items():
        file = ROOT / ".codebuddy/skills" / alias / "SKILL.md"
        expected = content(alias, canonical)
        if args.check:
            if not file.is_file() or file.read_text(encoding="utf-8") != expected:
                errors.append(alias)
        else:
            if unexpected:
                raise ValueError("加载入口超出九项能力，请先审阅")
            if file.exists() and file.read_text(encoding="utf-8") != expected:
                raise ValueError(f"加载入口已有修改，请先审阅: {alias}")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(expected, encoding="utf-8")
    if args.check and errors:
        print("WorkBuddy entry drift: " + ', '.join(errors))
        return 1
    print(f"WorkBuddy thin entries: {len(ALIASES)} verified" if args.check else f"WorkBuddy thin entries: {len(ALIASES)} generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
