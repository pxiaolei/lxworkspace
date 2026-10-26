#!/usr/bin/env python3
"""Write local config from a native window or legacy file, without secret output."""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[4]
FORM_KEYS = ("username", "password", "image_key", "wenda_token")
FOG_FIELDS = (
    ("username", ("lx_shujuku", "api", "username")),
    ("password", ("lx_shujuku", "api", "password")),
    ("image_key", ("lx_haibao", "image_api", "kie", "api_key")),
)
LABELS = {
    "username": "公司库账号",
    "password": "公司库密码",
    "image_key": "图片 Key",
    "token": "问答 token",
}
_TOKEN = re.compile(r'"token"\s*:\s*("(?:\\.|[^"\\])*"|null)')


def _clean(value):
    if not isinstance(value, str):
        return None
    if "\n" in value or "\r" in value or "\x00" in value:
        return None
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return text


def _values_from_mapping(data):
    if not isinstance(data, dict):
        return None, "表单格式无效"
    values = {}
    for key in FORM_KEYS:
        if key not in data:
            values[key] = ""
            continue
        cleaned = _clean(data[key])
        if cleaned is None:
            return None, "表单字段无效"
        values[key] = cleaned
    return values, None


def _load_form(path, kind):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None, "无法读取表单文件"
    if kind == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None, "表单 JSON 无法读取"
        return _values_from_mapping(data)
    values = {key: "" for key in FORM_KEYS}
    seen = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            return None, "表单格式无效"
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if key not in values:
            continue
        if key in seen:
            return None, "表单字段重复"
        seen.add(key)
        cleaned = _clean(raw.strip())
        if cleaned is None:
            return None, "表单字段无效"
        values[key] = cleaned
    return values, None


def parse_args(argv):
    source = None
    kind = None
    root = None
    gui = False
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--gui":
            if gui:
                return None, "参数重复"
            gui = True
            index += 1
            continue
        if arg not in {"--from-json", "--env-file", "--root"}:
            return None, "只接受 --gui、--from-json、--env-file 和 --root"
        if index + 1 >= len(argv):
            return None, "缺少参数值"
        value = argv[index + 1]
        index += 2
        if arg == "--root":
            if root is not None:
                return None, "参数重复"
            root = value
            continue
        if value.lstrip().startswith("{"):
            return None, "不接受把表单内容写在命令参数里，请改用文件"
        if source is not None:
            return None, "请只提供一种表单文件"
        source = value
        kind = "json" if arg == "--from-json" else "env"
    if gui and source is not None:
        return None, "本机窗口和文件导入不能同时使用"
    if source is None and not gui:
        return None, "请使用 --gui，或提供 --from-json / --env-file"
    return {"source": source, "kind": kind, "root": root, "gui": gui}, None


def _unsafe(root, path):
    root, path = root.absolute(), path.absolute()
    if root.is_symlink() or root not in path.parents:
        return True
    if path.is_symlink():
        return True
    current = path.parent
    while True:
        if current.is_symlink():
            return True
        if current == root or current.parent == current:
            break
        current = current.parent
    return False


def _line_parts(line):
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _deeper(lines, index, indent):
    for later in lines[index + 1:]:
        body, _ending = _line_parts(later)
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return len(body) - len(body.lstrip(" ")) > indent
    return False


def _scalar(rest):
    text = rest.strip()
    if text == "" or text == "~" or text.lower() in {"null", "none"}:
        return "empty", ""
    if text[0] in {'"', "'"}:
        quote = text[0]
        chars = []
        cursor = 1
        while cursor < len(text):
            char = text[cursor]
            if quote == "'" and char == "'" and cursor + 1 < len(text) and text[cursor + 1] == "'":
                chars.append("'")
                cursor += 2
                continue
            if quote == '"' and char == "\\" and cursor + 1 < len(text):
                chars.append(text[cursor + 1])
                cursor += 2
                continue
            if char == quote:
                tail = text[cursor + 1:].strip()
                if tail and not tail.startswith("#"):
                    return "complex", ""
                value = "".join(chars)
                return ("empty" if value.strip() == "" else "value"), tail if tail.startswith("#") else ""
            chars.append(char)
            cursor += 1
        return "complex", ""
    if "#" in text:
        before, comment = text.split("#", 1)
        token = before.strip()
        comment = "#" + comment
        if token == "" or token == "~" or token.lower() in {"null", "none"}:
            return "empty", comment
        return "value", ""
    return "value", ""


