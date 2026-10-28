#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from common import find_brand, load_brands, resolve_path, skill_root

logger = logging.getLogger(__name__)


class DecodeError(RuntimeError):
    pass


def decode_with_zxingcpp(path: Path) -> list[str]:
    import zxingcpp
    from PIL import Image

    image = Image.open(path)
    results = zxingcpp.read_barcodes(image)
    return [item.text for item in results if getattr(item, "text", None)]


def decode_qr(path: Path) -> tuple[list[str], str]:
    errors: list[str] = []
    try:
        values = decode_with_zxingcpp(path)
        if values:
            return values, "zxingcpp"
    except ImportError as exc:
        errors.append(f"zxingcpp missing dependency: {exc}")
    except Exception as exc:  # noqa: BLE001 - decoder details are returned to the caller.
        errors.append(f"zxingcpp: {exc}")
    raise DecodeError("No QR code decoded. Details: " + "; ".join(errors))


def unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def build_result(brand: str, poster: Path, brands: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """验证海报二维码是否匹配品牌源二维码。

    Args:
        brand: 品牌名、别名或 brand_id。
        poster: 待验证的海报图片路径。
        brands: 可选的品牌配置列表。传入时避免重复加载；为 None 时自动加载。
    """
    root = skill_root()
    if brands is None:
        brands = load_brands(root)
    config = find_brand(brand, brands)
    if config is None:
        configured = sorted({alias for item in brands for alias in item.get("aliases", [])})
        return {
            "ok": False,
            "brand": brand,
            "poster": str(poster),
            "error": "UNSUPPORTED_BRAND",
            "message": "Unsupported brand.",
            "configured_brands": configured,
        }

    canonical = str(config.get("canonical_name") or brand)
    qr_config = config.get("qr_validation", {})
    # expected_qr_path 优先取 qr_validation 中的配置，fallback 到 assets.qr_path
    expected_qr_raw = qr_config.get("expected_qr_path") or config.get("assets", {}).get("qr_path")
    expected_value_config = qr_config.get("expected_value")
    if not expected_qr_raw:
        return {
            "ok": False,
            "brand": canonical,
            "poster": str(poster),
            "error": "EXPECTED_QR_NOT_CONFIGURED",
            "message": "Brand config must set qr_validation.expected_qr_path or assets.qr_path.",
        }

    expected_path = resolve_path(str(expected_qr_raw), root)
    if not expected_path.is_file():
        return {
            "ok": False,
            "brand": canonical,
            "poster": str(poster),
            "expected_qr": str(expected_path),
            "error": "EXPECTED_QR_NOT_FOUND",
            "message": "Brand source QR image does not exist.",
        }
    if not poster.is_file():
        return {
            "ok": False,
            "brand": canonical,
            "poster": str(poster),
            "expected_qr": str(expected_path),
            "error": "POSTER_NOT_FOUND",
            "message": "Poster image does not exist.",
        }

    try:
        expected_values, expected_decoder = decode_qr(expected_path)
        poster_values, poster_decoder = decode_qr(poster)
    except DecodeError as exc:
        return {
            "ok": False,
            "brand": canonical,
            "poster": str(poster),
            "expected_qr": str(expected_path),
            "error": "QR_DECODE_FAILED",
            "message": str(exc),
        }

    expected_unique = unique(expected_values)
    poster_unique = unique(poster_values)
    if len(expected_unique) != 1:
        return {
            "ok": False,
            "brand": canonical,
            "poster": str(poster),
            "expected_qr": str(expected_path),
            "error": "EXPECTED_QR_AMBIGUOUS",
            "message": "Source QR decoded to multiple distinct values.",
            "expected_values": expected_unique,
        }
    if expected_value_config and expected_unique[0] != expected_value_config:
        return {
            "ok": False,
            "brand": canonical,
            "poster": str(poster),
            "expected_qr": str(expected_path),
            "error": "EXPECTED_QR_VALUE_MISMATCH",
            "message": "Source QR content does not match expected_value from brand config.",
            "expected_value": expected_value_config,
            "source_value": expected_unique[0],
        }
    if len(poster_unique) != 1:
        return {
            "ok": False,
            "brand": canonical,
            "poster": str(poster),
            "expected_qr": str(expected_path),
            "error": "POSTER_QR_AMBIGUOUS",
            "message": "Poster decoded to zero or multiple distinct QR values.",
            "poster_values": poster_unique,
        }

    expected_value = expected_unique[0]
    poster_value = poster_unique[0]
    ok = expected_value == poster_value
    return {
        "ok": ok,
        "brand": canonical,
        "poster": str(poster),
        "expected_qr": str(expected_path),
        "expected_decoder": expected_decoder,
        "poster_decoder": poster_decoder,
        "expected_value": expected_value,
        "poster_value": poster_value,
        "error": None if ok else "QR_CONTENT_MISMATCH",
        "message": "QR code matches the brand source QR."
        if ok
        else "Poster QR content does not match the brand source QR.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate that a poster QR matches the configured brand source QR.")
    parser.add_argument("--brand", required=True, help="Configured brand name, alias, or brand_id.")
    parser.add_argument("--poster", required=True, help="Generated poster PNG/JPG path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    result = build_result(args.brand.strip(), Path(args.poster).expanduser().resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if result["ok"] else "FAIL"
        print(f"{status}: {result['message']}")
        for key in ("brand", "poster", "expected_qr", "error"):
            if result.get(key):
                print(f"{key}={result[key]}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
