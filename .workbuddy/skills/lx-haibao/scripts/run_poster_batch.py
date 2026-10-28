#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from common import as_list, load_brands, load_templates, resolve_path, route_name, skill_root
from qr_verify import build_result as verify_qr


def _find_skills_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "lxx_share").is_dir():
            return parent
    return Path(__file__).resolve().parents[2]


_SKILLS_DIR = _find_skills_dir()
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from lxx_share.fog_config import get_section, resolve_project_path as resolve_fog_path  # noqa: E402

logger = logging.getLogger("lx-haibao")


DEFAULT_POSTER_OUTPUT_DIR = "workspace/09端外海报图/产出图"
DEFAULT_POSTER_META_DIR = "workspace/09端外海报图/元数据"
DEFAULT_POSTER_TMP_DIR = "workspace/09端外海报图/临时图"
DEFAULT_POSTER_LOG_DIR = "workspace/09端外海报图/处理日志"
DEFAULT_PROVIDER_SMOKE_DIR = "workspace/09端外海报图/临时图/provider-smoke"
ASSET_MODE_INTEGRATED = "integrated"
ASSET_MODE_HYBRID = "hybrid"
ASSET_MODE_OVERLAY = "overlay"
DEFAULT_ASSET_MODE = ASSET_MODE_HYBRID
SIZE_POLICY_AUTO = "auto"
SIZE_POLICY_FIXED = "fixed"
DEFAULT_SIZE_POLICY = SIZE_POLICY_AUTO
DEFAULT_LONG_POSTER_SIZE = "1:2"
COMMON_CJK_FONT_PATHS = (
    # macOS
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
    "C:/Windows/Fonts/Dengb.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
)


REQUIRED_RUNTIME_MODULES = {
    "requests": "requests",
    "Pillow": "PIL",
    "zxing-cpp": "zxingcpp",
}

COMMON_POSTER_RULES = """用户本次提供的 TXT 中，所有活动模块默认都要展示；"全量活动"属于正常展示模块。
只排除内部属性、历史/过期/已结束/仅供参考/明确标记不展示的内容。
不得引用模板图、旧海报、历史输出或其他品牌活动里的日期、城市、价格、奖励和规则。
TXT 没有的活动模块不展示，不写"无""暂无""无卡券"。
活动模块数量不固定：TXT 有几个活动类型就展示几个；不要为了凑模板预设区块而新增空模块、合成模块或替换模块标题。
日期、星期、时间段、金额、奖励、单量和门槛必须逐字按 TXT 展示；TXT 中已给出星期时，不得自行推算、改写或顺延星期。
免佣卡价格必须按每个日期独立逐项抄写；不得把前一天价格复用到后一天，也不得为了排版省略、合并或改写金额。
不得添加 TXT 未要求展示的温馨提示、活动说明、"以APP内显示为准"、安全出行提示或其他兜底文案。
可展示模块只来自当前 TXT 的【】标题和对应内容；不得从品牌风格、模板示例、历史海报或上一轮任务补充额外权益模块。
只有 TXT 明确出现【全量活动】标题时才展示"全量活动"模块；"周卡"属于所在原模块，不得单独改写成全量活动。
模块标题不展示 1/2/3/4/5/6 等序号徽标，统一使用纯模块标题。
海报文案不得展示"共补""共补免佣""平台共补""是否共补"等内部补贴属性。
卡券按日期展示，每个日期内先分"全天卡"和"时段卡"，时段卡按时间从早到晚。
新人免佣奖只展示免佣天数，不展示适用订单；新人成长奖才展示首单奖励、X天完成X单奖励等任务规则。
不要生成替代二维码、假二维码、二维码纹理、条形码或抽象扫码图案。"""

ACTIVITY_LAYOUT_RULES = """活动排版规则：
- 以 TXT 中的真实活动类型作为模块边界；常见模块和新增模块都按独立活动卡片处理。
- 活动类型较少时，收紧中部活动区，不保留空白预设卡片。
- 活动类型较多时，纵向增加活动卡片和海报高度，优先拉长画幅，不压缩到难以阅读。
- 脚本会在成图下方追加扫码下载 footer 并贴入真实二维码；模型不要在内容区内生成二维码、扫码卡片或底栏 CTA。"""

REFERENCE_IMAGE_RULES = """模板示例图中的"品牌占位""城市名称占位""二维码占位区""X元"以及旧日期、旧金额、旧奖励都是占位或旧内容，不得原样保留到新海报。品牌 Logo 只来自真实 Logo 参考图；二维码按 asset_mode 由真实二维码参考图生成或由脚本贴入。"""

TEXT_TO_IMAGE_RULES = """本次不传模板图，只按结构化版式说明生成；不要显示任何模板说明、坐标、占位标签、示例价格、旧日期、旧金额或伪二维码文案。text-to-image 不适用于默认真实 Logo 融合链路；正式成品应使用模板+Logo参考图生成，二维码由脚本贴入并验真。"""

CITY_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,12}(?:市|县|区|州|盟)")
SECTION_HEADING_PATTERN = re.compile(r"^【\s*(.+?)\s*】\s*$")
EXCLUDED_SECTION_PATTERNS = (
    ("共补/平台共补", re.compile(r"共补|平台共补|是否共补")),
    ("历史/过期/已结束/仅供参考/明确不展示", re.compile(r"历史|过期|已结束|仅供参考|不展示")),
)
META_SECTION_TITLE_PATTERN = re.compile(r"^(品牌|城市|日期|活动日期|说明|备注|不展示内容)$")
GENERATION_NOTES_SECTION_TITLE_PATTERN = re.compile(r"^(生成要求|版式要求|设计要求|视觉要求|海报要求)$")
EXCLUDED_LINE_PATTERNS = (
    ("共补/平台共补", re.compile(r"共补|平台共补|是否共补")),
    ("历史/过期/已结束/仅供参考/明确不展示", re.compile(r"历史|过期|已结束|仅供参考|不展示")),
)


def read_text_guess(path: Path) -> str:
    encodings = ("utf-8-sig", "utf-8", "gb18030")
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"无法读取 TXT 文件编码：{path}，最后错误：{last_error}")


def collect_input_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for file_arg in args.file or []:
        paths.append(Path(file_arg).expanduser().resolve())
    if args.dir:
        folder = Path(args.dir).expanduser().resolve()
        if not folder.is_dir():
            raise SystemExit(f"ERROR: directory not found: {folder}")
        paths.extend(sorted(folder.glob("*.txt")))
    return paths


def extract_city_from_text(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "📍" in stripped or "城市" in stripped:
            match = CITY_PATTERN.search(stripped)
            if match:
                return match.group(0)
    match = CITY_PATTERN.search(text)
    return match.group(0) if match else None


def extract_city_from_filename(path: Path, brand: dict[str, Any]) -> str:
    stem = path.stem
    text = stem
    for keyword in sorted((str(item) for item in as_list(brand.get("filename_keywords"))), key=len, reverse=True):
        text = text.replace(keyword, "")
    text = re.sub(r"^[\s_\-—]+|[\s_\-—]+$", "", text)
    parts = [part for part in re.split(r"[\s_\-—]+", text) if part]
    for part in parts:
        if re.search(r"[\u4e00-\u9fff].*(市|县|区|州|盟)$", part):
            return part
    for part in parts:
        if re.search(r"[\u4e00-\u9fff]", part) and not re.search(r"\d{4}", part):
            return part
    return text or stem


def resolve_city(
    path: Path,
    brand: dict[str, Any],
    *,
    txt_content: str | None = None,
    city_override: str | None = None,
) -> tuple[str, str]:
    if city_override:
        return city_override.strip(), "arg"
    if txt_content:
        city_from_text = extract_city_from_text(txt_content)
        if city_from_text:
            return city_from_text, "txt"
    return extract_city_from_filename(path, brand), "filename"


def safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)[:80] or "poster"


def import_errors() -> list[str]:
    errors: list[str] = []
    for package, module_name in REQUIRED_RUNTIME_MODULES.items():
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - report the exact runtime import failure.
            errors.append(f"{package}: {exc}")
    return errors


def output_dirs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    config = get_section("lx_haibao", Path(__file__))

    output_value = args.output_dir or os.environ.get("POSTER_OUTPUT_DIR") or config.get("output_dir")
    meta_value = os.environ.get("POSTER_META_DIR") or config.get("meta_dir")
    tmp_value = os.environ.get("POSTER_TMP_DIR") or config.get("tmp_dir")

    output = resolve_fog_path(output_value or DEFAULT_POSTER_OUTPUT_DIR, Path(__file__))
    meta = resolve_fog_path(meta_value or DEFAULT_POSTER_META_DIR, Path(__file__))
    tmp = resolve_fog_path(tmp_value or DEFAULT_POSTER_TMP_DIR, Path(__file__))
    output.mkdir(parents=True, exist_ok=True)
    meta.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return output.resolve(), meta.resolve(), tmp.resolve()