def _yaml_quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _collect(text):
    lines = text.splitlines(keepends=True)
    found = {}
    stack = []
    for index, line in enumerate(lines):
        body, ending = _line_parts(line)
        stripped = body.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        indent = len(body) - len(body.lstrip(" "))
        name, separator, rest = stripped.partition(":")
        key = name.strip()
        if not separator or not key or any(char.isspace() for char in key) or key[0] in {'"', "'"}:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if rest.strip() == "" and _deeper(lines, index, indent):
            kind, comment = "nested", ""
        else:
            kind, comment = _scalar(rest)
        found.setdefault(tuple(item[1] for item in stack), []).append({
            "index": index,
            "kind": kind,
            "comment": comment,
            "indent": body[:indent],
            "key": key,
            "ending": ending,
        })
    return lines, found


def fill_yaml(text, assignments):
    """Fill empty scalar paths. assignments is a list of (path, value, field_name)."""
    lines, found = _collect(text)
    statuses = {}
    changed = False
    for path, value, field_name in assignments:
        matches = found.get(path, [])
        if len(matches) != 1 or matches[0]["kind"] in {"nested", "complex"}:
            statuses[field_name] = "blocked"
            continue
        match = matches[0]
        if match["kind"] == "value":
            statuses[field_name] = "kept"
            continue
        if match["kind"] != "empty":
            statuses[field_name] = "blocked"
            continue
        if not value:
            statuses[field_name] = "missing"
            continue
        comment = (" " + match["comment"]) if match["comment"] else ""
        lines[match["index"]] = "%s%s: %s%s%s" % (match["indent"], match["key"], _yaml_quote(value), comment, match["ending"])
        statuses[field_name] = "written"
        changed = True
    return "".join(lines), statuses, changed


def fill_wenda(text, token):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text, "blocked", False
    if not isinstance(data, dict) or "token" not in data:
        return text, "blocked", False
    current = data.get("token")
    if isinstance(current, str) and current.strip():
        return text, "kept", False
    if current is None or (isinstance(current, str) and current.strip() == ""):
        if not token:
            return text, "missing", False
        matches = list(_TOKEN.finditer(text))
        if len(matches) != 1:
            return text, "blocked", False
        raw = matches[0].group(1)
        decoded = None if raw == "null" else json.loads(raw)
        if decoded not in ("", None):
            return text, "kept", False
        start, end = matches[0].span(1)
        updated = text[:start] + json.dumps(token, ensure_ascii=False) + text[end:]
        return updated, "written", True
    return text, "blocked", False


def _write_private(path, text):
    temporary = path.with_name(path.name + ".tmp")
    handle = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as file:
            file.write(text)
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _blocked_section(fields):
    return {"action": "blocked", "fields": {name: "blocked" for name in fields}}, True


def apply_fog(root, values):
    path = root / "config" / "fog_config.yaml"
    names = [name for name, _path in FOG_FIELDS]
    if _unsafe(root, path):
        return _blocked_section(names)
    assignments = [(field_path, values[name], name) for name, field_path in FOG_FIELDS]
    supplied = any(values[name] for name in names)
    if not path.exists():
        if not supplied:
            return {"action": "absent", "fields": {name: "missing" for name in names}}, False
        example = root / "config" / "fog_config.yaml.example"
        if not example.is_file() or example.is_symlink():
            return _blocked_section(names)
        try:
            example_text = example.read_bytes().decode("utf-8")
        except OSError:
            return _blocked_section(names)
        updated, statuses, _changed = fill_yaml(example_text, assignments)
        _write_private(path, updated)
        return {"action": "created", "fields": statuses}, False
    if not path.is_file():
        return _blocked_section(names)
    try:
        original = path.read_bytes().decode("utf-8")
    except OSError:
        return _blocked_section(names)
    updated, statuses, changed = fill_yaml(original, assignments)
    if changed and updated != original:
        _write_private(path, updated)
        action = "updated"
    else:
        action = "unchanged"
    return {"action": action, "fields": statuses}, False


