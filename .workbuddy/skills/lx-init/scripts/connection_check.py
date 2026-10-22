"""统一配置检查。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any


@dataclass
class CheckItem:
    name: str
    status: str
    message: str


def check_config(config: dict[str, Any], project_root: Path) -> list[CheckItem]:
    """检查统一配置的完整性，不做写入。"""
    items: list[CheckItem] = []
    enabled = config.get("enabled_skills", {})

    if enabled.get("lx_zhutichaibiao") and not enabled.get("lx_shujuku"):
        items.append(CheckItem(
            name="enabled_skills",
            status="error",
            message="lx_zhutichaibiao 依赖 lx_shujuku，请同时启用 lx_shujuku",
        ))

    if enabled.get("lx_dapanribao") and not enabled.get("lx_shujuku"):
        items.append(CheckItem(
            name="enabled_skills.lx_shujuku",
            status="error",
            message="lx_dapanribao 依赖 lx_shujuku 查询日报数据，请同时启用 lx_shujuku",
        ))

    if enabled.get("lx_shujuku"):
        api = config.get("lx_shujuku", {}).get("api", {})
        _required(items, "lx_shujuku.api.base_url", api.get("base_url"), severity="error")
        _required(items, "lx_shujuku.api.username", api.get("username"), severity="error")
        _required(items, "lx_shujuku.api.password", api.get("password"), sensitive=True, severity="error")
    else:
        items.append(CheckItem("lx_shujuku", "skipped", "未启用"))

    if enabled.get("lx_zhutichaibiao"):
        zhutichaibiao = config.get("lx_zhutichaibiao", {})
        _required(items, "lx_zhutichaibiao.work_dir", zhutichaibiao.get("work_dir"), severity="error")
        persons = zhutichaibiao.get("default_persons", [])
        if not persons:
            items.append(CheckItem(
                "lx_zhutichaibiao.default_persons",
                "warning",
                "未配置默认对接人",
            ))
    else:
        items.append(CheckItem("lx_zhutichaibiao", "skipped", "未启用"))

    if enabled.get("lx_dapanribao"):
        dailyreport = config.get("lx_dapanribao", {})
        _required(items, "lx_dapanribao.default_person", dailyreport.get("default_person"))
        _required(items, "lx_dapanribao.output_dir", dailyreport.get("output_dir"))
        _required(items, "lx_dapanribao.publish_backend", dailyreport.get("publish_backend"))
        _required(items, "lx_dapanribao.report_title_template", dailyreport.get("report_title_template"))
        publish_backend = str(dailyreport.get("publish_backend") or "")
        if publish_backend == "lx-feishudocs":
            _check_lx_feishudocs(items, config, required=True)
            root_folder, source = _dailyreport_root_folder(config, dailyreport)
            if root_folder:
                items.append(CheckItem("lx_dapanribao.feishu_root_folder", "ok", f"已配置或继承: {source}"))
            else:
                items.append(CheckItem(
                    "lx_dapanribao.feishu_root_folder",
                    "warning",
                    "未配置；可填写 lx_dapanribao.feishu_root_folder_url/token，或复用 lx_nongfu.operator_doc.contact_person_root_folders",
                ))
    else:
        items.append(CheckItem("lx_dapanribao", "skipped", "未启用"))

    if enabled.get("lx_haibao"):
        haibao = config.get("lx_haibao", {})
        image_api = haibao.get("image_api", {}) if isinstance(haibao, dict) else {}
        configured = False
        if isinstance(image_api, dict):
            for provider_name in (
                image_api.get("provider_primary", "volcengine_seedream"),
                image_api.get("provider_fallback", "apimart"),
            ):
                provider = image_api.get(str(provider_name), {})
                if isinstance(provider, dict) and provider.get("api_key"):
                    configured = True
        if os.environ.get("ARK_API_KEY") or os.environ.get("APIMART_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            configured = True
        if configured:
            items.append(CheckItem("lx_haibao.image_api", "ok", "至少一个图片 provider 已配置 API Key"))
        else:
            items.append(CheckItem("lx_haibao.image_api", "warning", "未配置图片 API Key；海报生成不可用"))
    else:
        items.append(CheckItem("lx_haibao", "skipped", "未启用"))

    if enabled.get("lx_feishudocs"):
        _check_lx_feishudocs(items, config, required=True)
    else:
        items.append(CheckItem("lx_feishudocs", "skipped", "未启用"))

    if not items:
        items.append(CheckItem("config", "ok", "配置结构可用"))
    items.extend(_check_sensitive_file_permissions(project_root))
    return items


def has_errors(items: list[CheckItem]) -> bool:
    return any(item.status == "error" for item in items)


def _required(
    items: list[CheckItem],
    name: str,
    value: Any,
    sensitive: bool = False,
    severity: str = "warning",
) -> None:
    if value:
        message = "已配置" if not sensitive else "已配置（不显示）"
        items.append(CheckItem(name, "ok", message))
    else:
        items.append(CheckItem(name, severity, "未配置"))


def _workbuddy_lark_cli_path() -> Path:
    return (
        Path.home()
        / ".workbuddy"
        / "binaries"
        / "node"
        / "cli-connector-packages"
        / "lib"
        / "node_modules"
        / "@larksuite"
        / "cli"
        / "bin"
        / "lark-cli"
    )


def _root_folder_from_contact_config(operator_doc: dict[str, Any], contact_person: str) -> str:
    mapping = operator_doc.get("contact_person_root_folders")
    if isinstance(mapping, dict) and contact_person:
        value = mapping.get(contact_person)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return str(value.get("url") or value.get("token") or "").strip()
    return ""


def _dailyreport_root_folder(config: dict[str, Any], dailyreport: dict[str, Any]) -> tuple[str, str]:
    explicit = str(
        dailyreport.get("feishu_root_folder_url")
        or dailyreport.get("feishu_root_folder_token")
        or ""
    ).strip()
    if explicit:
        return explicit, "lx_dapanribao"

    nongfu = config.get("lx_nongfu", {})
    if not isinstance(nongfu, dict):
        return "", ""
    operator_doc = nongfu.get("operator_doc", {})
    if not isinstance(operator_doc, dict):
        return "", ""

    contact_person = str(dailyreport.get("default_person") or "").strip()
    inherited = _root_folder_from_contact_config(operator_doc, contact_person)
    if inherited:
        return inherited, "lx_nongfu.operator_doc.contact_person_root_folders"

    fallback = str(
        operator_doc.get("operator_root_folder_url")
        or operator_doc.get("operator_root_folder_token")
        or ""
    ).strip()
    if fallback:
        return fallback, "lx_nongfu.operator_doc.operator_root_folder"
    return "", ""


def _check_lx_feishudocs(items: list[CheckItem], config: dict[str, Any], required: bool) -> None:
    skill_path = Path(__file__).resolve().parents[2] / "lx-feishudocs" / "SKILL.md"
    severity = "error" if required else "warning"
    if skill_path.exists():
        items.append(CheckItem("skill.lx-feishudocs", "ok", f"已安装: {skill_path}"))
    else:
        items.append(CheckItem("skill.lx-feishudocs", severity, f"未找到: {skill_path}"))

    feishu = config.get("lx_feishudocs", {})
    if not isinstance(feishu, dict):
        feishu = {}
    cli_path = str(feishu.get("cli_path") or "").strip()
    candidates = [
        Path(cli_path).expanduser() if cli_path else None,
        _workbuddy_lark_cli_path(),
    ]
    if any(path is not None and path.exists() for path in candidates):
        items.append(CheckItem("lx_feishudocs.lark_cli", "ok", "已找到 lark-cli"))
    else:
        items.append(CheckItem("lx_feishudocs.lark_cli", severity, "未找到 lark-cli；请在 WorkBuddy 安装飞书连接器"))

    spreadsheet_type = feishu.get("spreadsheet_type", "sheets")
    if spreadsheet_type == "sheets":
        items.append(CheckItem("lx_feishudocs.spreadsheet_type", "ok", "使用飞书普通电子表格"))
    else:
        items.append(CheckItem("lx_feishudocs.spreadsheet_type", "error", "必须是 sheets；当前需求不使用 Base/智能表格"))


def _check_sensitive_file_permissions(project_root: Path) -> list[CheckItem]:
    """检查敏感配置文件权限，不读取文件内容。"""
    results: list[CheckItem] = []
    paths = [
        project_root / "config" / "fog_config.yaml",
        project_root / "config" / "personal_config.yaml",
    ]
    paths.extend((project_root / ".workbuddy" / "skills").glob("*/assets/*cache*.json"))
    paths.extend((project_root / ".workbuddy" / "skills").glob("*/assets/entity_cache.json"))

    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        if not path.exists():
            continue
        mode = path.stat().st_mode & 0o777
        name = f"permissions.{_rel(path, project_root)}"
        if mode & 0o077:
            results.append(CheckItem(
                name=name,
                status="warning",
                message=f"权限 {mode:o} 过宽，建议 chmod 600",
            ))
        else:
            results.append(CheckItem(
                name=name,
                status="ok",
                message=f"权限 {mode:o}",
            ))
    return results


def _rel(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)