def log_dir() -> Path:
    config = get_section("lx_haibao", Path(__file__))
    log_value = os.environ.get("POSTER_LOG_DIR") or config.get("log_dir") or DEFAULT_POSTER_LOG_DIR
    path = resolve_fog_path(log_value, Path(__file__))
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def configure_logging(*, persistent: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if persistent:
        run_log_dir = log_dir()
        handlers.append(
            logging.FileHandler(
                run_log_dir / f"{datetime.now().strftime('%Y%m%d')}.log",
                encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def select_template(template_id: str | None = None) -> dict[str, Any]:
    templates = load_templates(skill_root())
    if not templates:
        raise RuntimeError("assets/templates/templates.yaml 中没有可用模板。")
    if template_id:
        for template in templates:
            if template["template_id"] == template_id:
                return template
        available = ", ".join(template["template_id"] for template in templates)
        raise RuntimeError(f"未知模板：{template_id}。可用模板：{available}")
    return templates[0]


def brand_preferred_template_id(brand: dict[str, Any]) -> str:
    template_config = brand.get("template")
    if not isinstance(template_config, dict):
        return ""
    return str(template_config.get("preferred_template_id") or "").strip()


def select_template_for_brand(
    brand: dict[str, Any],
    *,
    forced_template_id: str | None,
    default_template: dict[str, Any],
) -> dict[str, Any]:
    if forced_template_id:
        return default_template
    preferred_id = brand_preferred_template_id(brand)
    if preferred_id:
        return select_template(preferred_id)
    return default_template


def template_for_row(
    row: dict[str, Any],
    *,
    default_template: dict[str, Any],
    templates_by_brand_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return templates_by_brand_id.get(str(row.get("brand_id") or ""), default_template)


def build_rows(paths: list[Path], brands: list[dict[str, Any]], city_override: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    brands_by_id = {str(brand["brand_id"]): brand for brand in brands}
    for path in paths:
        row = route_name(path.name, brands)
        row["path"] = str(path)
        if row["status"] == "supported":
            brand = brands_by_id[str(row["brand_id"])]
            try:
                txt_content = read_text_guess(path)
            except Exception:
                txt_content = None
            row["city"], row["city_source"] = resolve_city(
                path,
                brand,
                txt_content=txt_content,
                city_override=city_override,
            )
            filename_template = brand.get("output", {}).get("filename_template") or "{brand}-{city}-{timestamp}.png"
            row["output_name"] = str(filename_template).format(brand=brand["canonical_name"], city=row["city"], timestamp="YYYYMMDD_HHMMSS")
        rows.append(row)

    sample_by_brand: dict[str, str] = {}
    for row in rows:
        if row["status"] == "supported":
            sample_by_brand.setdefault(str(row["brand_id"]), str(row["path"]))
    for row in rows:
        if row["status"] == "supported":
            sample_path = sample_by_brand[str(row["brand_id"])]
            row["sample_file"] = Path(sample_path).name
            row["is_sample"] = str(row["path"]) == sample_path
    return rows


def template_layout_description(template: dict[str, Any]) -> str:
    root = skill_root()
    layout_raw = template.get("layout_description_path")
    if layout_raw:
        layout_path = resolve_path(str(layout_raw), root)
        if layout_path.is_file():
            return read_text_guess(layout_path).strip()
    template_id = str(template.get("template_id") or "template")
    return (
        f"竖版 9:16 司机活动海报，使用 {template_id} 的卡片式运营海报结构："
        "顶部城市道路和车辆主视觉，中部活动权益卡片矩阵，底部扫码加入服务横条。"
        "顶部品牌区和底部扫码区要与整张海报自然融合；不要使用模板旧品牌、旧二维码或占位内容。"
    )


def reference_paths(
    brand: dict[str, Any],
    template: dict[str, Any],
    *,
    use_reference_images: bool = True,
    asset_mode: str = DEFAULT_ASSET_MODE,
) -> list[Path]:
    if not use_reference_images:
        return []
    root = skill_root()
    paths = [
        resolve_path(template["example_path"], root),
    ]
    assets = brand.get("assets") if isinstance(brand.get("assets"), dict) else {}
    if asset_mode in {ASSET_MODE_HYBRID, ASSET_MODE_INTEGRATED}:
        paths.append(resolve_path(str(assets.get("logo_path") or ""), root))
    if asset_mode == ASSET_MODE_INTEGRATED:
        paths.extend(
            [
                resolve_path(str(assets.get("qr_path") or ""), root),
            ]
        )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("参考图缺失：" + "；".join(missing))
    return paths


def _ratio_value(config: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _rgba_from_hex(value: Any) -> tuple[int, int, int, int] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) == 3:
        raw = "".join(item * 2 for item in raw)
    if len(raw) != 6:
        return None
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
    except ValueError:
        return None
    return (r, g, b, 255)


def _footer_fill_from_config(brand: dict[str, Any], overlay_config: dict[str, Any]) -> tuple[int, int, int, int] | None:
    configured = _rgba_from_hex(overlay_config.get("footer_color") or overlay_config.get("footer_fill"))
    if configured:
        return configured
    display = brand.get("display") if isinstance(brand.get("display"), dict) else {}
    return _rgba_from_hex(display.get("footer_color"))


def _paste_image_in_box(
    *,
    poster: Any,
    asset_path: Path,
    box: dict[str, int],
    resample: Any,
    background: bool,
    padding: int,
) -> None:
    from PIL import Image, ImageDraw

    asset = Image.open(asset_path).convert("RGBA")
    width = max(1, int(box["width"]))
    height = max(1, int(box["height"]))
    scale = min(width / asset.width, height / asset.height)
    target_size = (max(1, int(asset.width * scale)), max(1, int(asset.height * scale)))
    resized = asset.resize(target_size, resample)
    x = int(box["x"] + (width - target_size[0]) / 2)
    y = int(box["y"] + (height - target_size[1]) / 2)

    if background:
        draw = ImageDraw.Draw(poster)
        draw.rounded_rectangle(
            (
                max(0, box["x"] - padding),
                max(0, box["y"] - padding),
                min(poster.width, box["x"] + width + padding),
                min(poster.height, box["y"] + height + padding),
            ),
            radius=max(8, padding),
            fill="white",
        )
    poster.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)


def _candidate_font_paths(font_path: str | None = None) -> list[str]:
    raw_paths = [
        font_path,
        os.environ.get("POSTER_FOOTER_FONT_PATH"),
        os.environ.get("POSTER_CJK_FONT_PATH"),
        *COMMON_CJK_FONT_PATHS,
    ]
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        path = str(raw_path or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def usable_cjk_font_paths(font_path: str | None = None) -> list[str]:
    return [path for path in _candidate_font_paths(font_path) if Path(path).is_file()]


def _font_error_message(font_path: str | None = None) -> str:
    checked = "；".join(_candidate_font_paths(font_path))
    return (
        "未找到可用中文字体，不能继续生成 footer 文案。"
        "请安装 Windows 微软雅黑/黑体/宋体、Linux Noto Sans CJK/文泉驿等中文字体，"
        "或设置 POSTER_CJK_FONT_PATH / POSTER_FOOTER_FONT_PATH 指向字体文件。"
        f" 已检查：{checked}"
    )


def _fit_font(text: str, font_path: str, *, max_width: int, max_height: int) -> Any:
    from PIL import ImageFont

    usable_paths = usable_cjk_font_paths(font_path)
    if not usable_paths:
        raise RuntimeError(_font_error_message(font_path))

    best_font: Any | None = None
    best_score: tuple[int, int] | None = None
    for size in range(max(12, max_height), 11, -2):
        for path in usable_paths:
            try:
                font = ImageFont.truetype(path, size)
            except OSError:
                continue
            left, top, right, bottom = font.getbbox(text)
            text_width = right - left
            text_height = bottom - top
            overflow = max(0, text_width - max_width) + max(0, text_height - max_height)
            if overflow == 0:
                return font
            score = (overflow, -size)
            if best_score is None or score < best_score:
                best_font = font
                best_score = score
    if best_font is not None:
        return best_font
    raise RuntimeError(_font_error_message(font_path))


def _draw_text_chip_logo(
    *,
    poster: Any,
    brand: dict[str, Any],
    box: dict[str, int],
    overlay_config: dict[str, Any],
    padding: int,
) -> dict[str, Any]:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(poster)
    text = str(
        overlay_config.get("text")
        or brand.get("display", {}).get("poster_brand_name")
        or brand.get("canonical_name")
        or ""
    ).strip()
    if not text:
        return {"applied": False, "error": "draw_text_chip_text_empty"}

    x0 = max(0, box["x"] - padding)
    y0 = max(0, box["y"] - padding)
    x1 = min(poster.width, box["x"] + box["width"] + padding)
    y1 = min(poster.height, box["y"] + box["height"] + padding)
    radius = max(12, int((y1 - y0) * 0.28))
    fill = str(overlay_config.get("fill") or "#FFFFFF")
    border = str(overlay_config.get("border") or "#DCEBFF")
    text_color = str(overlay_config.get("text_color") or "#078DFF")

    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=fill, outline=border, width=max(2, padding // 3 or 2))

    inner_padding = max(8, int((x1 - x0) * 0.10))
    font = _fit_font(
        text,
        str(overlay_config.get("font_path") or ""),
        max_width=max(1, x1 - x0 - inner_padding * 2),
        max_height=max(1, y1 - y0 - inner_padding),
    )
    left, top, right, bottom = font.getbbox(text)
    text_width = right - left
    text_height = bottom - top
    tx = x0 + (x1 - x0 - text_width) / 2 - left
    ty = y0 + (y1 - y0 - text_height) / 2 - top
    draw.text((tx, ty), text, font=font, fill=text_color)
    return {
        "applied": True,
        "method": "draw_text_chip_logo",
        "text": text,
        "box": {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]},
    }


def _draw_logo_lockup(
    *,
    poster: Any,
    brand: dict[str, Any],
    box: dict[str, int],
    overlay_config: dict[str, Any],
) -> dict[str, Any]:
    from PIL import ImageDraw

    icon_text = str(
        overlay_config.get("text")
        or brand.get("display", {}).get("poster_brand_name")
        or brand.get("canonical_name")
        or ""
    ).strip()
    wordmark_text = str(overlay_config.get("wordmark_text") or icon_text).strip()
    if not icon_text and not wordmark_text:
        return {"applied": False, "error": "draw_logo_lockup_text_empty"}

    draw = ImageDraw.Draw(poster)
    icon_size = max(32, min(box["height"], int(box["width"] * 0.36)))
    icon_x = box["x"]
    icon_y = box["y"] + int((box["height"] - icon_size) / 2)
    radius = max(8, int(icon_size * 0.18))
    icon_fill = str(overlay_config.get("icon_fill") or "#078DFF")
    icon_text_color = str(overlay_config.get("icon_text_color") or "#FFFFFF")
    wordmark_color = str(overlay_config.get("wordmark_color") or "#111111")

    shadow_offset = max(1, int(icon_size * 0.035))
    draw.rounded_rectangle(
        (icon_x + shadow_offset, icon_y + shadow_offset, icon_x + icon_size + shadow_offset, icon_y + icon_size + shadow_offset),
        radius=radius,
        fill=(0, 75, 180, 42),
    )
    draw.rounded_rectangle((icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), radius=radius, fill=icon_fill)

    icon_font = _fit_font(
        icon_text,
        str(overlay_config.get("font_path") or ""),
        max_width=max(1, int(icon_size * 0.78)),
        max_height=max(1, int(icon_size * 0.48)),
    )
    left, top, right, bottom = icon_font.getbbox(icon_text)
    text_width = right - left
    text_height = bottom - top
    icon_tx = icon_x + (icon_size - text_width) / 2 - left
    icon_ty = icon_y + (icon_size - text_height) / 2 - top
    draw.text((icon_tx, icon_ty), icon_text, font=icon_font, fill=icon_text_color)

    wordmark_x = icon_x + icon_size + max(10, int(icon_size * 0.18))
    wordmark_width = max(1, box["x"] + box["width"] - wordmark_x)
    wordmark_font = _fit_font(
        wordmark_text,
        str(overlay_config.get("font_path") or ""),
        max_width=wordmark_width,
        max_height=max(1, int(icon_size * 0.62)),
    )
    left, top, right, bottom = wordmark_font.getbbox(wordmark_text)
    wordmark_height = bottom - top
    wordmark_y = icon_y + (icon_size - wordmark_height) / 2 - top
    draw.text(
        (wordmark_x, wordmark_y),
        wordmark_text,
        font=wordmark_font,
        fill=wordmark_color,
        stroke_width=max(1, int(icon_size * 0.018)),
        stroke_fill=(255, 255, 255, 190),
    )
    return {
        "applied": True,
        "method": "draw_logo_lockup",
        "text": icon_text,
        "wordmark_text": wordmark_text,
        "box": {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]},
        "icon_box": {"x": icon_x, "y": icon_y, "size": icon_size},
    }


def overlay_poster_logo(poster_path: Path, brand: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image

    root = skill_root()
    overlay_config = brand.get("logo_overlay") if isinstance(brand.get("logo_overlay"), dict) else {}
    if not overlay_config:
        return {"applied": False, "error": "brand_logo_overlay_not_configured"}

    poster = Image.open(poster_path).convert("RGBA")
    width, height = poster.size
    box = {
        "x": int(width * _ratio_value(overlay_config, "x_ratio", 0.05)),
        "y": int(height * _ratio_value(overlay_config, "y_ratio", 0.025)),
        "width": int(width * _ratio_value(overlay_config, "width_ratio", 0.14)),
        "height": int(height * _ratio_value(overlay_config, "height_ratio", 0.07)),
    }
    padding = max(0, int(width * _ratio_value(overlay_config, "padding_ratio", 0.0)))
    if str(overlay_config.get("mode") or "").strip().lower() == "draw_text_chip":
        result = _draw_text_chip_logo(
            poster=poster,
            brand=brand,
            box=box,
            overlay_config=overlay_config,
            padding=padding,
        )
        if result.get("applied"):
            poster.convert("RGB").save(poster_path)
        return result
    if str(overlay_config.get("mode") or "").strip().lower() == "draw_logo_lockup":
        result = _draw_logo_lockup(
            poster=poster,
            brand=brand,
            box=box,
            overlay_config=overlay_config,
        )
        if result.get("applied"):
            poster.convert("RGB").save(poster_path)
        return result

    assets = brand.get("assets") if isinstance(brand.get("assets"), dict) else {}
    logo_raw = assets.get("logo_path")
    if not logo_raw:
        return {"applied": False, "error": "brand_logo_not_configured"}
    logo_path = resolve_path(str(logo_raw), root)
    if not logo_path.is_file():
        return {"applied": False, "error": f"brand_logo_not_found: {logo_path}"}
    _paste_image_in_box(
        poster=poster,
        asset_path=logo_path,
        box=box,
        resample=Image.Resampling.LANCZOS,
        background=bool(overlay_config.get("background", False)),
        padding=padding,
    )
    poster.convert("RGB").save(poster_path)
    return {"applied": True, "method": "source_logo_overlay", "logo_path": str(logo_path), "box": box}


def _detect_footer_blue_band(poster: Any) -> tuple[int, int] | None:
    width, height = poster.size
    sample_step = max(1, width // 180)
    min_blue_fraction = 0.44
    max_gap = max(8, int(height * 0.02))
    rows: list[int] = []

    for y in range(int(height * 0.62), height):
        sampled = 0
        blue = 0
        for x in range(0, width, sample_step):
            r, g, b = poster.getpixel((x, y))[:3]
            sampled += 1
            if b >= 145 and g >= 45 and r <= 90 and b >= g + 35 and b >= r + 70:
                blue += 1
        if sampled and blue / sampled >= min_blue_fraction:
            rows.append(y)

    if not rows:
        return None

    groups: list[tuple[int, int]] = []
    start = prev = rows[0]
    for y in rows[1:]:
        if y <= prev + max_gap:
            prev = y
            continue
        groups.append((start, prev))
        start = prev = y
    groups.append((start, prev))

    candidates = [
        group
        for group in groups
        if group[1] >= int(height * 0.9) and group[1] - group[0] >= max(24, int(height * 0.045))
    ]
    return candidates[-1] if candidates else None


def _detect_footer_bottom_band(poster: Any) -> tuple[int, int] | None:
    footer_band = _detect_footer_blue_band(poster)
    if footer_band:
        return footer_band

    width, height = poster.size
    sample_step = max(1, width // 180)
    min_fraction = 0.34
    max_gap = max(8, int(height * 0.02))
    bottom_samples: list[tuple[int, int, int]] = []
    for y in range(max(0, height - max(12, height // 60)), height):
        for x in range(int(width * 0.05), int(width * 0.95), sample_step):
            r, g, b = poster.getpixel((x, y))[:3]
            if not (r >= 230 and g >= 230 and b >= 230):
                bottom_samples.append((r, g, b))
    if not bottom_samples:
        return None

    base = tuple(sum(pixel[index] for pixel in bottom_samples) // len(bottom_samples) for index in range(3))

    def similar_to_bottom(pixel: tuple[int, int, int]) -> bool:
        r, g, b = pixel
        return abs(r - base[0]) + abs(g - base[1]) + abs(b - base[2]) <= 115

    rows: list[int] = []
    for y in range(int(height * 0.62), height):
        sampled = 0
        similar = 0
        for x in range(0, width, sample_step):
            sampled += 1
            if similar_to_bottom(poster.getpixel((x, y))[:3]):
                similar += 1
        if sampled and similar / sampled >= min_fraction:
            rows.append(y)

    if not rows:
        return None

    groups: list[tuple[int, int]] = []
    start = prev = rows[0]
    for y in rows[1:]:
        if y <= prev + max_gap:
            prev = y
            continue
        groups.append((start, prev))
        start = prev = y
    groups.append((start, prev))

    candidates = [
        group
        for group in groups
        if group[1] >= int(height * 0.9) and group[1] - group[0] >= max(24, int(height * 0.045))
    ]
    return candidates[-1] if candidates else None


def _footer_blue_fill(poster: Any, footer_band: tuple[int, int] | None) -> tuple[int, int, int, int]:
    width, height = poster.size
    top, bottom = footer_band or (int(height * 0.9), height - 1)
    colors: list[tuple[int, int, int]] = []
    for y in range(top, bottom + 1, max(1, (bottom - top) // 12 or 1)):
        for x in range(int(width * 0.05), int(width * 0.55), max(1, width // 80)):
            r, g, b = poster.getpixel((x, y))[:3]
            if b >= 145 and g >= 45 and r <= 90 and b >= g + 35 and b >= r + 70:
                colors.append((r, g, b))
    if not colors:
        for y in range(top, bottom + 1, max(1, (bottom - top) // 12 or 1)):
            for x in range(int(width * 0.05), int(width * 0.55), max(1, width // 80)):
                r, g, b = poster.getpixel((x, y))[:3]
                if not (r >= 230 and g >= 230 and b >= 230):
                    colors.append((r, g, b))
    if not colors:
        return (0, 94, 216, 255)
    r = sum(item[0] for item in colors) // len(colors)
    g = sum(item[1] for item in colors) // len(colors)
    b = sum(item[2] for item in colors) // len(colors)
    return (r, g, b, 255)


def _needs_qr_background_cleanup(poster: Any, box: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    step_x = max(1, width // 40)
    step_y = max(1, height // 40)
    sampled = 0
    light = 0
    for y in range(top, bottom, step_y):
        for x in range(left, right, step_x):
            r, g, b = poster.getpixel((x, y))[:3]
            sampled += 1
            if r >= 220 and g >= 220 and b >= 220:
                light += 1
    return bool(sampled and light / sampled >= 0.12)


def _barcode_box(position: Any) -> tuple[int, int, int, int] | None:
    points = re.findall(r"(\d+)x(\d+)", str(position or ""))
    if not points:
        return None
    xs = [int(x) for x, _y in points]
    ys = [int(y) for _x, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _local_cleanup_fill(poster: Any, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    width, height = poster.size
    margin = max(8, int(max(right - left, bottom - top) * 0.18))
    outer_left = max(0, left - margin)
    outer_top = max(0, top - margin)
    outer_right = min(width, right + margin)
    outer_bottom = min(height, bottom + margin)
    samples: list[tuple[int, int, int]] = []
    step = max(1, max(outer_right - outer_left, outer_bottom - outer_top) // 48)
    for y in range(outer_top, outer_bottom, step):
        for x in range(outer_left, outer_right, step):
            if left <= x <= right and top <= y <= bottom:
                continue
            r, g, b = poster.getpixel((x, y))[:3]
            # Avoid averaging QR black modules or pure white card interiors when nearby color is available.
            if r <= 35 and g <= 35 and b <= 35:
                continue
            samples.append((r, g, b))
    if not samples:
        return (255, 255, 255, 255)
    r = sum(item[0] for item in samples) // len(samples)
    g = sum(item[1] for item in samples) // len(samples)
    b = sum(item[2] for item in samples) // len(samples)
    return (r, g, b, 255)


def find_existing_qr_codes(poster: Any) -> list[dict[str, Any]]:
    try:
        import zxingcpp
    except ImportError:
        return []

    barcodes = zxingcpp.read_barcodes(poster.convert("RGB"))
    items: list[dict[str, Any]] = []
    for barcode in barcodes:
        if str(getattr(barcode, "format", "") or "") != "QR Code":
            continue
        box = _barcode_box(getattr(barcode, "position", None))
        if not box:
            continue
        items.append(
            {
                "text": str(getattr(barcode, "text", "") or ""),
                "box_tuple": box,
                "box": {
                    "x": box[0],
                    "y": box[1],
                    "right": box[2],
                    "bottom": box[3],
                },
            }
        )
    return items


def _box_intersects_band(box: tuple[int, int, int, int], band: tuple[int, int]) -> bool:
    _left, top, _right, bottom = box
    band_top, band_bottom = band
    return bottom >= band_top and top <= band_bottom


def cleanup_existing_qr_codes(poster: Any, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from PIL import ImageDraw

    if items is None:
        items = find_existing_qr_codes(poster)
    if not items:
        return {"applied": False, "items": []}

    draw = ImageDraw.Draw(poster)
    cleaned_items: list[dict[str, Any]] = []
    for item in items:
        box = item.get("box_tuple")
        if not box:
            continue
        left, top, right, bottom = box
        pad = max(8, int(max(right - left, bottom - top) * 0.12))
        cleanup_box = (
            max(0, left - pad),
            max(0, top - pad),
            min(poster.width, right + pad),
            min(poster.height, bottom + pad),
        )
        fill = _local_cleanup_fill(poster, cleanup_box)
        radius = max(6, int(max(cleanup_box[2] - cleanup_box[0], cleanup_box[3] - cleanup_box[1]) * 0.08))
        draw.rounded_rectangle(cleanup_box, radius=radius, fill=fill)
        cleaned_items.append(
            {
                "text": str(item.get("text") or ""),
                "box": {
                    "x": cleanup_box[0],
                    "y": cleanup_box[1],
                    "right": cleanup_box[2],
                    "bottom": cleanup_box[3],
                },
            }
        )
    return {"applied": bool(cleaned_items), "method": "cover_existing_qr_boxes", "items": cleaned_items}


def _draw_deterministic_footer_copy(
    *,
    poster: Any,
    brand: dict[str, Any],
    overlay_config: dict[str, Any],
    footer_band: tuple[int, int],
    qr_left: int,
    footer_fill: tuple[int, int, int, int],
) -> dict[str, Any]:
    from PIL import ImageDraw

    width, height = poster.size
    footer_top, footer_bottom = footer_band
    footer_height = footer_bottom - footer_top + 1
    left_margin = max(18, int(width * _ratio_value(overlay_config, "footer_left_margin_ratio", 0.065)))
    text_right = max(left_margin, qr_left - max(18, int(width * _ratio_value(overlay_config, "footer_qr_gap_ratio", 0.045))))
    max_text_width = max(1, text_right - left_margin)
    if max_text_width < int(width * 0.24):
        return {"applied": False, "reason": "footer_text_area_too_narrow"}

    font_path = str(overlay_config.get("footer_font_path") or "/System/Library/Fonts/STHeiti Medium.ttc")
    title = str(overlay_config.get("footer_title") or "扫码下载司机端")
    subtitle = str(overlay_config.get("footer_subtitle") or "了解更多活动福利")
    note = str(overlay_config.get("footer_note") or "*最终解释权归平台所有")

    title_font = _fit_font(title, font_path, max_width=max_text_width, max_height=max(20, int(footer_height * 0.25)))
    subtitle_font = _fit_font(subtitle, font_path, max_width=max_text_width, max_height=max(16, int(footer_height * 0.2)))
    note_font = _fit_font(note, font_path, max_width=max_text_width, max_height=max(10, int(footer_height * 0.105)))

    draw = ImageDraw.Draw(poster)
    r, g, b, _alpha = footer_fill
    # Use high-contrast text on dark/saturated brand footers and dark text on rare light footers.
    is_light_footer = (r * 299 + g * 587 + b * 114) / 1000 > 178
    title_color = (25, 38, 58, 255) if is_light_footer else (255, 255, 255, 255)
    subtitle_color = (0, 106, 255, 255) if is_light_footer else (255, 242, 66, 255)
    note_color = (65, 76, 92, 230) if is_light_footer else (255, 255, 255, 220)

    title_box = draw.textbbox((0, 0), title, font=title_font)
    subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    note_box = draw.textbbox((0, 0), note, font=note_font)
    title_h = title_box[3] - title_box[1]
    subtitle_h = subtitle_box[3] - subtitle_box[1]
    note_h = note_box[3] - note_box[1]
    gap = max(6, int(footer_height * 0.045))
    content_h = title_h + subtitle_h + note_h + gap * 2
    y = footer_top + max(10, int((footer_height - content_h) / 2))

    draw.text((left_margin, y), title, font=title_font, fill=title_color)
    y += title_h + gap
    draw.text((left_margin, y), subtitle, font=subtitle_font, fill=subtitle_color)
    note_y = min(footer_bottom - note_h - max(6, int(footer_height * 0.08)), y + subtitle_h + gap)
    draw.text((left_margin, note_y), note, font=note_font, fill=note_color)

    return {
        "applied": True,
        "title": title,
        "subtitle": subtitle,
        "note": note,
        "text_box": {
            "x": left_margin,
            "y": footer_top,
            "right": text_right,
            "bottom": footer_bottom,
        },
    }


def overlay_poster_qr(poster_path: Path, brand: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image, ImageDraw, ImageFilter

    root = skill_root()
    qr_config = brand.get("qr_validation") if isinstance(brand.get("qr_validation"), dict) else {}
    assets = brand.get("assets") if isinstance(brand.get("assets"), dict) else {}
    qr_raw = qr_config.get("expected_qr_path") or assets.get("qr_path")
    if not qr_raw:
        return {"applied": False, "error": "brand_qr_not_configured"}
    qr_path = resolve_path(str(qr_raw), root)
    if not qr_path.is_file():
        return {"applied": False, "error": f"brand_qr_not_found: {qr_path}"}
    if not poster_path.is_file():
        return {"applied": False, "error": f"poster_not_found: {poster_path}"}

    overlay_config = template.get("qr_overlay") if isinstance(template.get("qr_overlay"), dict) else {}
    if not overlay_config:
        return {"applied": False, "error": "template_qr_overlay_not_configured"}

    poster = Image.open(poster_path).convert("RGBA")
    existing_qr_items = find_existing_qr_codes(poster)
    source_footer_band_before_cleanup = _detect_footer_bottom_band(poster)
    if source_footer_band_before_cleanup and any(
        _box_intersects_band(item["box_tuple"], source_footer_band_before_cleanup)
        for item in existing_qr_items
        if item.get("box_tuple")
    ):
        original_width, _original_height = poster.size
        footer_top, footer_bottom = source_footer_band_before_cleanup
        poster = poster.crop((0, 0, original_width, footer_top))
        existing_qr_cleanup = {
            "applied": True,
            "method": "crop_existing_footer",
            "footer_band": source_footer_band_before_cleanup,
            "cropped_pixels": footer_bottom - footer_top + 1,
            "items": [
                {key: value for key, value in item.items() if key != "box_tuple"}
                for item in existing_qr_items
            ],
        }
    else:
        existing_qr_cleanup = cleanup_existing_qr_codes(poster, existing_qr_items)
    width, height = poster.size
    qr_size = max(128, int(width * _ratio_value(overlay_config, "size_ratio", 0.16)))
    box = {
        "x": int(width * _ratio_value(overlay_config, "x_ratio", 0.78)),
        "y": int(height * _ratio_value(overlay_config, "y_ratio", 0.087)),
        "width": qr_size,
        "height": qr_size,
    }
    box["x"] = max(0, min(box["x"], width - qr_size))
    box["y"] = max(0, min(box["y"], height - qr_size))
    padding = max(8, int(qr_size * _ratio_value(overlay_config, "padding_ratio", 0.08)))
    card_size = qr_size + padding * 2
    footer_band: tuple[int, int] | None = None
    anchor = str(overlay_config.get("anchor") or "").strip().lower()
    right_margin = max(12, int(width * _ratio_value(overlay_config, "right_margin_ratio", 0.035)))
    bottom_margin = max(12, int(width * _ratio_value(overlay_config, "bottom_margin_ratio", 0.035)))
    deterministic_footer = bool(overlay_config.get("deterministic_footer", anchor == "bottom_right"))
    footer_result: dict[str, Any] = {"applied": False}
    if anchor == "bottom_right":
        source_footer_band = _detect_footer_bottom_band(poster)
        if deterministic_footer:
            footer_fill = _footer_fill_from_config(brand, overlay_config) or _footer_blue_fill(poster, source_footer_band)
            top_clearance = max(8, int(card_size * _ratio_value(overlay_config, "footer_top_clearance_ratio", 0.08)))
            footer_vertical_padding = max(top_clearance, bottom_margin)
            footer_height = max(
                int(width * _ratio_value(overlay_config, "footer_height_ratio", 0.26)),
                card_size + footer_vertical_padding * 2,
            )
            max_footer_height = int(height * _ratio_value(overlay_config, "footer_max_height_ratio", 0.22))
            if max_footer_height:
                footer_height = min(footer_height, max_footer_height)
            footer_height = max(footer_height, card_size + footer_vertical_padding * 2)
            append_footer = bool(overlay_config.get("append_footer_for_qr", True))
            if append_footer:
                extended = Image.new("RGBA", (width, height + footer_height), footer_fill)
                extended.alpha_composite(poster, (0, 0))
                poster = extended
                footer_band = (height, height + footer_height - 1)
                height = height + footer_height
            else:
                footer_height = min(footer_height, height)
                footer_band = (height - footer_height, height - 1)
            footer_draw = ImageDraw.Draw(poster)
            footer_draw.rectangle((0, footer_band[0], width, height), fill=footer_fill)
        elif bool(overlay_config.get("append_footer_for_qr", False)):
            footer_band = source_footer_band
            top_clearance = max(8, int(card_size * _ratio_value(overlay_config, "footer_top_clearance_ratio", 0.08)))
            footer_height = top_clearance + card_size + bottom_margin
            extended = Image.new("RGBA", (width, height + footer_height), _footer_blue_fill(poster, footer_band))
            extended.alpha_composite(poster, (0, 0))
            poster = extended
            footer_band = (height, height + footer_height - 1)
            height = height + footer_height
        elif bool(overlay_config.get("ensure_footer_min_height", True)):
            footer_band = source_footer_band
            if footer_band:
                footer_top, _footer_bottom = footer_band
                top_clearance = max(8, int(card_size * _ratio_value(overlay_config, "footer_top_clearance_ratio", 0.08)))
                required_height = footer_top + top_clearance + card_size + bottom_margin
                if height < required_height:
                    extra_height = required_height - height
                    extended = Image.new("RGBA", (width, height + extra_height), _footer_blue_fill(poster, footer_band))
                    extended.alpha_composite(poster, (0, 0))
                    poster = extended
                    height = height + extra_height
                    footer_band = (footer_top, height - 1)
        card_x = max(0, min(width - right_margin - card_size, width - card_size))
        if deterministic_footer and footer_band:
            footer_top, footer_bottom = footer_band
            footer_height = footer_bottom - footer_top + 1
            card_y = footer_top + max(0, int((footer_height - card_size) / 2))
            card_y = max(footer_top, min(card_y, footer_bottom + 1 - card_size))
            footer_result = _draw_deterministic_footer_copy(
                poster=poster,
                brand=brand,
                overlay_config=overlay_config,
                footer_band=footer_band,
                qr_left=card_x,
                footer_fill=_footer_blue_fill(poster, footer_band),
            )
        else:
            card_y = max(0, min(height - bottom_margin - card_size, height - card_size))
    elif anchor == "footer_right":
        footer_band = _detect_footer_bottom_band(poster)
        card_x = max(0, min(width - right_margin - card_size, width - card_size))
        if footer_band:
            footer_top, footer_bottom = footer_band
            footer_height = footer_bottom - footer_top + 1
            if footer_height >= card_size:
                card_y = footer_top + max(0, int((footer_height - card_size) / 2))
            else:
                card_y = height - bottom_margin - card_size
        else:
            card_y = height - bottom_margin - card_size
        card_y = max(0, min(card_y, height - card_size))
    else:
        card_x = max(0, min(box["x"] - padding, width - card_size))
        card_y = max(0, min(box["y"] - padding, height - card_size))
    radius = max(10, int(card_size * _ratio_value(overlay_config, "radius_ratio", 0.08)))

    if (
        anchor in {"bottom_right", "footer_right"}
        and bool(overlay_config.get("cleanup_background", True))
        and not deterministic_footer
    ):
        draw = ImageDraw.Draw(poster)
        cleanup_left = max(0, card_x - int(card_size * _ratio_value(overlay_config, "cleanup_left_ratio", 0.7)))
        cleanup_top = max(footer_band[0] if footer_band else 0, card_y - int(card_size * _ratio_value(overlay_config, "cleanup_top_ratio", 0.08)))
        cleanup_right = min(width, card_x + card_size + int(card_size * 0.08))
        cleanup_bottom_limit = footer_band[1] + 1 if footer_band else height
        cleanup_bottom = min(cleanup_bottom_limit, card_y + card_size + int(card_size * 0.12))
        cleanup_box = (cleanup_left, cleanup_top, cleanup_right, cleanup_bottom)
        if anchor == "bottom_right" or _needs_qr_background_cleanup(poster, cleanup_box):
            draw.rounded_rectangle(
                cleanup_box,
                radius=max(10, int(card_size * 0.08)),
                fill=_footer_blue_fill(poster, footer_band),
            )

    shadow = Image.new("RGBA", poster.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_offset = max(2, int(card_size * 0.018))
    shadow_draw.rounded_rectangle(
        (
            card_x + shadow_offset,
            card_y + shadow_offset,
            card_x + card_size + shadow_offset,
            card_y + card_size + shadow_offset,
        ),
        radius=radius,
        fill=(2, 42, 120, 42),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(3, int(card_size * 0.025))))
    poster = Image.alpha_composite(poster, shadow)

    card = Image.new("RGBA", (card_size, card_size), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card)
    card_draw.rounded_rectangle(
        (0, 0, card_size - 1, card_size - 1),
        radius=radius,
        fill=(255, 255, 255, 255),
        outline=(192, 218, 255, 255),
        width=max(1, int(card_size * 0.01)),
    )

    qr = Image.open(qr_path).convert("RGBA")
    qr_canvas = Image.new("RGBA", qr.size, (255, 255, 255, 255))
    qr_canvas.alpha_composite(qr)
    qr = qr_canvas.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    card.alpha_composite(qr, (padding, padding))
    poster.alpha_composite(card, (card_x, card_y))
    poster.convert("RGB").save(poster_path)
    return {
        "applied": True,
        "method": "source_qr_card_overlay",
        "qr_path": str(qr_path),
        "box": {
            "x": card_x,
            "y": card_y,
            "card_size": card_size,
            "qr_x": card_x + padding,
            "qr_y": card_y + padding,
            "qr_size": qr_size,
            "padding": padding,
            "anchor": anchor or "ratio",
            "footer_band": footer_band,
            "deterministic_footer": deterministic_footer,
            "footer": footer_result,
            "existing_qr_cleanup": existing_qr_cleanup,
        },
    }


def apply_deterministic_assets(poster_path: Path, brand: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    logo_result = overlay_poster_logo(poster_path, brand)
    qr_result = overlay_poster_qr(poster_path, brand, template)
    return {
        "logo": logo_result,
        "qr": qr_result,
        "ok": bool(logo_result.get("applied") and qr_result.get("applied")),
    }


def apply_hybrid_assets(poster_path: Path, brand: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    qr_result = overlay_poster_qr(poster_path, brand, template)
    return {
        "logo": {"applied": False, "method": "model_integrated"},
        "qr": qr_result,
        "ok": bool(qr_result.get("applied")),
    }


def text_preview(text: str, limit: int = 18) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[:limit]


def detect_modules(text: str) -> list[str]:
    modules: list[str] = []
    seen: set[str] = set()

    def add_module(label: str) -> None:
        normalized = label.strip()
        key = re.sub(r"[\s/／、:：_-]+", "", normalized)
        if normalized and key not in seen:
            seen.add(key)
            modules.append(normalized)

    headings: list[str] = []
    for line in text.splitlines():
        heading = SECTION_HEADING_PATTERN.match(line.strip())
        if not heading:
            continue
        title = heading.group(1).strip()
        if META_SECTION_TITLE_PATTERN.search(title):
            continue
        if any(pattern.search(title) for _, pattern in EXCLUDED_SECTION_PATTERNS):
            continue
        headings.append(title)
        add_module(title)

    for label, pattern in (
        ("免佣/飞涨奖", r"免佣|飞涨|流水"),
        ("卡券概览", r"卡券|全天卡|时段卡"),
        ("全量活动", r"全量活动"),
        ("新人权益", r"新人|成长奖|首单"),
        ("司邀司", r"司邀司|邀请"),
        ("定向活动", r"定向"),
        ("完单奖", r"完单奖"),
    ):
        if re.search(pattern, text):
            if any(re.search(pattern, title) for title in headings):
                continue
            add_module(label)
    return modules


def non_empty_line_count(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()])


def poster_size_plan(
    *,
    display_text: str,
    template: dict[str, Any],
    base_size: str,
    size_policy: str,
) -> dict[str, Any]:
    modules = detect_modules(display_text)
    line_count = non_empty_line_count(display_text)
    char_count = len(display_text)
    sizing = template.get("content_sizing") if isinstance(template.get("content_sizing"), dict) else {}
    long_size = str(sizing.get("long_size") or DEFAULT_LONG_POSTER_SIZE)
    try:
        module_threshold = int(sizing.get("long_module_threshold") or 6)
    except (TypeError, ValueError):
        module_threshold = 6
    try:
        line_threshold = int(sizing.get("long_line_threshold") or 28)
    except (TypeError, ValueError):
        line_threshold = 28
    try:
        char_threshold = int(sizing.get("long_char_threshold") or 560)
    except (TypeError, ValueError):
        char_threshold = 560

    reasons: list[str] = []
    if len(modules) >= module_threshold:
        reasons.append(f"模块数 {len(modules)} >= {module_threshold}")
    if line_count >= line_threshold:
        reasons.append(f"可展示行数 {line_count} >= {line_threshold}")
    if char_count >= char_threshold:
        reasons.append(f"可展示字符数 {char_count} >= {char_threshold}")

    if size_policy == SIZE_POLICY_FIXED:
        return {
            "mode": "fixed",
            "size": base_size,
            "base_size": base_size,
            "size_policy": size_policy,
            "module_count": len(modules),
            "line_count": line_count,
            "char_count": char_count,
            "reasons": reasons,
        }
    if size_policy == SIZE_POLICY_AUTO and reasons:
        return {
            "mode": "long",
            "size": long_size,
            "base_size": base_size,
            "size_policy": size_policy,
            "module_count": len(modules),
            "line_count": line_count,
            "char_count": char_count,
            "reasons": reasons,
        }
    return {
        "mode": "normal",
        "size": base_size,
        "base_size": base_size,
        "size_policy": size_policy,
        "module_count": len(modules),
        "line_count": line_count,
        "char_count": char_count,
        "reasons": reasons,
    }


def detect_hidden_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for label, pattern in EXCLUDED_SECTION_PATTERNS:
        if pattern.search(text):
            candidates.append(label)
    return candidates


def split_activity_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    title: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        heading = SECTION_HEADING_PATTERN.match(line.strip())
        if heading:
            if lines:
                sections.append({"title": title, "lines": lines})
            title = heading.group(1).strip()
            lines = [line]
            continue
        lines.append(line)
    if lines:
        sections.append({"title": title, "lines": lines})
    return sections


def filter_display_content(text: str) -> tuple[str, list[str]]:
    display_blocks: list[str] = []
    excluded: list[str] = []
    for section in split_activity_sections(text):
        title = str(section.get("title") or "")
        lines = [str(line) for line in section.get("lines") or []]
        if GENERATION_NOTES_SECTION_TITLE_PATTERN.search(title):
            excluded.append(f"{title}：仅作为生成要求，不展示为海报活动内容")
            continue
        matched_section_exclusion = next(
            (label for label, pattern in EXCLUDED_SECTION_PATTERNS if pattern.search(title)),
            None,
        )
        if matched_section_exclusion:
            excluded.append(f"{title or '未命名段落'}：{matched_section_exclusion}")
            continue

        kept_lines: list[str] = []
        for line in lines:
            matched_line_exclusion = next(
                (label for label, pattern in EXCLUDED_LINE_PATTERNS if pattern.search(line)),
                None,
            )
            if matched_line_exclusion:
                excluded.append(f"{title or '未命名段落'} 行：{matched_line_exclusion}")
                continue
            kept_lines.append(line)
        if any(line.strip() for line in kept_lines):
            display_blocks.append("\n".join(kept_lines).strip("\n"))

    display_text = "\n\n".join(block for block in display_blocks if block)
    return display_text or text, sorted(set(excluded))


def extract_generation_notes(text: str) -> str:
    note_blocks: list[str] = []
    for section in split_activity_sections(text):
        title = str(section.get("title") or "")
        if not GENERATION_NOTES_SECTION_TITLE_PATTERN.search(title):
            continue
        lines = [str(line).strip() for line in section.get("lines") or []]
        body = "\n".join(line for line in lines[1:] if line)
        if body:
            note_blocks.append(body)
    return "\n\n".join(note_blocks)


def brand_safe_display_content(text: str, brand: dict[str, Any]) -> str:
    display = brand.get("display") if isinstance(brand.get("display"), dict) else {}
    poster_brand_name = str(display.get("poster_brand_name") or brand.get("canonical_name") or "").strip()
    if not poster_brand_name:
        return text
    safe_text = text
    for forbidden in as_list(display.get("forbidden_brand_text")):
        forbidden_text = str(forbidden).strip()
        if forbidden_text and forbidden_text != poster_brand_name:
            safe_text = safe_text.replace(forbidden_text, poster_brand_name)
    return safe_text


def attach_confirmation_materials(
    rows: list[dict[str, Any]],
    *,
    default_template: dict[str, Any],
    templates_by_brand_id: dict[str, dict[str, Any]],
    base_size: str,
    size_policy: str,
) -> None:
    for row in rows:
        if row["status"] != "supported":
            continue
        template = template_for_row(row, default_template=default_template, templates_by_brand_id=templates_by_brand_id)
        path = Path(str(row["path"]))
        try:
            text = read_text_guess(path)
        except Exception as exc:  # noqa: BLE001 - expose unreadable TXT instead of guessing.
            row["confirmation_error"] = str(exc)
            continue
        display_text, excluded = filter_display_content(text)
        size_plan = poster_size_plan(
            display_text=display_text,
            template=template,
            base_size=base_size,
            size_policy=size_policy,
        )
        row["confirmation"] = {
            "brand": row.get("brand", ""),
            "city": row.get("city", ""),
            "city_source": row.get("city_source", ""),
            "template": f"{template['display_name']} ({template['template_id']})",
            "sample_file": row.get("sample_file", ""),
            "is_sample": bool(row.get("is_sample")),
            "output_name": row.get("output_name", ""),
            "txt_char_count": len(text),
            "txt_line_count": len(text.splitlines()),
            "display_char_count": len(display_text),
            "detected_modules": detect_modules(display_text),
            "poster_size_plan": size_plan,
            "hidden_candidates": excluded or detect_hidden_candidates(text),
            "txt_preview": text_preview(text),
        }


def compile_prompt(
    *,
    brand: dict[str, Any],
    city: str,
    template: dict[str, Any],
    txt_path: Path,
    txt_content: str,
    excluded_content: list[str] | None = None,
    use_reference_images: bool = True,
    asset_mode: str = DEFAULT_ASSET_MODE,
    size_plan: dict[str, Any] | None = None,
    qr_retry_note: str | None = None,
    generation_notes: str | None = None,
) -> str:
    display = brand.get("display") if isinstance(brand.get("display"), dict) else {}
    brand_name = str(display.get("poster_brand_name") or brand.get("canonical_name"))
    forbidden_brand_text = [str(item).strip() for item in as_list(display.get("forbidden_brand_text")) if str(item).strip()]
    forbidden_brand_rule = ""
    if forbidden_brand_text:
        forbidden_brand_rule = (
            "\n品牌展示锁定："
            f"海报上品牌展示名只能写“{brand_name}”；"
            f"不得出现这些识别别名或禁用字样：{'、'.join(forbidden_brand_text)}。"
            "如果活动 TXT 中出现这些字样，只用于品牌识别，不得展示到海报画面。\n"
        )
    style_notes = "\n".join(f"- {item}" for item in as_list(brand.get("style_notes")))
    retry_block = f"\n二维码上次验证失败，必须修正：{qr_retry_note}\n" if qr_retry_note else ""
    plan = size_plan or {}
    poster_size = str(plan.get("size") or "")
    layout_mode = str(plan.get("mode") or "normal")
    if layout_mode == "long":
        content_layout_rules = (
            f"画幅策略：内容偏多，本次使用长海报画幅 {poster_size}。"
            "请纵向拉长海报，压缩顶部主视觉高度，扩展中部活动卡片区域；"
            "不要为了塞内容把正文缩得过小，不要遗漏可展示活动模块；"
            "扫码下载 footer 会在成图下方追加，不要牺牲正文空间预留扫码底栏。"
        )
    elif layout_mode == "fixed":
        content_layout_rules = f"画幅策略：固定海报画幅 {poster_size or '9:16'}。即使内容偏多，也不要改变输出画幅。"
    else:
        content_layout_rules = f"画幅策略：常规海报画幅 {poster_size or '9:16'}。保持模板的常规竖版信息层级。"
    generation_notes_block = ""
    if generation_notes:
        generation_notes_block = (
            "\n本次用户指定版式要求（只用于排版和视觉层级，不要作为海报正文逐字展示）：\n"
            f"{generation_notes}\n"
        )
    layout_block = ""
    mode_rules = REFERENCE_IMAGE_RULES
    if asset_mode == ASSET_MODE_OVERLAY:
        reference_block = (
            "参考图输入顺序：\n"
            "1. 模板示例图：只参考版式结构、模块顺序、留白、占位区和信息层级，不复制其中的旧内容。\n"
        )
        asset_rules = """Logo/二维码规则：
- 不要生成品牌 Logo。
- 不要生成二维码、假二维码、二维码纹理或扫码图案。
- 请保留干净的 Logo 区域，不要保留模板示例图中的占位文字、占位图标或假二维码图案。
- 扫码下载 footer 会由脚本在生成图下方追加并贴入真实二维码；模型不要生成二维码、扫码卡片、预留框、底栏 CTA 文字、图标或复杂装饰。"""
    elif asset_mode == ASSET_MODE_HYBRID:
        reference_block = (
            "参考图输入顺序：\n"
            "1. 模板示例图：只参考版式结构、模块顺序、留白、占位区和信息层级，不复制其中的旧内容。\n"
            "2. 品牌 Logo：必须来自这张真实 Logo 参考图，并自然融入顶部品牌区，不要使用模板旧 Logo 或重写成其他品牌。\n"
        )
        asset_rules = """Logo/二维码规则：
- 品牌 Logo 来自第 2 张真实 Logo 参考图，作为海报品牌区的一部分自然生成，不要后贴感、不要变形、不要替换品牌字样。
- 不要生成二维码、假二维码、二维码纹理、条形码或抽象扫码图案。
- 扫码下载 footer 会由脚本在生成图下方追加并贴入真实二维码；模型不要生成二维码、扫码卡片、预留框、底栏 CTA 文字、图标或复杂装饰。
- 请把正文、卡片和表格正常排布在当前画布内，不要为扫码区额外预留大块底栏。"""
    else:
        reference_block = (
            "参考图输入顺序：\n"
            "1. 模板示例图：只参考版式结构、模块顺序、留白、占位区和信息层级，不复制其中的旧内容。\n"
            "2. 品牌 Logo：必须来自这张真实 Logo 参考图，并自然融入顶部品牌区，不要使用模板旧 Logo 或重写成其他品牌。\n"
            "3. 品牌二维码：必须来自这张真实二维码参考图，放入底部扫码区，保持正方形、清晰、完整、无遮挡、无透视、无裁切、可扫码。\n"
        )
        asset_rules = """Logo/二维码规则：
- 品牌 Logo 来自第 2 张真实 Logo 参考图，作为海报品牌区的一部分自然生成，不要后贴感、不要变形、不要替换品牌字样。
- 二维码来自第 3 张真实二维码参考图，必须保持正方形、清晰、完整、可扫码，不得重绘成假二维码、二维码纹理、条形码或抽象扫码图案。
- 模板图中的旧 Logo、旧二维码、二维码占位、扫码占位文字都只是版式参考，不得原样保留。"""
    if not use_reference_images:
        reference_block = "生成模式：KIE text-to-image。本次不传任何模板参考图。\n"
        layout_block = f"\n结构化版式说明：\n{template_layout_description(template)}\n"
        mode_rules = TEXT_TO_IMAGE_RULES
        asset_rules = """Logo/二维码规则：
- text-to-image 模式没有真实 Logo 参考图，不得生成假二维码或伪扫码图案。
- 如需正式成品，请关闭 --text-to-image，使用默认 hybrid 模式传入模板和 Logo 参考图，并由脚本贴入真实二维码。"""
    return f"""请生成一张竖版司机城市活动海报。

输出目标：
- 品牌：{brand_name}
- 城市：{city}
- 模板：{template['display_name']}（{template['template_id']}）
- 目标画幅：{poster_size or '9:16'}
- 活动 TXT 文件：{txt_path.name}

{reference_block}{layout_block}

{asset_rules}

品牌视觉要求：
{style_notes}
{forbidden_brand_rule}

通用内容规则：
{COMMON_POSTER_RULES}
{ACTIVITY_LAYOUT_RULES}
{mode_rules}
{content_layout_rules}
{generation_notes_block}
{retry_block}
请从下面"可展示活动内容"中提取活动内容，并尽量覆盖其中所有模块。不要编造城市、日期、金额、奖励、门槛或活动规则。
已排除内容：{"；".join(excluded_content or ["无"])}

可展示活动内容：
<<<TXT
{txt_content}
TXT
>>>
"""


def markdown_summary(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| 品牌 | TXT 文件 | 模板 | 输出图路径 | 二维码验证 | 状态 | 失败原因 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {brand} | {txt} | {template} | {path} | {qr} | {status} | {reason} |".format(
                brand=row.get("brand", ""),
                txt=Path(str(row.get("path", row.get("name", "")))).name,
                template=row.get("template", ""),
                path=row.get("output_path", ""),
                qr=row.get("qr_validation", ""),
                status=row.get("run_status", row.get("status", "")),
                reason=row.get("failure_reason", row.get("reason", "")),
            )
        )
    return "\n".join(lines)


def print_dry_run(
    rows: list[dict[str, Any]],
    *,
    default_template: dict[str, Any],
    templates_by_brand_id: dict[str, dict[str, Any]],
) -> None:
    logger.info("DRY RUN: no image API will be called.")
    logger.info("Selected default template: %s (%s)", default_template["display_name"], default_template["template_id"])
    print()
    for row in rows:
        if row["status"] == "supported":
            template = template_for_row(row, default_template=default_template, templates_by_brand_id=templates_by_brand_id)
            sample = "sample=yes" if row.get("is_sample") else f"sample={row.get('sample_file')}"
            print(
                f"SUPPORTED  {row['brand']}  {Path(row['path']).name}  "
                f"city={row.get('city')} city_source={row.get('city_source')} keyword={row['matched_keyword']} "
                f"template={template['template_id']} output={row.get('output_name')} {sample}"
            )
        elif row["status"] == "ambiguous":
            matches = ", ".join(f"{item['brand']}({item['keyword']})" for item in row["matches"])
            print(f"AMBIGUOUS  {Path(row['path']).name}  matches={matches}")
        else:
            print(f"UNSUPPORTED  {Path(row['path']).name}  reason={row['reason']}")
    print()
    print("待用户确认清单：")
    print(
        markdown_summary(
            [
                {
                    **row,
                    "template": template_for_row(row, default_template=default_template, templates_by_brand_id=templates_by_brand_id)["display_name"],
                }
                for row in rows
            ]
        )
    )
    print()
    print("确认材料（仅来自真实 TXT 文件，未调用图片 API）：")
    for row in rows:
        if row["status"] != "supported":
            continue
        print()
        print(f"### {row['brand']} / {Path(row['path']).name}")
        if row.get("confirmation_error"):
            print(f"- TXT 读取失败：{row['confirmation_error']}")
            continue
        confirmation = row.get("confirmation", {})
        modules = "、".join(confirmation.get("detected_modules") or ["未命中常见模块关键词"])
        hidden = "、".join(confirmation.get("hidden_candidates") or ["未命中"])
        print(f"- 城市：{confirmation.get('city')}")
        print(f"- 模板：{confirmation.get('template')}")
        print(f"- 样图文件：{confirmation.get('sample_file')}")
        print(f"- 成品命名：{confirmation.get('output_name')}")
        print(f"- 城市来源：{confirmation.get('city_source')}")
        print(f"- 关键词命中模块：{modules}")
        print(f"- 排除候选关键词：{hidden}")
        size_plan = confirmation.get("poster_size_plan") if isinstance(confirmation.get("poster_size_plan"), dict) else {}
        reasons = "；".join(size_plan.get("reasons") or [])
        reason_note = ""
        if size_plan.get("mode") == SIZE_POLICY_FIXED and reasons:
            reason_note = f"（已关闭自动拉长；内容达到长图阈值：{reasons}）"
        elif reasons:
            reason_note = f"（{reasons}）"
        print(
            f"- 海报画幅策略：{size_plan.get('mode', 'normal')} / {size_plan.get('size', '')}"
            + reason_note
        )
        print(
            f"- TXT 行数/字符数：{confirmation.get('txt_line_count')} 行 / "
            f"{confirmation.get('txt_char_count')} 字符；可展示内容 {confirmation.get('display_char_count')} 字符"
        )
        print("- TXT 预览：")
        for line in confirmation.get("txt_preview") or []:
            print(f"  {line}")
    print()
    print("确认以上内容后，生成样图或批量成品时必须追加 --confirmed。")


def run_check(template_id: str | None = None) -> dict[str, Any]:
    root = skill_root()
    errors: list[str] = []
    details: dict[str, Any] = {
        "skill_root": str(root),
        "dependencies": {},
        "fonts": {},
        "templates": [],
        "brands": [],
    }

    for package, module_name in REQUIRED_RUNTIME_MODULES.items():
        try:
            importlib.import_module(module_name)
            details["dependencies"][package] = {"ok": True}
        except Exception as exc:  # noqa: BLE001 - health check reports exact import failure.
            message = str(exc)
            details["dependencies"][package] = {"ok": False, "error": message}
            errors.append(f"依赖不可用：{package}: {message}")

    font_paths = usable_cjk_font_paths()
    details["fonts"] = {
        "ok": bool(font_paths),
        "usable_paths": font_paths,
    }
    if not font_paths:
        errors.append(_font_error_message())

    try:
        templates = load_templates(root)
    except Exception as exc:  # noqa: BLE001 - report invalid template file as a check failure.
        templates = []
        errors.append(f"模板索引读取失败：{exc}")

    if not templates:
        errors.append("assets/templates/templates.yaml 中没有可用模板。")
    template_ids = {str(template.get("template_id") or "") for template in templates}
    for template in templates:
        template_path = resolve_path(template.get("example_path", ""), root)
        item = {
            "template_id": template.get("template_id"),
            "display_name": template.get("display_name"),
            "example_path": str(template_path),
            "ok": template_path.is_file(),
        }
        if not item["ok"]:
            errors.append(f"模板示例图不存在：{template_path}")
        details["templates"].append(item)
    if template_id and not any(template.get("template_id") == template_id for template in templates):
        errors.append(f"指定模板不存在：{template_id}")

    try:
        brands = load_brands(root)
    except Exception as exc:  # noqa: BLE001 - report invalid brand config as a check failure.
        brands = []
        errors.append(f"品牌配置读取失败：{exc}")

    if not brands:
        errors.append("brands/ 中没有可用品牌配置。")
    seen_brand_ids: set[str] = set()
    for brand in brands:
        brand_errors: list[str] = []
        brand_id = str(brand.get("brand_id") or "")
        canonical = str(brand.get("canonical_name") or brand_id)
        if not brand_id:
            brand_errors.append("brand_id 为空")
        elif brand_id in seen_brand_ids:
            brand_errors.append(f"brand_id 重复：{brand_id}")
        seen_brand_ids.add(brand_id)
        if not canonical:
            brand_errors.append("canonical_name 为空")
        if not as_list(brand.get("filename_keywords")):
            brand_errors.append("filename_keywords 为空")
        preferred_template_id = brand_preferred_template_id(brand)
        if preferred_template_id and preferred_template_id not in template_ids:
            brand_errors.append(f"preferred_template_id 不存在：{preferred_template_id}")

        assets = brand.get("assets", {})
        logo_path = resolve_path(str(assets.get("logo_path") or ""), root)
        qr_path = resolve_path(str(assets.get("qr_path") or ""), root)
        if not logo_path.is_file():
            brand_errors.append(f"logo 不存在：{logo_path}")
        if not qr_path.is_file():
            brand_errors.append(f"二维码不存在：{qr_path}")

        qr_result: dict[str, Any] | None = None
        if qr_path.is_file():
            # 传入 brands 避免重复加载
            qr_result = verify_qr(canonical, qr_path, brands=brands)
            if not qr_result.get("ok"):
                brand_errors.append(
                    "源二维码自检失败："
                    + str(qr_result.get("message") or qr_result.get("error") or "unknown error")
                )

        if brand_errors:
            errors.extend(f"{canonical}: {item}" for item in brand_errors)
        details["brands"].append(
            {
                "brand_id": brand_id,
                "canonical_name": canonical,
                "config_path": brand.get("_config_path"),
                "logo_path": str(logo_path),
                "qr_path": str(qr_path),
                "preferred_template_id": preferred_template_id,
                "qr_ok": bool(qr_result and qr_result.get("ok")),
                "errors": brand_errors,
            }
        )

    return {"ok": not errors, "errors": errors, "details": details}


def print_check(result: dict[str, Any]) -> None:
    status = "OK" if result["ok"] else "FAIL"
    details = result["details"]
    print(f"{status}: lx-haibao check")
    print(f"skill_root={details['skill_root']}")
    print()
    print("Dependencies:")
    for package, item in details["dependencies"].items():
        if item["ok"]:
            print(f"- {package}: OK")
        else:
            print(f"- {package}: FAIL ({item['error']})")
    print()
    print("Fonts:")
    fonts = details.get("fonts") or {}
    if fonts.get("ok"):
        print(f"- CJK font: OK {fonts.get('usable_paths', [''])[0]}")
    else:
        print("- CJK font: FAIL")
    print()
    print("Templates:")
    for template in details["templates"]:
        template_status = "OK" if template["ok"] else "FAIL"
        print(f"- {template['template_id']}: {template_status} {template['example_path']}")
    print()
    print("Brands:")
    for brand in details["brands"]:
        brand_status = "OK" if not brand["errors"] else "FAIL"
        print(f"- {brand['canonical_name']} ({brand['brand_id']}): {brand_status}")
        print(f"  logo={brand['logo_path']}")
        print(f"  qr={brand['qr_path']} qr_ok={brand['qr_ok']}")
        if brand.get("preferred_template_id"):
            print(f"  preferred_template={brand['preferred_template_id']}")
        for error in brand["errors"]:
            print(f"  error={error}")
    if result["errors"]:
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")


def has_ratio_keys(config: Any, required: tuple[str, ...]) -> bool:
    if not isinstance(config, dict):
        return False
    for key in required:
        try:
            float(config.get(key))
        except (TypeError, ValueError):
            return False
    return True


def run_brand_locks_check() -> dict[str, Any]:
    root = skill_root()
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {"templates": [], "brands": []}
    templates = load_templates(root)
    template_ids = {str(template.get("template_id") or "") for template in templates}

    for template in templates:
        template_id = str(template.get("template_id") or "")
        example_path = resolve_path(str(template.get("example_path") or ""), root)
        template_errors: list[str] = []
        template_warnings: list[str] = []
        if not example_path.is_file():
            template_errors.append(f"模板示例图不存在：{example_path}")
        if not has_ratio_keys(template.get("qr_overlay"), ("x_ratio", "y_ratio", "size_ratio")):
            template_errors.append("qr_overlay 缺少 x_ratio/y_ratio/size_ratio；默认 hybrid 模式需要用它贴入真实二维码。")
        errors.extend(f"{template_id}: {item}" for item in template_errors)
        warnings.extend(f"{template_id}: {item}" for item in template_warnings)
        details["templates"].append(
            {
                "template_id": template_id,
                "example_path": str(example_path),
                "qr_overlay_configured": has_ratio_keys(template.get("qr_overlay"), ("x_ratio", "y_ratio", "size_ratio")),
                "errors": template_errors,
                "warnings": template_warnings,
            }
        )

    brands = load_brands(root)
    for brand in brands:
        brand_id = str(brand.get("brand_id") or "")
        canonical = str(brand.get("canonical_name") or brand_id)
        brand_errors: list[str] = []
        brand_warnings: list[str] = []
        assets = brand.get("assets") if isinstance(brand.get("assets"), dict) else {}
        logo_path = resolve_path(str(assets.get("logo_path") or ""), root)
        qr_path = resolve_path(str(assets.get("qr_path") or ""), root)
        if not logo_path.is_file():
            brand_errors.append(f"logo 不存在：{logo_path}")
        if not qr_path.is_file():
            brand_errors.append(f"二维码不存在：{qr_path}")
        if not has_ratio_keys(brand.get("logo_overlay"), ("x_ratio", "y_ratio", "width_ratio", "height_ratio")):
            brand_warnings.append("logo_overlay 未配置；默认 hybrid 模式不需要，仅 legacy overlay 模式需要。")

        preferred_template_id = brand_preferred_template_id(brand)
        if not preferred_template_id:
            brand_warnings.append("品牌尚未绑定 preferred_template_id，当前会使用默认模板。")
        elif preferred_template_id not in template_ids:
            brand_errors.append(f"preferred_template_id 不存在：{preferred_template_id}")

        qr_result: dict[str, Any] | None = None
        if qr_path.is_file():
            qr_result = verify_qr(canonical, qr_path, brands=brands)
            if not qr_result.get("ok"):
                brand_errors.append("源二维码自检失败：" + str(qr_result.get("message") or qr_result.get("error") or "unknown error"))

        errors.extend(f"{canonical}: {item}" for item in brand_errors)
        warnings.extend(f"{canonical}: {item}" for item in brand_warnings)
        details["brands"].append(
            {
                "brand_id": brand_id,
                "canonical_name": canonical,
                "preferred_template_id": preferred_template_id,
                "logo_path": str(logo_path),
                "qr_path": str(qr_path),
                "logo_overlay_configured": has_ratio_keys(brand.get("logo_overlay"), ("x_ratio", "y_ratio", "width_ratio", "height_ratio")),
                "qr_ok": bool(qr_result and qr_result.get("ok")),
                "errors": brand_errors,
                "warnings": brand_warnings,
            }
        )

    return {"ok": not errors, "errors": errors, "warnings": warnings, "details": details}


def print_brand_locks_check(result: dict[str, Any]) -> None:
    status = "OK" if result["ok"] else "FAIL"
    print(f"{status}: lx-haibao brand locks")
    print()
    print("Templates:")
    for template in result["details"]["templates"]:
        qr_status = "OK" if template["qr_overlay_configured"] else "FAIL"
        print(f"- {template['template_id']}: qr_overlay={qr_status} example={template['example_path']}")
        for error in template["errors"]:
            print(f"  error={error}")
        for warning in template.get("warnings", []):
            print(f"  warning={warning}")
    print()
    print("Brands:")
    for brand in result["details"]["brands"]:
        preferred = brand.get("preferred_template_id") or "(unbound)"
        logo_status = "OK" if brand["logo_overlay_configured"] else "optional-missing"
        qr_status = "OK" if brand["qr_ok"] else "FAIL"
        print(f"- {brand['canonical_name']} ({brand['brand_id']}): template={preferred} logo_overlay_legacy={logo_status} qr={qr_status}")
        for warning in brand["warnings"]:
            print(f"  warning={warning}")
        for error in brand["errors"]:
            print(f"  error={error}")
    if result["warnings"]:
        print()
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
    if result["errors"]:
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")


def print_provider_check(result: dict[str, Any]) -> None:
    status = "OK" if result["ok"] else "FAIL"
    print(f"{status}: lx-haibao image providers")
    print(f"primary={result.get('primary') or ''}")
    print(f"fallback={result.get('fallback') or ''}")
    print(result.get("note") or "")
    print()
    print("| Provider | Order | Model | API Key | Reachable | HTTP | Latency ms | Status | Error |")
    print("|---|---:|---|---|---|---:|---:|---|---|")
    for item in result["providers"]:
        api_key = "configured" if item.get("api_key_configured") else "missing"
        reachable = "yes" if item.get("reachable") else "no"
        order = item.get("enabled_order") or ""
        http_status = item.get("http_status") or ""
        latency_ms = item.get("latency_ms") if item.get("latency_ms") is not None else ""
        print(
            "| {provider} | {order} | {model} | {api_key} | {reachable} | {http_status} | {latency_ms} | {status} | {error} |".format(
                provider=item.get("provider") or "",
                order=order,
                model=item.get("model") or "",
                api_key=api_key,
                reachable=reachable,
                http_status=http_status,
                latency_ms=latency_ms,
                status=item.get("status") or "",
                error=str(item.get("error") or "").replace("|", "/"),
            )
        )


def print_smoke_result(result: dict[str, Any]) -> None:
    print("OK: lx-haibao image provider smoke test")
    print(f"provider={result.get('provider') or ''}")
    print(f"model={result.get('model') or ''}")
    print(f"request_id={result.get('request_id') or ''}")
    print(f"latency_ms={result.get('latency_ms') or ''}")
    print(f"output_path={result.get('filepath') or ''}")


def save_metadata(meta_dir: Path, payload: dict[str, Any]) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = safe_filename(str(payload.get("txt_file") or "poster"))
    path = meta_dir / f"{ts}_{stem}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def enrich_imagegen_request(api_result: dict[str, Any], extra: dict[str, Any]) -> str:
    request_path_value = api_result.get("imagegen_request_path")
    if not request_path_value:
        return ""
    request_path = Path(str(request_path_value))
    if not request_path.is_file():
        return str(request_path)
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except ValueError:
        payload = {}
    payload.update(extra)
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(request_path)


def generate_one(
    *,
    row: dict[str, Any],
    brand: dict[str, Any],
    template: dict[str, Any],
    output_dir: Path,
    meta_dir: Path,
    tmp_dir: Path,
    sample_only: bool,
    max_retries: int,
    size: str,
    size_policy: str,
    resolution: str,
    use_reference_images: bool = True,
    asset_mode: str = DEFAULT_ASSET_MODE,
    skip_providers: list[str] | None = None,
    skip_qr: bool = False,
    brands: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from image2_client import generate_image

    txt_path = Path(str(row["path"]))
    txt_content = read_text_guess(txt_path)
    display_content, excluded_content = filter_display_content(txt_content)
    generation_notes = extract_generation_notes(txt_content)
    model_display_content = brand_safe_display_content(display_content, brand)
    size_plan = poster_size_plan(
        display_text=display_content,
        template=template,
        base_size=size,
        size_policy=size_policy,
    )
    request_size = str(size_plan.get("size") or size)
    city, city_source = resolve_city(txt_path, brand, txt_content=txt_content)
    if row.get("city"):
        city = str(row["city"])
        city_source = str(row.get("city_source") or city_source)
    file_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_template = brand.get("output", {}).get("filename_template") or "{brand}-{city}-{timestamp}.png"
    output_name = str(filename_template).format(brand=brand["canonical_name"], city=city, timestamp=file_ts)
    if sample_only:
        output_name = output_name.replace(".png", "-sample.png")
    final_path = output_dir / output_name
    refs = reference_paths(brand, template, use_reference_images=use_reference_images, asset_mode=asset_mode)

    last_failure = ""
    qr_retry_note: str | None = None
    for attempt in range(max_retries + 1):
        logger.info(
            "开始生成海报：品牌=%s 城市=%s 城市来源=%s TXT=%s 模式=%s asset_mode=%s 尝试=%d/%d",
            brand["canonical_name"],
            city,
            city_source,
            txt_path.name,
            "image-to-image" if use_reference_images else "text-to-image",
            asset_mode,
            attempt + 1,
            max_retries + 1,
        )
        prompt = compile_prompt(
            brand=brand,
            city=city,
            template=template,
            txt_path=txt_path,
            txt_content=model_display_content,
            excluded_content=excluded_content,
            use_reference_images=use_reference_images,
            asset_mode=asset_mode,
            size_plan=size_plan,
            qr_retry_note=qr_retry_note,
            generation_notes=generation_notes,
        )
        tmp_path = tmp_dir / f"{safe_filename(brand['brand_id'])}_{safe_filename(city)}_{attempt + 1}.png"
        try:
            api_result = generate_image(
                prompt=prompt,
                reference_images=refs,
                output_path=tmp_path,
                size=request_size,
                resolution=resolution,
                skip_providers=skip_providers,
            )
            if api_result.get("status") == "agent_handoff":
                request_path = enrich_imagegen_request(
                    api_result,
                    {
                        "txt_file": str(txt_path),
                        "brand": brand["canonical_name"],
                        "city": city,
                        "city_source": city_source,
                        "template": template,
                        "attempt": attempt + 1,
                        "asset_mode": asset_mode,
                        "size_plan": size_plan,
                        "request_size": request_size,
                        "display_content": display_content,
                        "model_display_content": model_display_content,
                        "excluded_content": excluded_content,
                        "generation_notes": generation_notes,
                        "final_path": str(final_path),
                    },
                )
                meta_path = save_metadata(
                    meta_dir,
                    {
                        "txt_file": str(txt_path),
                        "brand": brand["canonical_name"],
                        "city": city,
                        "city_source": city_source,
                        "template": template,
                        "attempt": attempt + 1,
                        "asset_mode": asset_mode,
                        "size_plan": size_plan,
                        "request_size": request_size,
                        "display_content": display_content,
                        "model_display_content": model_display_content,
                        "excluded_content": excluded_content,
                        "generation_notes": generation_notes,
                        "prompt": prompt,
                        "reference_images": [str(path) for path in refs],
                        "reference_image_order": ["template_example", "brand_logo", "brand_qr"][: len(refs)],
                        "api_result": api_result,
                        "qr_result": {"ok": False, "message": "等待 Codex 内置 image_gen 生图后验证。"},
                        "final_path": str(final_path),
                        "imagegen_request_path": request_path,
                    },
                )
                return {
                    **row,
                    "template": template["display_name"],
                    "output_path": str(final_path),
                    "qr_validation": "待验证",
                    "run_status": "等待内置 image_gen",
                    "failure_reason": f"外部图片 API 已回退到内置 image_gen；请求材料：{request_path}",
                    "metadata_path": str(meta_path),
                    "imagegen_request_path": request_path,
                }
            postprocess_result: dict[str, Any] = {
                "mode": asset_mode,
                "applied": False,
                "ok": True,
                "message": "not_used_in_integrated_mode",
            }
            if asset_mode == ASSET_MODE_OVERLAY:
                postprocess_result = apply_deterministic_assets(tmp_path, brand, template)
                if not postprocess_result.get("ok"):
                    qr_result = {
                        "ok": False,
                        "brand": brand["canonical_name"],
                        "message": "Logo/二维码后处理失败。",
                        "postprocess_result": postprocess_result,
                    }
                elif skip_qr:
                    qr_result = {"ok": True, "brand": brand["canonical_name"], "skipped": True, "message": "QR validation skipped (--skip-qr)."}
                else:
                    # 传入 brands 避免每次验证都重新加载
                    qr_result = verify_qr(str(brand["canonical_name"]), tmp_path, brands=brands)
            elif asset_mode == ASSET_MODE_HYBRID:
                postprocess_result = apply_hybrid_assets(tmp_path, brand, template)
                if not postprocess_result.get("ok"):
                    qr_result = {
                        "ok": False,
                        "brand": brand["canonical_name"],
                        "message": "二维码后处理失败。",
                        "postprocess_result": postprocess_result,
                    }
                elif skip_qr:
                    qr_result = {"ok": True, "brand": brand["canonical_name"], "skipped": True, "message": "QR validation skipped (--skip-qr)."}
                else:
                    # 传入 brands 避免每次验证都重新加载
                    qr_result = verify_qr(str(brand["canonical_name"]), tmp_path, brands=brands)
            elif skip_qr:
                qr_result = {"ok": True, "brand": brand["canonical_name"], "skipped": True, "message": "QR validation skipped (--skip-qr)."}
            else:
                # 传入 brands 避免每次验证都重新加载
                qr_result = verify_qr(str(brand["canonical_name"]), tmp_path, brands=brands)
            meta_path = save_metadata(
                meta_dir,
                {
                    "txt_file": str(txt_path),
                    "brand": brand["canonical_name"],
                    "city": city,
                    "city_source": city_source,
                    "template": template,
                    "attempt": attempt + 1,
                    "asset_mode": asset_mode,
                    "size_plan": size_plan,
                    "request_size": request_size,
                    "display_content": display_content,
                    "model_display_content": model_display_content,
                    "excluded_content": excluded_content,
                    "generation_notes": generation_notes,
                    "prompt": prompt,
                    "reference_images": [str(path) for path in refs],
                    "reference_image_order": ["template_example", "brand_logo", "brand_qr"][: len(refs)],
                    "api_result": api_result,
                    "postprocess_result": postprocess_result,
                    "qr_result": qr_result,
                    "final_path": str(final_path),
                },
            )
            if qr_result.get("ok"):
                final_path.parent.mkdir(parents=True, exist_ok=True)
                if final_path.exists():
                    final_path.unlink()
                shutil.move(str(tmp_path), str(final_path))
                return {
                    **row,
                    "template": template["display_name"],
                    "output_path": str(final_path),
                    "qr_validation": "通过",
                    "run_status": "完成",
                    "failure_reason": "",
                    "metadata_path": str(meta_path),
                }
            if not postprocess_result.get("ok"):
                if asset_mode == ASSET_MODE_OVERLAY:
                    last_failure = "Logo/二维码后处理失败：" + json.dumps(postprocess_result, ensure_ascii=False)
                elif asset_mode == ASSET_MODE_HYBRID:
                    last_failure = "二维码后处理失败：" + json.dumps(postprocess_result, ensure_ascii=False)
                else:
                    last_failure = str(qr_result.get("message") or qr_result.get("error") or "二维码验证失败")
            else:
                last_failure = str(qr_result.get("message") or qr_result.get("error") or "二维码验证失败")
            if asset_mode == ASSET_MODE_INTEGRATED:
                qr_retry_note = (
                    last_failure
                    + "。请确保第 2 张真实 Logo 参考图自然融入品牌区，第 3 张真实二维码参考图完整、清晰、无变形地放入扫码区。"
                )
            elif asset_mode == ASSET_MODE_HYBRID:
                qr_retry_note = last_failure + "。请确保第 2 张真实 Logo 参考图自然融入品牌区，并保持底部扫码卡片干净、规则、无假二维码图案，便于脚本贴入真实二维码。"
            else:
                qr_retry_note = last_failure + "。请保持模板中的 Logo 和扫码区域干净，便于旧版 overlay 后处理贴入真实素材。"
            logger.warning(
                "品牌 %s 城市 %s 第 %d 次尝试资产/QR 验证失败：provider=%s asset_mode=%s error=%s",
                brand["canonical_name"],
                city,
                attempt + 1,
                str(api_result.get("provider") or "") or "-",
                asset_mode,
                last_failure,
            )
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception as exc:  # noqa: BLE001 - summarize per-file failures without stopping other brands.
            last_failure = str(exc)
            logger.error("品牌 %s 城市 %s 生成失败：%s", brand["canonical_name"], city, exc)
            if tmp_path.exists():
                tmp_path.unlink()
            break

    return {
        **row,
        "template": template["display_name"],
        "output_path": "",
        "qr_validation": "未通过",
        "run_status": "失败",
        "failure_reason": last_failure,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="WorkBuddy poster batch generator using configured image providers.")
    parser.add_argument("--check", action="store_true", help="Validate dependencies, brand assets, templates, and source QR codes without generating images.")
    parser.add_argument("--check-brand-locks", action="store_true", help="Check brand-template bindings, source assets, source QR decoding, and optional legacy overlay coordinates.")
    parser.add_argument("--check-providers", action="store_true", help="Check image provider config and base_url connectivity without generating images.")
    parser.add_argument("--confirmed-network", action="store_true", help="Confirm network access for --check-providers. Does not authorize image generation.")
    parser.add_argument("--smoke-provider", help="Run one real minimal image generation call for a provider. Requires --confirmed and may incur API cost.")
    parser.add_argument("--smoke-output-dir", help="Directory for --smoke-provider output image.")
    parser.add_argument("--smoke-prompt", default="A simple clean square test image with a single blue circle on a white background.", help="Prompt for --smoke-provider.")
    parser.add_argument("--smoke-size", default=os.environ.get("POSTER_IMAGE_SMOKE_SIZE", "1024x1024"), help="Image size for --smoke-provider.")
    parser.add_argument("--smoke-quality", default=os.environ.get("POSTER_IMAGE_SMOKE_QUALITY", "low"), help="Image quality for --smoke-provider.")
    parser.add_argument("--dir", help="Directory containing activity TXT files.")
    parser.add_argument("--file", action="append", help="Single activity TXT path. Can be repeated.")
    parser.add_argument("--dry-run", action="store_true", help="Route files and preview confirmation table without calling image API.")
    parser.add_argument("--confirmed", action="store_true", help="Required for sample or final image generation after the user confirms dry-run content.")
    parser.add_argument("--sample-only", action="store_true", help="Generate only the first routed TXT per brand.")
    parser.add_argument("--template", help="Template id from assets/templates/templates.yaml. Admin override only; defaults to brand binding or first template.")
    parser.add_argument("--admin-template-override", action="store_true", help="Allow --template to override brand template locks for admin testing.")
    parser.add_argument("--text-to-image", action="store_true", help="Do not pass template images to the provider; use structured layout prompt only.")
    parser.add_argument(
        "--asset-mode",
        choices=[ASSET_MODE_HYBRID, ASSET_MODE_INTEGRATED, ASSET_MODE_OVERLAY],
        default=DEFAULT_ASSET_MODE,
        help="Logo/QR handling mode. hybrid passes template+logo to the model and overlays real QR; integrated passes template+logo+QR to the model; overlay uses the legacy deterministic postprocess path.",
    )
    parser.add_argument("--skip-provider", action="append", default=[], help="Skip a configured image provider. Can be repeated.")
    parser.add_argument("--city", help="Override city name for all input TXT files. Default: parse from TXT content, then filename.")
    parser.add_argument("--output-dir", help="Override poster output directory.")
    parser.add_argument("--size", default=os.environ.get("POSTER_IMAGE_SIZE", "9:16"), help="Business size/aspect value for the configured image providers.")
    parser.add_argument(
        "--size-policy",
        choices=[SIZE_POLICY_AUTO, SIZE_POLICY_FIXED],
        default=os.environ.get("POSTER_SIZE_POLICY", DEFAULT_SIZE_POLICY),
        help="Poster size policy. auto keeps normal content at --size and switches content-heavy posters to the template long_size; fixed always uses --size.",
    )
    parser.add_argument("--resolution", default=os.environ.get("POSTER_IMAGE_RESOLUTION", "2k"), help="Resolution value for providers that support it.")
    parser.add_argument("--max-retries", type=int, default=2, help="QR failure retries per poster.")
    parser.add_argument("--skip-qr", action="store_true", help="Skip QR code validation (for environments without zxingcpp).")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers for brand generation (default: 1, sequential).")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    args = parser.parse_args()
    configure_logging(persistent=False)

    if args.check:
        result = run_check(args.template)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_check(result)
        return 0 if result["ok"] else 1

    if args.check_brand_locks:
        result = run_brand_locks_check()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_brand_locks_check(result)
        return 0 if result["ok"] else 1

    if args.check_providers:
        if not args.confirmed_network:
            logger.error("--check-providers 会访问 provider base URL；请追加 --confirmed-network。")
            return 2
        from image2_client import check_providers

        result = check_providers()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_provider_check(result)
        return 0 if result["ok"] else 1

    if args.smoke_provider:
        if not args.confirmed:
            logger.error("--smoke-provider 会真实调用图片 API，可能产生费用；请追加 --confirmed。")
            return 2
        from image2_client import smoke_test_provider

        configure_logging(persistent=True)
        smoke_dir = (
            Path(args.smoke_output_dir).expanduser().resolve()
            if args.smoke_output_dir
            else resolve_fog_path(DEFAULT_PROVIDER_SMOKE_DIR, Path(__file__)).resolve()
        )
        smoke_dir.mkdir(parents=True, exist_ok=True)
        provider_name = str(args.smoke_provider).strip().lower()
        output_path = smoke_dir / f"{safe_filename(provider_name)}-{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        try:
            result = smoke_test_provider(
                provider_name=provider_name,
                output_path=output_path,
                prompt=str(args.smoke_prompt),
                size=str(args.smoke_size),
                quality=str(args.smoke_quality),
            )
        except Exception as exc:  # noqa: BLE001 - report exact provider failure.
            logger.error("图片 provider 最小生图检查失败：%s", exc)
            if args.json:
                print(json.dumps({"ok": False, "provider": provider_name, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        if args.json:
            print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
        else:
            print_smoke_result(result)
        return 0

    if args.template and not args.admin_template_override:
        logger.error("--template 是管理员覆盖能力；如确需临时覆盖品牌模板，请追加 --admin-template-override。")
        return 2

    if args.text_to_image and args.asset_mode in {ASSET_MODE_HYBRID, ASSET_MODE_INTEGRATED}:
        logger.error("--text-to-image 不传真实 Logo/二维码参考图，不能使用 hybrid/integrated 资产模式；请去掉 --text-to-image，或显式使用 --asset-mode overlay 做旧版后处理。")
        return 2

    if not args.dry_run and not args.confirmed:
        logger.error("图片生成需要 --confirmed。请先运行 --dry-run，确认 TXT 内容后，再追加 --confirmed。")
        return 2

    paths = collect_input_paths(args)
    if not paths:
        logger.error("未提供输入文件，请使用 --file 或 --dir")
        return 1
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        logger.error("TXT 文件不存在：%s", "；".join(missing))
        return 1

    brands = load_brands(skill_root())
    default_template = select_template(args.template)
    rows = build_rows(paths, brands, city_override=args.city)
    brands_by_id = {str(brand["brand_id"]): brand for brand in brands}
    try:
        templates_by_brand_id = {
            str(brand["brand_id"]): select_template_for_brand(
                brand,
                forced_template_id=args.template,
                default_template=default_template,
            )
            for brand in brands
        }
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    if args.dry_run:
        attach_confirmation_materials(
            rows,
            default_template=default_template,
            templates_by_brand_id=templates_by_brand_id,
            base_size=args.size,
            size_policy=args.size_policy,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "template": default_template,
                        "templates_by_brand_id": templates_by_brand_id,
                        "routes": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print_dry_run(
                rows,
                default_template=default_template,
                templates_by_brand_id=templates_by_brand_id,
            )
        return 0

    dependency_errors = import_errors()
    if dependency_errors and not args.skip_qr:
        logger.error("运行时依赖不可用：%s", "；".join(dependency_errors))
        return 1
    if dependency_errors and args.skip_qr:
        zxing_errors = [e for e in dependency_errors if "zxing" in e.lower()]
        other_errors = [e for e in dependency_errors if "zxing" not in e.lower()]
        if other_errors:
            logger.error("运行时依赖不可用：%s", "；".join(other_errors))
            return 1
        logger.warning("跳过 QR 验证 (--skip-qr)；zxing-cpp 不可用：%s", zxing_errors[0])

    from image2_client import require_api_key

    try:
        require_api_key()
    except RuntimeError as exc:
        logger.error("%s；未调用图片 API。", exc)
        return 1

    configure_logging(persistent=True)
    output_dir, meta_dir, tmp_dir = output_dirs(args)

    # 筛选需要生成的行
    gen_rows: list[dict[str, Any]] = []
    skip_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["status"] != "supported":
            skip_rows.append(
                {
                    **row,
                    "template": "",
                    "output_path": "",
                    "qr_validation": "",
                    "run_status": "未生成",
                    "failure_reason": row.get("reason", ""),
                }
            )
            continue
        if args.sample_only and not row.get("is_sample"):
            row_template = template_for_row(row, default_template=default_template, templates_by_brand_id=templates_by_brand_id)
            skip_rows.append(
                {
                    **row,
                    "template": row_template["display_name"],
                    "output_path": "",
                    "qr_validation": "",
                    "run_status": "跳过",
                    "failure_reason": "sample-only 模式只生成每个品牌第一个 TXT",
                }
            )
            continue
        gen_rows.append(row)

    # 按品牌分组，同品牌保持顺序，不同品牌可并行
    workers = max(1, args.workers)
    results: list[dict[str, Any]] = list(skip_rows)

    if workers > 1 and len(gen_rows) > 1:
        logger.info("使用 %d 个并行 worker 生成 %d 张海报", workers, len(gen_rows))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for row in gen_rows:
                brand = brands_by_id[str(row["brand_id"])]
                row_template = template_for_row(row, default_template=default_template, templates_by_brand_id=templates_by_brand_id)
                future = pool.submit(
                    generate_one,
                    row=row,
                    brand=brand,
                    template=row_template,
                    output_dir=output_dir,
                    meta_dir=meta_dir,
                    tmp_dir=tmp_dir,
                    sample_only=args.sample_only,
                    max_retries=args.max_retries,
                    size=args.size,
                    size_policy=args.size_policy,
                    resolution=args.resolution,
                    use_reference_images=not args.text_to_image,
                    asset_mode=args.asset_mode,
                    skip_providers=args.skip_provider,
                    skip_qr=args.skip_qr,
                    brands=brands,
                )
                futures[future] = (row, row_template)
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001 - don't let one failure kill the batch.
                    row, row_template = futures[future]
                    logger.error("并行生成异常：%s", exc)
                    results.append({
                        **row,
                        "template": row_template["display_name"],
                        "output_path": "",
                        "qr_validation": "",
                        "run_status": "失败",
                        "failure_reason": str(exc),
                    })
    else:
        for row in gen_rows:
            brand = brands_by_id[str(row["brand_id"])]
            row_template = template_for_row(row, default_template=default_template, templates_by_brand_id=templates_by_brand_id)
            results.append(
                generate_one(
                    row=row,
                    brand=brand,
                    template=row_template,
                    output_dir=output_dir,
                    meta_dir=meta_dir,
                    tmp_dir=tmp_dir,
                    sample_only=args.sample_only,
                    max_retries=args.max_retries,
                    size=args.size,
                    size_policy=args.size_policy,
                    resolution=args.resolution,
                    use_reference_images=not args.text_to_image,
                    asset_mode=args.asset_mode,
                    skip_providers=args.skip_provider,
                    skip_qr=args.skip_qr,
                    brands=brands,
                )
            )

    if args.json:
        print(
            json.dumps(
                {
                    "dry_run": False,
                    "template": default_template,
                    "templates_by_brand_id": templates_by_brand_id,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(markdown_summary(results))
    incomplete_statuses = {"失败", "等待内置 image_gen"}
    return 1 if any(row.get("run_status") in incomplete_statuses for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