def apply_wenda(root, token):
    path = root / "config" / "lx-wenda.json"
    if _unsafe(root, path):
        return _blocked_section(("token",))
    if not path.exists():
        if not token:
            return {"action": "absent", "fields": {"token": "missing"}}, False
        example = root / "config" / "lx-wenda.json.example"
        if not example.is_file() or example.is_symlink():
            return _blocked_section(("token",))
        try:
            example_text = example.read_bytes().decode("utf-8")
        except OSError:
            return _blocked_section(("token",))
        updated, status, _changed = fill_wenda(example_text, token)
        if status == "blocked":
            return _blocked_section(("token",))
        _write_private(path, updated)
        return {"action": "created", "fields": {"token": status}}, False
    if not path.is_file():
        return _blocked_section(("token",))
    try:
        original = path.read_bytes().decode("utf-8")
    except OSError:
        return _blocked_section(("token",))
    updated, status, changed = fill_wenda(original, token)
    if status == "blocked":
        return {"action": "blocked", "fields": {"token": "blocked"}}, True
    if changed and updated != original:
        _write_private(path, updated)
        action = "updated"
    else:
        action = "unchanged"
    return {"action": action, "fields": {"token": status}}, False


def _summary(fog, wenda):
    statuses = dict(fog["fields"])
    statuses["token"] = wenda["fields"]["token"]
    written = [LABELS[name] for name in ("username", "password", "image_key", "token") if statuses.get(name) == "written"]
    missing = [LABELS[name] for name in ("username", "password", "image_key", "token") if statuses.get(name) == "missing"]
    blocked = [LABELS[name] for name in ("username", "password", "image_key", "token") if statuses.get(name) == "blocked"]
    parts = []
    if written:
        parts.append("已写入" + "、".join(written))
    if missing:
        parts.append("仍缺" + "、".join(missing))
    if blocked:
        parts.append("未能安全写入" + "、".join(blocked))
    if not parts:
        parts.append("必填项已齐")
    return "；".join(parts)


def source_managed(root):
    # Even a damaged source marker must not send private credentials to the
    # generic shared-workspace initializer. No credential file is inspected.
    return os.path.lexists(root / "config/distribution-manifest.json")


def managed_report():
    return {"status": "managed", "summary": "私人源仓配置由底稿管理；未读取或修改凭据，请按 AGENT.md §3.1 维护"}


def wenda_enabled(root):
    marker = root / "config/distribution.json"
    try:
        if _unsafe(root, marker) or not marker.is_file():
            return False
        identity = json.loads(marker.read_text(encoding="utf-8"))
        return (identity.get("version") == 1 and identity.get("kind") in ("FOG", "lxworkspace")
                and identity.get("repository") == "pxiaolei/" + identity["kind"]
                and isinstance(identity.get("skills"), list) and "lx-wenda" in identity["skills"]
                and any(os.path.lexists(root / "config" / name) for name in ("lx-wenda.json", "lx-wenda.json.example")))
    except (OSError, ValueError, AttributeError):
        return False


def apply_local_config(root, values):
    if source_managed(root):
        return managed_report(), False
    fog, fog_unsafe = apply_fog(root, values)
    if wenda_enabled(root):
        wenda, wenda_unsafe = apply_wenda(root, values.get("wenda_token", ""))
    else:
        wenda, wenda_unsafe = {"action": "not-enabled", "fields": {"token": "not-enabled"}}, False
    report = {"fog_config.yaml": fog, "lx-wenda.json": wenda, "summary": _summary(fog, wenda)}
    return report, fog_unsafe or wenda_unsafe


def gui_plan(root, values):
    """Read-only field states and expected output bytes, kept only in memory."""
    if source_managed(root):
        return [], {}
    def read(name):
        path = root / "config" / name
        source = path if path.exists() or path.is_symlink() else path.with_name(name + ".example")
        if _unsafe(root, path) or _unsafe(root, source) or not source.is_file():
            raise ValueError("配置路径不可用")
        return path, source.read_bytes().decode("utf-8")

    path, text = read("fog_config.yaml")
    updated, statuses, _changed = fill_yaml(text, [(p, values.get(name, ""), name) for name, p in FOG_FIELDS])
    expected = {path: updated}
    names = [(name, LABELS[name]) for name, _path in FOG_FIELDS]
    if wenda_enabled(root):
        path, text = read("lx-wenda.json")
        updated, status, _changed = fill_wenda(text, values.get("wenda_token", ""))
        expected[path] = updated
        statuses["wenda_token"] = status
        names.append(("wenda_token", LABELS["token"]))
    if "blocked" in statuses.values():
        raise ValueError("配置格式不可用")
    fields = [{"name": name, "label": label, "secret": name != "username", "status": statuses[name]} for name, label in names]
    return fields, expected


def open_config_window(root, fields):
    from local_config_window import collect_values
    return collect_values(root, fields)


def run_gui(root):
    phase = "inspect"
    try:
        if source_managed(root):
            print(json.dumps(managed_report(), ensure_ascii=False))
            return 0
        fields, _expected = gui_plan(root, {})
        phase = "open-window"
        values = open_config_window(root, fields)
        if values is None:
            print(json.dumps({"status": "cancelled", "summary": "已取消，未写入配置"}, ensure_ascii=False))
            return 1
        phase = "validate"
        if not isinstance(values, dict) or any(not isinstance(v, str) or any(c in v for c in ("\n", "\r", "\x00")) for v in values.values()):
            raise ValueError("输入格式无效")
        values = {key: values.get(key, "") for key in FORM_KEYS}
        fields, expected = gui_plan(root, values)
        if any(f["status"] == "missing" for f in fields):
            raise ValueError("必填项未齐")
        phase = "save"
        report, unsafe = apply_local_config(root, values)
        if unsafe:
            raise ValueError("配置写入失败")
        phase = "readback"
        if any(_unsafe(root, path) or path.read_bytes() != text.encode("utf-8") for path, text in expected.items()):
            raise ValueError("保存结果不一致")
        print(json.dumps({"status": "saved", "summary": report["summary"], "readback": "verified"}, ensure_ascii=False))
        return 0
    except Exception:
        # Never echo GUI inputs, file contents or raw exception text.
        print(json.dumps({"status": "error", "phase": phase, "summary": "本机窗口或配置保存失败，请联系对接人；不要将凭据发到聊天或改用记事本填写。"}, ensure_ascii=False))
        return 2


def main(argv=None):
    parsed, error = parse_args(list(sys.argv[1:] if argv is None else argv))
    if error:
        print(error)
        return 2
    root = Path(parsed["root"]).expanduser() if parsed["root"] else ROOT
    if not root.is_dir() or root.is_symlink():
        print("找不到工作区")
        return 2
    if source_managed(root):
        print(json.dumps(managed_report(), ensure_ascii=False))
        return 0
    if parsed["gui"]:
        return run_gui(root)
    form = Path(parsed["source"])
    if not form.is_file() or form.is_symlink():
        print("找不到表单文件")
        return 2
    values, error = _load_form(form, parsed["kind"])
    if error:
        print(error)
        return 2
    report, unsafe = apply_local_config(root, values)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if unsafe else 0


if __name__ == "__main__":
    raise SystemExit(main())
