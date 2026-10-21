#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import functools
import json
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


def _find_skills_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "lxx_share").is_dir():
            return parent
    return Path(__file__).resolve().parents[2]


_SKILLS_DIR = _find_skills_dir()
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from lxx_share.fog_config import get_section  # noqa: E402


BUILTIN_IMAGE_GEN_PROVIDER = "builtin_image_gen"
BUILTIN_IMAGE_GEN_ALIASES = {"builtin_image_gen", "codex_image_gen", "image_gen"}
DEFAULT_PROVIDER_PRIMARY = "kie"
DEFAULT_PROVIDER_FALLBACK = ""
DEFAULT_PROVIDERS = [DEFAULT_PROVIDER_PRIMARY]
DEFAULT_IMAGE_TASK_INITIAL_DELAY_SECONDS = 30
DEFAULT_IMAGE_TASK_POLL_ATTEMPTS = 40
DEFAULT_IMAGE_TASK_POLL_INTERVAL_SECONDS = 30

DEFAULT_KIE_BASE_URL = "https://api.kie.ai"
DEFAULT_KIE_UPLOAD_BASE_URL = "https://kieai.redpandaai.co"
DEFAULT_KIE_TEXT_MODEL = "gpt-image-2-text-to-image"
DEFAULT_KIE_IMAGE_MODEL = "gpt-image-2-image-to-image"
DEFAULT_KIE_ASPECT_RATIO = "auto"
DEFAULT_KIE_UPLOAD_PATH = "images/lx-haibao"

DEFAULT_AIHUBMIX_BASE_URL = "https://aihubmix.com/v1"
DEFAULT_AIHUBMIX_TASK_BASE_URL = "https://api.aihubmix.com/v1"
DEFAULT_AIHUBMIX_MODEL = "gpt-image-2"
DEFAULT_AIHUBMIX_ENDPOINT = "images_edits"
DEFAULT_AIHUBMIX_SIZE = "1024x1536"
DEFAULT_AIHUBMIX_QUALITY = "low"
DEFAULT_AIHUBMIX_OUTPUT_FORMAT = "png"
DEFAULT_AIHUBMIX_STREAM = False
DEFAULT_AIHUBMIX_PARTIAL_IMAGES = "2"

DEFAULT_APIMART_BASE_URL = "https://api.apimart.ai/v1"
DEFAULT_APIMART_MODEL = "gpt-image-2"

TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
NON_FALLBACK_STATUS_CODES = {400, 401, 403}


class ImageProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str = "",
        error_code: str = "",
        request_id: str = "",
        latency_ms: int | None = None,
        fallback_allowed: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.error_code = error_code
        self.request_id = request_id
        self.latency_ms = latency_ms
        self.fallback_allowed = fallback_allowed


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


@functools.lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    section = get_section("lx_haibao", Path(__file__))
    image_api = section.get("image_api")
    if isinstance(image_api, dict):
        return image_api
    return {}


def _provider_config(provider: str) -> dict[str, Any]:
    data = _load_config().get(provider)
    return data if isinstance(data, dict) else {}


def _top_config_value(key: str, env_name: str, default: str = "") -> str:
    config = _load_config()
    value = config.get(key)
    if value not in (None, ""):
        return str(value)
    return os.environ.get(env_name, "") or default


def _provider_value(
    provider: str,
    key: str,
    env_names: list[str],
    default: str = "",
    *,
    legacy_key: str | None = None,
) -> str:
    provider_config = _provider_config(provider)
    value = provider_config.get(key)
    if value not in (None, ""):
        return str(value)

    config = _load_config()
    if legacy_key and provider == "apimart":
        legacy_value = config.get(legacy_key)
        if legacy_value not in (None, ""):
            return str(legacy_value)

    for env_name in env_names:
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value
    return default


def _provider_bool(provider: str, key: str, env_names: list[str], default: bool) -> bool:
    provider_config = _provider_config(provider)
    if key in provider_config:
        return _as_bool(provider_config.get(key), default)
    for env_name in env_names:
        if env_name in os.environ:
            return _as_bool(os.environ.get(env_name), default)
    return default


def _normalize_provider_name(name: str) -> str:
    normalized = name.strip().lower().replace("-", "_")
    if normalized in BUILTIN_IMAGE_GEN_ALIASES:
        return BUILTIN_IMAGE_GEN_PROVIDER
    return normalized


def _configured_provider_names() -> list[str]:
    configured = _load_config()
    providers_value = configured.get("providers")
    raw_names: list[str] = []
    if isinstance(providers_value, list):
        raw_names.extend(str(item) for item in providers_value)
    if not raw_names:
        raw_names.extend(DEFAULT_PROVIDERS)
    primary = _top_config_value("provider_primary", "IMAGE_PROVIDER_PRIMARY", DEFAULT_PROVIDER_PRIMARY).strip()
    fallback = _top_config_value("provider_fallback", "IMAGE_PROVIDER_FALLBACK", DEFAULT_PROVIDER_FALLBACK).strip()
    raw_names.extend([primary, fallback])
    names: list[str] = []
    for name in raw_names:
        normalized = _normalize_provider_name(name)
        if normalized and normalized != "none" and normalized not in names:
            names.append(normalized)
    return names or [DEFAULT_PROVIDER_PRIMARY]


def _build_session() -> requests.Session:
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=sorted(TRANSIENT_STATUS_CODES),
        # Image-generation POSTs are not idempotent. Retrying them after a read
        # timeout can submit duplicate paid jobs.
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _response_request_id(response: requests.Response | None) -> str:
    if response is None:
        return ""
    for header in ("x-request-id", "x-tt-logid", "request-id"):
        value = response.headers.get(header)
        if value:
            return value
    return ""


def _extract_error(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type") or ""
        message = error.get("message") or ""
        return str(code), str(message)
    if isinstance(error, str):
        return "", error
    code = payload.get("code")
    message = payload.get("message") or payload.get("msg") or ""
    return (str(code) if code not in (None, "") else "", str(message) if message else "")


def _is_transient_code(code: str) -> bool:
    if not code:
        return False
    try:
        return int(code) in TRANSIENT_STATUS_CODES
    except ValueError:
        lowered = code.lower()
        return any(token in lowered for token in ("timeout", "temporar", "unavailable", "rate", "limit", "internal"))


def _fallback_allowed_for_status(status_code: int) -> bool:
    if status_code in NON_FALLBACK_STATUS_CODES:
        return False
    return status_code in TRANSIENT_STATUS_CODES or status_code >= 500


def _http_error(
    provider: str,
    model: str,
    response: requests.Response,
    started: float,
) -> ImageProviderError:
    request_id = _response_request_id(response)
    try:
        payload: Any = response.json()
    except ValueError:
        payload = {}
    payload_code, payload_message = _extract_error(payload)
    error_code = payload_code or str(response.status_code)
    message = payload_message or response.text[:300] or response.reason
    return ImageProviderError(
        f"{provider} HTTP {response.status_code}: {message}",
        provider=provider,
        model=model,
        error_code=error_code,
        request_id=request_id,
        latency_ms=_elapsed_ms(started),
        fallback_allowed=_fallback_allowed_for_status(response.status_code),
    )


def _network_error(provider: str, model: str, exc: Exception, started: float) -> ImageProviderError:
    return ImageProviderError(
        f"{provider} 网络错误：{exc}",
        provider=provider,
        model=model,
        error_code=exc.__class__.__name__,
        latency_ms=_elapsed_ms(started),
        fallback_allowed=True,
    )


def _write_b64_image(b64_json: str, output_path: Path) -> None:
    payload = b64_json
    if "," in payload and payload.strip().lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"Base64 图片数据解码失败：{exc}") from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)


def _download(url: str, output_path: Path) -> None:
    timeout = _env_int("POSTER_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 300)
    retries = _env_int("POSTER_IMAGE_DOWNLOAD_RETRIES", 3)
    delay = _env_int("POSTER_IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS", 2)
    last_error: Exception | None = None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            logger.info("开始下载生成图片：attempt=%d/%d output=%s", attempt + 1, retries, output_path)
            response = requests.get(url, stream=True, timeout=timeout)
            response.raise_for_status()
            with output_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        handle.write(chunk)
            logger.info("图片下载完成：output=%s", output_path)
            return
        except Exception as exc:  # noqa: BLE001 - keep the original download failure.
            last_error = exc
            logger.warning("图片下载第 %d 次重试失败：%s", attempt + 1, exc)
            time.sleep(delay)
    raise RuntimeError(f"图片下载失败：{last_error}")


def image_to_data_url(path: Path) -> str:
    data = path.read_bytes()
    ext = path.suffix.lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64,{base64.b64encode(data).decode('ascii')}"


def _extract_task_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            task_id = item.get("task_id") or item.get("id")
            return str(task_id) if task_id else None
    if isinstance(data, dict):
        task_id = data.get("task_id") or data.get("taskId") or data.get("id")
        return str(task_id) if task_id else None
    output = payload.get("output")
    if isinstance(output, list) and output:
        item = output[0]
        if isinstance(item, dict):
            task_id = item.get("task_id") or item.get("taskId") or item.get("id")
            return str(task_id) if task_id else None
    if isinstance(output, dict):
        task_id = output.get("task_id") or output.get("taskId") or output.get("id")
        return str(task_id) if task_id else None
    return None


def _extract_direct_url(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            url = item.get("url") or item.get("image_url")
            if isinstance(url, list):
                return str(url[0]) if url else None
            return str(url) if url else None
    output = payload.get("output")
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            url = first.get("url") or first.get("image_url")
            if isinstance(url, list):
                return str(url[0]) if url else None
            return str(url) if url else None
    if isinstance(output, str):
        return output
    return None


def _extract_b64_json(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            b64_json = item.get("b64_json")
            return str(b64_json) if b64_json else None
    return None


def _extract_completed_url(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, dict):
        result_json = data.get("resultJson")
        if isinstance(result_json, str) and result_json.strip():
            try:
                result_payload = json.loads(result_json)
            except ValueError:
                result_payload = {}
            if isinstance(result_payload, dict):
                result_urls = result_payload.get("resultUrls")
                if isinstance(result_urls, list) and result_urls:
                    return str(result_urls[0])
                for key in ("url", "imageUrl", "image_url"):
                    value = result_payload.get(key)
                    if value:
                        return str(value)
    if isinstance(data, dict):
        result = data.get("result")
    else:
        result = payload.get("result") or payload.get("output")
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            url = first.get("url") or first.get("image_url")
            if isinstance(url, list):
                return str(url[0]) if url else None
            return str(url) if url else None
    if not isinstance(result, dict):
        return None
    images = result.get("images")
    if not isinstance(images, list) or not images:
        return None
    first = images[0]
    if not isinstance(first, dict):
        return None
    url = first.get("url") or first.get("image_url")
    if isinstance(url, list):
        return str(url[0]) if url else None
    return str(url) if url else None


class ImageProvider:
    name = "provider"

    def __init__(self) -> None:
        self.session = _build_session()

    @property
    def api_key(self) -> str:
        raise NotImplementedError

    @property
    def base_url(self) -> str:
        raise NotImplementedError

    @property
    def model(self) -> str:
        raise NotImplementedError

    def generate(
        self,
        *,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
        size: str,
        resolution: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        result: dict[str, Any] = {
            "provider": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "reachable": False,
            "http_status": None,
            "latency_ms": None,
            "status": "unknown",
            "error": "",
        }
        try:
            response = self.session.get(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                timeout=_env_int("POSTER_PROVIDER_HEALTH_TIMEOUT_SECONDS", 8),
            )
            result["reachable"] = True
            result["http_status"] = response.status_code
            result["status"] = "ok" if self.api_key else "missing_api_key"
        except requests.exceptions.RequestException as exc:
            result["status"] = "unreachable"
            result["error"] = str(exc)
        result["latency_ms"] = _elapsed_ms(started)
        return result


class KIEProvider(ImageProvider):
    name = "kie"

    @property
    def api_key(self) -> str:
        return _provider_value(self.name, "api_key", ["KIE_API_KEY"])

    @property
    def base_url(self) -> str:
        return _provider_value(self.name, "base_url", ["KIE_BASE_URL"], DEFAULT_KIE_BASE_URL).rstrip("/")

    @property
    def upload_base_url(self) -> str:
        return _provider_value(
            self.name,
            "upload_base_url",
            ["KIE_UPLOAD_BASE_URL"],
            DEFAULT_KIE_UPLOAD_BASE_URL,
        ).rstrip("/")

    @property
    def text_model(self) -> str:
        return _provider_value(self.name, "text_model", ["KIE_TEXT_MODEL", "KIE_MODEL"], DEFAULT_KIE_TEXT_MODEL)

    @property
    def image_model(self) -> str:
        return _provider_value(self.name, "image_model", ["KIE_IMAGE_MODEL", "KIE_MODEL"], DEFAULT_KIE_IMAGE_MODEL)

    @property
    def model(self) -> str:
        return self.image_model

    @property
    def aspect_ratio(self) -> str:
        return _provider_value(self.name, "aspect_ratio", ["KIE_ASPECT_RATIO"], DEFAULT_KIE_ASPECT_RATIO)

    @property
    def upload_path(self) -> str:
        return _provider_value(self.name, "upload_path", ["KIE_UPLOAD_PATH"], DEFAULT_KIE_UPLOAD_PATH)

    @property
    def callback_url(self) -> str:
        return _provider_value(self.name, "callback_url", ["KIE_CALLBACK_URL"])

    def _request_aspect_ratio(self, size: str) -> str:
        configured = self.aspect_ratio.strip()
        raw_size = (size or "").strip()
        if configured and configured.lower() not in {"auto", "default"}:
            return configured
        if ":" in raw_size:
            return raw_size
        return configured or DEFAULT_KIE_ASPECT_RATIO

    def _upload_image(self, path: Path, *, index: int, started: float, model_id: str) -> str:
        upload_timeout = _env_int("POSTER_KIE_UPLOAD_TIMEOUT_SECONDS", 120)
        content_type = mimetypes.guess_type(path.name)[0] or "image/png"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        file_name = f"{int(time.time())}-{index}-{path.name}"
        try:
            with path.open("rb") as handle:
                response = self.session.post(
                    f"{self.upload_base_url}/api/file-stream-upload",
                    headers=headers,
                    data={"uploadPath": self.upload_path, "fileName": file_name},
                    files={"file": (path.name, handle, content_type)},
                    timeout=upload_timeout,
                )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
            raise _network_error(self.name, model_id, exc, started) from exc
        except requests.exceptions.RequestException as exc:
            raise _network_error(self.name, model_id, exc, started) from exc

        if response.status_code >= 400:
            raise _http_error(self.name, model_id, response, started)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                f"{self.name} 文件上传返回非 JSON 响应：{exc}",
                provider=self.name,
                model=model_id,
                request_id=_response_request_id(response),
                latency_ms=_elapsed_ms(started),
                error_code="invalid_upload_json",
                fallback_allowed=False,
            ) from exc
        if payload.get("success") is False or payload.get("code", 200) != 200:
            code, message = _extract_error(payload)
            raise ImageProviderError(
                f"{self.name} 文件上传失败：{message or code or payload}",
                provider=self.name,
                model=model_id,
                request_id=_response_request_id(response),
                latency_ms=_elapsed_ms(started),
                error_code=code or "upload_failed",
                fallback_allowed=_is_transient_code(code),
            )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        url = data.get("downloadUrl") or data.get("fileUrl") or data.get("url")
        if not url:
            raise ImageProviderError(
                f"{self.name} 文件上传成功但未返回 downloadUrl/fileUrl。",
                provider=self.name,
                model=model_id,
                request_id=_response_request_id(response),
                latency_ms=_elapsed_ms(started),
                error_code="missing_upload_url",
                fallback_allowed=False,
            )
        return str(url)

    def _upload_reference_images(self, paths: list[Path], *, started: float, model_id: str) -> list[str]:
        urls: list[str] = []
        for index, path in enumerate((item for item in paths if item.is_file()), start=1):
            logger.info("上传 KIE 参考图：index=%d path=%s", index, path)
            urls.append(self._upload_image(path, index=index, started=started, model_id=model_id))
        return urls

    def _poll_task(
        self,
        *,
        task_id: str,
        headers: dict[str, str],
        output_path: Path,
        model_id: str,
        request_id: str,
        started: float,
        request_timeout: int,
    ) -> dict[str, Any]:
        initial_delay = _env_int("POSTER_IMAGE_TASK_INITIAL_DELAY_SECONDS", DEFAULT_IMAGE_TASK_INITIAL_DELAY_SECONDS)
        attempts = _env_int("POSTER_IMAGE_TASK_POLL_ATTEMPTS", DEFAULT_IMAGE_TASK_POLL_ATTEMPTS)
        interval = _env_int("POSTER_IMAGE_TASK_POLL_INTERVAL_SECONDS", DEFAULT_IMAGE_TASK_POLL_INTERVAL_SECONDS)
        logger.info(
            "KIE 任务已提交：task_id=%s initial_delay=%ss poll_attempts=%d poll_interval=%ss",
            task_id,
            initial_delay,
            attempts,
            interval,
        )
        time.sleep(initial_delay)
        for attempt in range(1, attempts + 1):
            try:
                query = self.session.get(
                    f"{self.base_url}/api/v1/jobs/recordInfo",
                    headers=headers,
                    params={"taskId": task_id},
                    timeout=request_timeout,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
            except requests.exceptions.RequestException as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
            if query.status_code >= 400:
                raise _http_error(self.name, model_id, query, started)
            task_request_id = _response_request_id(query) or request_id
            try:
                task_payload = query.json()
            except ValueError as exc:
                raise ImageProviderError(
                    f"{self.name} 任务轮询返回非 JSON 响应：{exc}",
                    provider=self.name,
                    model=model_id,
                    request_id=task_request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code="invalid_task_json",
                    fallback_allowed=False,
                ) from exc
            if task_payload.get("code", 200) not in (200, "200") and task_payload.get("success") is not True:
                code, message = _extract_error(task_payload)
                raise ImageProviderError(
                    f"{self.name} 任务查询失败：{message or code or task_payload}",
                    provider=self.name,
                    model=model_id,
                    request_id=task_request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code=code or "task_query_failed",
                    fallback_allowed=_is_transient_code(code),
                )

            data = task_payload.get("data") if isinstance(task_payload.get("data"), dict) else {}
            state = str(data.get("state") or data.get("status") or "").strip().lower()
            logger.info("KIE 任务轮询：task_id=%s poll=%d/%d state=%s", task_id, attempt, attempts, state or "unknown")
            if state == "success":
                image_url = _extract_completed_url(task_payload)
                if not image_url:
                    raise ImageProviderError(
                        f"{self.name} 任务成功，但响应中没有结果图片 URL。",
                        provider=self.name,
                        model=model_id,
                        request_id=task_request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code="missing_completed_url",
                        fallback_allowed=False,
                    )
                try:
                    _download(image_url, output_path)
                except RuntimeError as exc:
                    raise ImageProviderError(
                        f"{self.name} 图片下载失败：{exc}",
                        provider=self.name,
                        model=model_id,
                        request_id=task_request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code="image_download_failed",
                        fallback_allowed=True,
                    ) from exc
                return {
                    "status": "success",
                    "filepath": str(output_path),
                    "url": image_url,
                    "task_id": task_id,
                    "provider": self.name,
                    "model": model_id,
                    "request_id": task_request_id,
                    "latency_ms": _elapsed_ms(started),
                    "error_code": "",
                }
            if state == "fail":
                fail_code = str(data.get("failCode") or "task_failed")
                fail_msg = str(data.get("failMsg") or task_payload)
                raise ImageProviderError(
                    f"{self.name} 任务失败：{fail_msg}",
                    provider=self.name,
                    model=model_id,
                    request_id=task_request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code=fail_code,
                    fallback_allowed=_is_transient_code(fail_code),
                )
            time.sleep(interval)

        raise ImageProviderError(
            f"{self.name} 任务超时：{task_id}",
            provider=self.name,
            model=model_id,
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            error_code="task_timeout",
            fallback_allowed=True,
        )

    def generate(
        self,
        *,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
        size: str,
        resolution: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        valid_reference_images = [path for path in reference_images if path.is_file()]
        model_id = model or (self.image_model if valid_reference_images else self.text_model)
        if not self.api_key:
            raise ImageProviderError(
                "缺少 KIE API Key：请配置 KIE_API_KEY 或 config/fog_config.yaml 的 lx_haibao.image_api.kie.api_key。",
                provider=self.name,
                model=model_id,
                error_code="missing_api_key",
                fallback_allowed=True,
            )

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        request_timeout = _env_int("POSTER_IMAGE_REQUEST_TIMEOUT_SECONDS", 120)
        started = time.perf_counter()
        input_payload: dict[str, Any] = {
            "prompt": prompt,
            "aspect_ratio": self._request_aspect_ratio(size),
        }
        uploaded_urls: list[str] = []
        if valid_reference_images:
            uploaded_urls = self._upload_reference_images(valid_reference_images, started=started, model_id=model_id)
            input_payload["input_urls"] = uploaded_urls
        payload: dict[str, Any] = {"model": model_id, "input": input_payload}
        if self.callback_url:
            payload["callBackUrl"] = self.callback_url

        logger.info(
            "提交 KIE 生图任务：model=%s aspect_ratio=%s reference_images=%d timeout=%ss",
            model_id,
            input_payload["aspect_ratio"],
            len(uploaded_urls),
            request_timeout,
        )
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/jobs/createTask",
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
            raise _network_error(self.name, model_id, exc, started) from exc
        except requests.exceptions.RequestException as exc:
            raise _network_error(self.name, model_id, exc, started) from exc

        if response.status_code >= 400:
            raise _http_error(self.name, model_id, response, started)
        request_id = _response_request_id(response)
        try:
            submit_payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                f"{self.name} 返回非 JSON 响应：{exc}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="invalid_json",
                fallback_allowed=False,
            ) from exc
        if submit_payload.get("code", 200) not in (200, "200") and submit_payload.get("success") is not True:
            code, message = _extract_error(submit_payload)
            raise ImageProviderError(
                f"{self.name} 返回错误：{message or code or submit_payload}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code=code or "provider_error",
                fallback_allowed=_is_transient_code(code),
            )
        task_id = _extract_task_id(submit_payload)
        if not task_id:
            raise ImageProviderError(
                f"{self.name} 未返回 taskId。",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="missing_task_id",
                fallback_allowed=False,
            )
        result = self._poll_task(
            task_id=task_id,
            headers=headers,
            output_path=output_path,
            model_id=model_id,
            request_id=request_id,
            started=started,
            request_timeout=request_timeout,
        )
        result["reference_image_count"] = len(uploaded_urls)
        result["request"] = {
            "endpoint": "jobs/createTask",
            "aspect_ratio": input_payload["aspect_ratio"],
            "model": model_id,
            "uploaded_reference_count": len(uploaded_urls),
        }
        return result


class APIMartProvider(ImageProvider):
    name = "apimart"

    @property
    def api_key(self) -> str:
        return _provider_value(
            self.name,
            "api_key",
            ["APIMART_API_KEY", "OPENAI_API_KEY"],
            legacy_key="api_key",
        )

    @property
    def base_url(self) -> str:
        return _provider_value(
            self.name,
            "base_url",
            ["APIMART_BASE_URL", "POSTER_IMAGE_API_BASE_URL"],
            DEFAULT_APIMART_BASE_URL,
            legacy_key="base_url",
        ).rstrip("/")

    @property
    def model(self) -> str:
        return _provider_value(
            self.name,
            "model",
            ["APIMART_IMAGE_MODEL", "POSTER_IMAGE_MODEL"],
            DEFAULT_APIMART_MODEL,
            legacy_key="model",
        )

    def _provider_payload_error(self, payload: dict[str, Any], model_id: str, started: float) -> ImageProviderError:
        code, message = _extract_error(payload)
        if not code:
            code = "provider_error"
        return ImageProviderError(
            f"{self.name} 返回错误：{message or code}",
            provider=self.name,
            model=model_id,
            error_code=code,
            latency_ms=_elapsed_ms(started),
            fallback_allowed=_is_transient_code(code),
        )

    def generate(
        self,
        *,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
        size: str,
        resolution: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        model_id = model or self.model
        if not self.api_key:
            raise ImageProviderError(
                "缺少 APIMart API Key：请配置 APIMART_API_KEY、OPENAI_API_KEY 或 config/fog_config.yaml 的 lx_haibao.image_api.apimart.api_key。",
                provider=self.name,
                model=model_id,
                error_code="missing_api_key",
                fallback_allowed=True,
            )

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload: dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "resolution": resolution,
        }
        image_urls = [image_to_data_url(path) for path in reference_images if path.is_file()]
        if image_urls:
            payload["image_urls"] = image_urls

        request_timeout = _env_int("POSTER_IMAGE_REQUEST_TIMEOUT_SECONDS", 30)
        logger.info(
            "提交 APIMart 生图请求：model=%s size=%s resolution=%s reference_images=%d timeout=%ss",
            payload["model"],
            size,
            resolution,
            len(image_urls),
            request_timeout,
        )

        started = time.perf_counter()
        response: requests.Response | None = None
        try:
            response = self.session.post(
                f"{self.base_url}/images/generations",
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
            raise _network_error(self.name, model_id, exc, started) from exc
        except requests.exceptions.RequestException as exc:
            raise _network_error(self.name, model_id, exc, started) from exc

        if response.status_code >= 400:
            raise _http_error(self.name, model_id, response, started)

        request_id = _response_request_id(response)
        try:
            submit_payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                f"{self.name} 返回非 JSON 响应：{exc}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="invalid_json",
                fallback_allowed=False,
            ) from exc

        if submit_payload.get("code", 200) != 200:
            raise self._provider_payload_error(submit_payload, model_id, started)

        direct_url = _extract_direct_url(submit_payload)
        if direct_url:
            try:
                _download(direct_url, output_path)
            except RuntimeError as exc:
                raise ImageProviderError(
                    f"{self.name} 图片下载失败：{exc}",
                    provider=self.name,
                    model=model_id,
                    request_id=request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code="image_download_failed",
                    fallback_allowed=True,
                ) from exc
            return {
                "status": "success",
                "filepath": str(output_path),
                "url": direct_url,
                "task_id": None,
                "provider": self.name,
                "model": model_id,
                "request_id": request_id,
                "latency_ms": _elapsed_ms(started),
                "error_code": "",
                "reference_image_count": len(image_urls),
                "request": {"size": size, "resolution": resolution, "model": model_id},
            }

        task_id = _extract_task_id(submit_payload)
        if not task_id:
            raise ImageProviderError(
                f"{self.name} 未返回 task_id 或图片 URL。",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="missing_task_id",
                fallback_allowed=False,
            )

        initial_delay = _env_int("POSTER_IMAGE_TASK_INITIAL_DELAY_SECONDS", DEFAULT_IMAGE_TASK_INITIAL_DELAY_SECONDS)
        attempts = _env_int("POSTER_IMAGE_TASK_POLL_ATTEMPTS", DEFAULT_IMAGE_TASK_POLL_ATTEMPTS)
        interval = _env_int("POSTER_IMAGE_TASK_POLL_INTERVAL_SECONDS", DEFAULT_IMAGE_TASK_POLL_INTERVAL_SECONDS)
        logger.info(
            "APIMart 任务已提交：task_id=%s initial_delay=%ss poll_attempts=%d poll_interval=%ss",
            task_id,
            initial_delay,
            attempts,
            interval,
        )
        time.sleep(initial_delay)
        for attempt in range(1, attempts + 1):
            try:
                query = self.session.get(f"{self.base_url}/tasks/{task_id}", headers=headers, timeout=request_timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
            except requests.exceptions.RequestException as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
            if query.status_code >= 400:
                raise _http_error(self.name, model_id, query, started)
            task_request_id = _response_request_id(query) or request_id
            try:
                task_payload = query.json()
            except ValueError as exc:
                raise ImageProviderError(
                    f"{self.name} 任务轮询返回非 JSON 响应：{exc}",
                    provider=self.name,
                    model=model_id,
                    request_id=task_request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code="invalid_task_json",
                    fallback_allowed=False,
                ) from exc
            if task_payload.get("code", 200) != 200:
                logger.info(
                    "APIMart 任务轮询：task_id=%s poll=%d/%d code=%s，继续等待",
                    task_id,
                    attempt,
                    attempts,
                    task_payload.get("code"),
                )
                time.sleep(interval)
                continue
            data = task_payload.get("data") if isinstance(task_payload.get("data"), dict) else {}
            status = str(data.get("status") or "").lower()
            logger.info("APIMart 任务轮询：task_id=%s poll=%d/%d status=%s", task_id, attempt, attempts, status or "unknown")
            if status == "completed":
                image_url = _extract_completed_url(task_payload)
                if not image_url:
                    raise ImageProviderError(
                        f"{self.name} 任务完成，但响应中没有图片 URL。",
                        provider=self.name,
                        model=model_id,
                        request_id=task_request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code="missing_completed_url",
                        fallback_allowed=False,
                    )
                try:
                    _download(image_url, output_path)
                except RuntimeError as exc:
                    raise ImageProviderError(
                        f"{self.name} 图片下载失败：{exc}",
                        provider=self.name,
                        model=model_id,
                        request_id=task_request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code="image_download_failed",
                        fallback_allowed=True,
                    ) from exc
                return {
                    "status": "success",
                    "filepath": str(output_path),
                    "url": image_url,
                    "task_id": task_id,
                    "provider": self.name,
                    "model": model_id,
                    "request_id": task_request_id,
                    "latency_ms": _elapsed_ms(started),
                    "error_code": "",
                    "reference_image_count": len(image_urls),
                    "request": {"size": size, "resolution": resolution, "model": model_id},
                }
            if status == "failed":
                error = data.get("error") or task_payload
                logger.error("APIMart 任务失败：task_id=%s error=%s", task_id, error)
                raise ImageProviderError(
                    json.dumps(error, ensure_ascii=False),
                    provider=self.name,
                    model=model_id,
                    request_id=task_request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code="task_failed",
                    fallback_allowed=False,
                )
            time.sleep(interval)

        raise ImageProviderError(
            f"{self.name} 任务超时：{task_id}",
            provider=self.name,
            model=model_id,
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            error_code="task_timeout",
            fallback_allowed=True,
        )


class AIHubMixProvider(ImageProvider):
    name = "aihubmix"

    @property
    def api_key(self) -> str:
        return _provider_value(self.name, "api_key", ["AIHUBMIX_API_KEY"])

    @property
    def base_url(self) -> str:
        return _provider_value(self.name, "base_url", ["AIHUBMIX_BASE_URL"], DEFAULT_AIHUBMIX_BASE_URL).rstrip("/")

    @property
    def task_base_url(self) -> str:
        return _provider_value(
            self.name,
            "task_base_url",
            ["AIHUBMIX_TASK_BASE_URL"],
            DEFAULT_AIHUBMIX_TASK_BASE_URL,
        ).rstrip("/")

    @property
    def model(self) -> str:
        return _provider_value(self.name, "model", ["AIHUBMIX_IMAGE_MODEL"], DEFAULT_AIHUBMIX_MODEL)

    @property
    def endpoint(self) -> str:
        return _provider_value(self.name, "endpoint", ["AIHUBMIX_IMAGE_ENDPOINT"], DEFAULT_AIHUBMIX_ENDPOINT)

    @property
    def quality(self) -> str:
        return _provider_value(self.name, "quality", ["AIHUBMIX_IMAGE_QUALITY"], DEFAULT_AIHUBMIX_QUALITY)

    @property
    def output_format(self) -> str:
        return _provider_value(self.name, "output_format", ["AIHUBMIX_OUTPUT_FORMAT"], DEFAULT_AIHUBMIX_OUTPUT_FORMAT)

    @property
    def stream(self) -> bool:
        return _provider_bool(self.name, "stream", ["AIHUBMIX_IMAGE_STREAM"], DEFAULT_AIHUBMIX_STREAM)

    @property
    def partial_images(self) -> int:
        value = _provider_value(
            self.name,
            "partial_images",
            ["AIHUBMIX_PARTIAL_IMAGES"],
            DEFAULT_AIHUBMIX_PARTIAL_IMAGES,
        )
        try:
            return max(0, min(int(value), 3))
        except ValueError:
            return int(DEFAULT_AIHUBMIX_PARTIAL_IMAGES)

    @staticmethod
    def _request_size(size: str, resolution: str) -> str:
        raw_size = (size or "").strip()
        if "x" in raw_size.lower() and raw_size[0].isdigit():
            return raw_size.lower()
        ratio_to_portrait = {
            "1:1": "1024x1024",
            "4:3": "1536x1024",
            "3:4": "1024x1536",
            "16:9": "1536x864",
            "9:16": "1024x1536",
            "3:2": "1536x1024",
            "2:3": "1024x1536",
        }
        if raw_size in ratio_to_portrait:
            return ratio_to_portrait[raw_size]
        if (resolution or "").lower() in {"1k", "2k", "4k"}:
            return (resolution or "").upper()
        return DEFAULT_AIHUBMIX_SIZE

    def _handle_sync_response(
        self,
        *,
        payload: dict[str, Any],
        output_path: Path,
        model_id: str,
        request_id: str,
        started: float,
    ) -> dict[str, Any] | None:
        b64_json = _extract_b64_json(payload)
        direct_url = _extract_direct_url(payload)
        if not b64_json and not direct_url:
            return None
        try:
            if b64_json:
                _write_b64_image(b64_json, output_path)
            elif direct_url:
                _download(direct_url, output_path)
        except RuntimeError as exc:
            raise ImageProviderError(
                f"{self.name} 图片保存失败：{exc}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="image_save_failed",
                fallback_allowed=True,
            ) from exc
        return {
            "status": "success",
            "filepath": str(output_path),
            "url": direct_url or None,
            "task_id": None,
            "provider": self.name,
            "model": model_id,
            "request_id": request_id,
            "latency_ms": _elapsed_ms(started),
            "error_code": "",
        }

    def _handle_stream_response(
        self,
        *,
        response: requests.Response,
        output_path: Path,
        model_id: str,
        request_id: str,
        started: float,
    ) -> dict[str, Any]:
        last_b64_json = ""
        event_count = 0
        last_event_type = ""
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except ValueError as exc:
                    raise ImageProviderError(
                        f"{self.name} 流式响应包含非 JSON 事件：{exc}",
                        provider=self.name,
                        model=model_id,
                        request_id=request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code="invalid_stream_json",
                        fallback_allowed=False,
                    ) from exc
                event_count += 1
                last_event_type = str(payload.get("type") or "")
                error = payload.get("error")
                if error:
                    code, message = _extract_error(payload)
                    raise ImageProviderError(
                        f"{self.name} 流式响应返回错误：{message or code or error}",
                        provider=self.name,
                        model=model_id,
                        request_id=request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code=code or "stream_error",
                        fallback_allowed=_is_transient_code(code),
                    )
                b64_json = payload.get("b64_json") or payload.get("partial_image_b64") or _extract_b64_json(payload)
                if b64_json:
                    last_b64_json = str(b64_json)
                    logger.info(
                        "AIHubMix 流式图片事件：type=%s partial_index=%s elapsed_ms=%d",
                        last_event_type or "unknown",
                        payload.get("partial_image_index"),
                        _elapsed_ms(started),
                    )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
            raise _network_error(self.name, model_id, exc, started) from exc
        except requests.exceptions.RequestException as exc:
            raise _network_error(self.name, model_id, exc, started) from exc

        if not last_b64_json:
            raise ImageProviderError(
                f"{self.name} 流式响应未返回图片数据。",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="missing_stream_image",
                fallback_allowed=True,
            )
        try:
            _write_b64_image(last_b64_json, output_path)
        except RuntimeError as exc:
            raise ImageProviderError(
                f"{self.name} 流式图片保存失败：{exc}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="image_save_failed",
                fallback_allowed=True,
            ) from exc
        return {
            "status": "success",
            "filepath": str(output_path),
            "url": None,
            "task_id": None,
            "provider": self.name,
            "model": model_id,
            "request_id": request_id,
            "latency_ms": _elapsed_ms(started),
            "error_code": "",
            "stream_event_count": event_count,
            "stream_last_event_type": last_event_type,
        }

    def _poll_task(
        self,
        *,
        task_id: str,
        headers: dict[str, str],
        output_path: Path,
        model_id: str,
        request_id: str,
        started: float,
        request_timeout: int,
    ) -> dict[str, Any]:
        initial_delay = _env_int("POSTER_IMAGE_TASK_INITIAL_DELAY_SECONDS", DEFAULT_IMAGE_TASK_INITIAL_DELAY_SECONDS)
        attempts = _env_int("POSTER_IMAGE_TASK_POLL_ATTEMPTS", DEFAULT_IMAGE_TASK_POLL_ATTEMPTS)
        interval = _env_int("POSTER_IMAGE_TASK_POLL_INTERVAL_SECONDS", DEFAULT_IMAGE_TASK_POLL_INTERVAL_SECONDS)
        logger.info(
            "AIHubMix 任务已提交：task_id=%s initial_delay=%ss poll_attempts=%d poll_interval=%ss",
            task_id,
            initial_delay,
            attempts,
            interval,
        )
        time.sleep(initial_delay)
        for attempt in range(1, attempts + 1):
            try:
                query = self.session.get(f"{self.task_base_url}/tasks/{task_id}", headers=headers, timeout=request_timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
            except requests.exceptions.RequestException as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
            if query.status_code >= 400:
                raise _http_error(self.name, model_id, query, started)
            task_request_id = _response_request_id(query) or request_id
            try:
                task_payload = query.json()
            except ValueError as exc:
                raise ImageProviderError(
                    f"{self.name} 任务轮询返回非 JSON 响应：{exc}",
                    provider=self.name,
                    model=model_id,
                    request_id=task_request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code="invalid_task_json",
                    fallback_allowed=False,
                ) from exc
            if task_payload.get("code", 200) != 200:
                logger.info(
                    "AIHubMix 任务轮询：task_id=%s poll=%d/%d code=%s，继续等待",
                    task_id,
                    attempt,
                    attempts,
                    task_payload.get("code"),
                )
                time.sleep(interval)
                continue
            data = task_payload.get("data") if isinstance(task_payload.get("data"), dict) else {}
            status = str(data.get("status") or "").lower()
            logger.info("AIHubMix 任务轮询：task_id=%s poll=%d/%d status=%s", task_id, attempt, attempts, status or "unknown")
            if status == "completed":
                image_url = _extract_completed_url(task_payload)
                if not image_url:
                    raise ImageProviderError(
                        f"{self.name} 任务完成，但响应中没有图片 URL。",
                        provider=self.name,
                        model=model_id,
                        request_id=task_request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code="missing_completed_url",
                        fallback_allowed=False,
                    )
                try:
                    _download(image_url, output_path)
                except RuntimeError as exc:
                    raise ImageProviderError(
                        f"{self.name} 图片下载失败：{exc}",
                        provider=self.name,
                        model=model_id,
                        request_id=task_request_id,
                        latency_ms=_elapsed_ms(started),
                        error_code="image_download_failed",
                        fallback_allowed=True,
                    ) from exc
                return {
                    "status": "success",
                    "filepath": str(output_path),
                    "url": image_url,
                    "task_id": task_id,
                    "provider": self.name,
                    "model": model_id,
                    "request_id": task_request_id,
                    "latency_ms": _elapsed_ms(started),
                    "error_code": "",
                }
            if status == "failed":
                error = data.get("error") or task_payload
                logger.error("AIHubMix 任务失败：task_id=%s error=%s", task_id, error)
                raise ImageProviderError(
                    json.dumps(error, ensure_ascii=False),
                    provider=self.name,
                    model=model_id,
                    request_id=task_request_id,
                    latency_ms=_elapsed_ms(started),
                    error_code="task_failed",
                    fallback_allowed=False,
                )
            time.sleep(interval)

        raise ImageProviderError(
            f"{self.name} 任务超时：{task_id}",
            provider=self.name,
            model=model_id,
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            error_code="task_timeout",
            fallback_allowed=True,
        )

    def _predictions_payload(self, prompt: str, image_urls: list[str], size: str, resolution: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "input": {
                "prompt": prompt,
                "size": self._request_size(size, resolution),
                "n": 1,
                "quality": self.quality,
                "output_format": self.output_format,
            }
        }
        if image_urls:
            payload["input"]["image"] = image_urls[0]
            payload["input"]["images"] = image_urls
        return payload

    def _images_payload(self, prompt: str, image_urls: list[str], size: str, resolution: str, model_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "n": 1,
            "size": self._request_size(size, resolution),
            "quality": self.quality,
            "output_format": self.output_format,
        }
        if image_urls:
            payload["image_urls"] = image_urls
        return payload

    def _generate_with_edits(
        self,
        *,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
        size: str,
        resolution: str,
        model_id: str,
    ) -> dict[str, Any]:
        if not reference_images:
            raise ImageProviderError(
                f"{self.name} images_edits 需要至少一张参考图。",
                provider=self.name,
                model=model_id,
                error_code="missing_reference_image",
                fallback_allowed=False,
            )

        request_size = self._request_size(size, resolution)
        data = {
            "model": model_id,
            "prompt": prompt,
            "n": "1",
            "size": request_size,
            "quality": self.quality,
            "output_format": self.output_format,
        }
        stream_enabled = self.stream
        if stream_enabled:
            data["stream"] = "true"
            data["partial_images"] = str(self.partial_images)
        request_timeout = _env_int("POSTER_IMAGE_REQUEST_TIMEOUT_SECONDS", 120)
        logger.info(
            "提交 AIHubMix edits 生图请求：model=%s size=%s reference_images=%d stream=%s partial_images=%s timeout=%ss",
            model_id,
            request_size,
            len(reference_images),
            stream_enabled,
            data.get("partial_images", ""),
            request_timeout,
        )

        handles = []
        files = []
        try:
            for path in reference_images:
                handle = path.open("rb")
                handles.append(handle)
                content_type = mimetypes.guess_type(path.name)[0] or "image/png"
                files.append(("image[]", (path.name, handle, content_type)))

            headers = {"Authorization": f"Bearer {self.api_key}"}
            started = time.perf_counter()
            response: requests.Response | None = None
            try:
                response = self.session.post(
                    f"{self.base_url}/images/edits",
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=request_timeout,
                    stream=stream_enabled,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
            except requests.exceptions.RequestException as exc:
                raise _network_error(self.name, model_id, exc, started) from exc
        finally:
            for handle in handles:
                handle.close()

        if response.status_code >= 400:
            raise _http_error(self.name, model_id, response, started)

        request_id = _response_request_id(response)
        if stream_enabled and "text/event-stream" in response.headers.get("content-type", "").lower():
            stream_result = self._handle_stream_response(
                response=response,
                output_path=output_path,
                model_id=model_id,
                request_id=request_id,
                started=started,
            )
            stream_result["reference_image_count"] = len(reference_images)
            stream_result["request"] = {
                "endpoint": "images_edits",
                "size": request_size,
                "model": model_id,
                "stream": True,
                "partial_images": self.partial_images,
            }
            return stream_result

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                f"{self.name} 返回非 JSON 响应：{exc}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="invalid_json",
                fallback_allowed=False,
            ) from exc

        if response_payload.get("code", 200) != 200:
            code, message = _extract_error(response_payload)
            raise ImageProviderError(
                f"{self.name} 返回错误：{message or code or response_payload}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code=code or "provider_error",
                fallback_allowed=_is_transient_code(code),
            )

        sync_result = self._handle_sync_response(
            payload=response_payload,
            output_path=output_path,
            model_id=model_id,
            request_id=request_id,
            started=started,
        )
        if sync_result is not None:
            sync_result["reference_image_count"] = len(reference_images)
            sync_result["request"] = {"endpoint": "images_edits", "size": request_size, "model": model_id}
            return sync_result

        task_id = _extract_task_id(response_payload)
        if task_id:
            result = self._poll_task(
                task_id=task_id,
                headers={"Authorization": f"Bearer {self.api_key}"},
                output_path=output_path,
                model_id=model_id,
                request_id=request_id,
                started=started,
                request_timeout=request_timeout,
            )
            result["reference_image_count"] = len(reference_images)
            result["request"] = {"endpoint": "images_edits", "size": request_size, "model": model_id}
            return result

        raise ImageProviderError(
            f"{self.name} 未返回图片或 task_id。",
            provider=self.name,
            model=model_id,
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            error_code="missing_image",
            fallback_allowed=False,
        )

    def generate(
        self,
        *,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
        size: str,
        resolution: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        model_id = model or self.model
        if not self.api_key:
            raise ImageProviderError(
                "缺少 AIHubMix API Key：请配置 AIHUBMIX_API_KEY 或 config/fog_config.yaml 的 lx_haibao.image_api.aihubmix.api_key。",
                provider=self.name,
                model=model_id,
                error_code="missing_api_key",
                fallback_allowed=True,
            )

        image_urls = [image_to_data_url(path) for path in reference_images if path.is_file()]
        endpoint = self.endpoint.strip().lower()
        if endpoint == "images_edits" or (endpoint == "auto" and image_urls):
            return self._generate_with_edits(
                prompt=prompt,
                reference_images=[path for path in reference_images if path.is_file()],
                output_path=output_path,
                size=size,
                resolution=resolution,
                model_id=model_id,
            )
        if endpoint == "predictions":
            url = f"{self.base_url}/models/openai/{model_id}/predictions"
            payload = self._predictions_payload(prompt, image_urls, size, resolution)
        else:
            url = f"{self.base_url}/images/generations"
            payload = self._images_payload(prompt, image_urls, size, resolution, model_id)

        request_timeout = _env_int("POSTER_IMAGE_REQUEST_TIMEOUT_SECONDS", 120)
        logger.info(
            "提交 AIHubMix 生图请求：endpoint=%s model=%s size=%s reference_images=%d timeout=%ss",
            endpoint,
            model_id,
            payload.get("size") or payload.get("input", {}).get("size"),
            len(image_urls),
            request_timeout,
        )

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        response: requests.Response | None = None
        try:
            response = self.session.post(url, headers=headers, json=payload, timeout=request_timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
            raise _network_error(self.name, model_id, exc, started) from exc
        except requests.exceptions.RequestException as exc:
            raise _network_error(self.name, model_id, exc, started) from exc

        if response.status_code >= 400:
            raise _http_error(self.name, model_id, response, started)

        request_id = _response_request_id(response)
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                f"{self.name} 返回非 JSON 响应：{exc}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="invalid_json",
                fallback_allowed=False,
            ) from exc

        if response_payload.get("code", 200) != 200:
            code, message = _extract_error(response_payload)
            raise ImageProviderError(
                f"{self.name} 返回错误：{message or code or response_payload}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code=code or "provider_error",
                fallback_allowed=_is_transient_code(code),
            )

        sync_result = self._handle_sync_response(
            payload=response_payload,
            output_path=output_path,
            model_id=model_id,
            request_id=request_id,
            started=started,
        )
        if sync_result is not None:
            sync_result["reference_image_count"] = len(image_urls)
            sync_result["request"] = {"endpoint": endpoint, "size": payload.get("size") or payload.get("input", {}).get("size"), "model": model_id}
            return sync_result

        task_id = _extract_task_id(response_payload)
        if task_id:
            result = self._poll_task(
                task_id=task_id,
                headers=headers,
                output_path=output_path,
                model_id=model_id,
                request_id=request_id,
                started=started,
                request_timeout=request_timeout,
            )
            result["reference_image_count"] = len(image_urls)
            result["request"] = {"endpoint": endpoint, "size": payload.get("size") or payload.get("input", {}).get("size"), "model": model_id}
            return result

        raise ImageProviderError(
            f"{self.name} 未返回图片或 task_id。",
            provider=self.name,
            model=model_id,
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            error_code="missing_image",
            fallback_allowed=False,
        )

    def generate_text_image(
        self,
        *,
        prompt: str,
        output_path: Path,
        size: str,
        quality: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        model_id = model or self.model
        if not self.api_key:
            raise ImageProviderError(
                "缺少 AIHubMix API Key：请配置 AIHUBMIX_API_KEY 或 config/fog_config.yaml 的 lx_haibao.image_api.aihubmix.api_key。",
                provider=self.name,
                model=model_id,
                error_code="missing_api_key",
                fallback_allowed=False,
            )

        payload: dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "output_format": self.output_format,
        }
        request_timeout = _env_int("POSTER_IMAGE_SMOKE_TIMEOUT_SECONDS", 300)
        logger.info(
            "提交 AIHubMix 最小生图检查：endpoint=images_generations model=%s size=%s quality=%s timeout=%ss",
            model_id,
            size,
            quality,
            request_timeout,
        )

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            response = self.session.post(
                f"{self.base_url}/images/generations",
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RetryError) as exc:
            raise _network_error(self.name, model_id, exc, started) from exc
        except requests.exceptions.RequestException as exc:
            raise _network_error(self.name, model_id, exc, started) from exc

        if response.status_code >= 400:
            raise _http_error(self.name, model_id, response, started)

        request_id = _response_request_id(response)
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                f"{self.name} 返回非 JSON 响应：{exc}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code="invalid_json",
                fallback_allowed=False,
            ) from exc

        if response_payload.get("code", 200) != 200:
            code, message = _extract_error(response_payload)
            raise ImageProviderError(
                f"{self.name} 返回错误：{message or code or response_payload}",
                provider=self.name,
                model=model_id,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error_code=code or "provider_error",
                fallback_allowed=False,
            )

        sync_result = self._handle_sync_response(
            payload=response_payload,
            output_path=output_path,
            model_id=model_id,
            request_id=request_id,
            started=started,
        )
        if sync_result is not None:
            sync_result["reference_image_count"] = 0
            sync_result["request"] = {
                "endpoint": "images_generations",
                "size": size,
                "quality": quality,
                "model": model_id,
                "smoke": True,
            }
            return sync_result

        task_id = _extract_task_id(response_payload)
        if task_id:
            result = self._poll_task(
                task_id=task_id,
                headers=headers,
                output_path=output_path,
                model_id=model_id,
                request_id=request_id,
                started=started,
                request_timeout=request_timeout,
            )
            result["reference_image_count"] = 0
            result["request"] = {
                "endpoint": "images_generations",
                "size": size,
                "quality": quality,
                "model": model_id,
                "smoke": True,
            }
            return result

        raise ImageProviderError(
            f"{self.name} 未返回图片或 task_id。",
            provider=self.name,
            model=model_id,
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            error_code="missing_image",
            fallback_allowed=False,
        )


class BuiltinImageGenProvider(ImageProvider):
    name = BUILTIN_IMAGE_GEN_PROVIDER

    @property
    def api_key(self) -> str:
        return "codex-image-gen"

    @property
    def base_url(self) -> str:
        return "codex://image_gen"

    @property
    def model(self) -> str:
        return "image_gen"

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_configured": True,
            "reachable": True,
            "http_status": None,
            "latency_ms": 0,
            "status": "agent_handoff",
            "error": "由 Codex agent 调用内置 image_gen；Python 脚本只写交接请求，不直接生图。",
        }

    @staticmethod
    def _request_path(output_path: Path) -> Path:
        return output_path.with_name(f"{output_path.name}.imagegen-request.json")

    def generate(
        self,
        *,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
        size: str,
        resolution: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        request_path = self._request_path(output_path)
        request_path.parent.mkdir(parents=True, exist_ok=True)
        reference_order = ["template_example", "brand_logo", "brand_qr"][: len(reference_images)]
        if len(reference_images) >= 3:
            agent_steps = [
                "按 reference_image_order 用 view_image 打开三张参考图，不能只把路径写进 prompt。",
                "调用内置 image_gen 生成海报，Logo 必须来自第二张真实 Logo 输入图，二维码必须来自第三张真实二维码输入图。",
                "将生成图片保存到 output_path 后，继续执行二维码验证；验证通过后再移动到 final_path。",
            ]
        elif len(reference_images) == 2:
            agent_steps = [
                "按 reference_image_order 用 view_image 打开模板图和真实 Logo 图，不能只把路径写进 prompt。",
                "调用内置 image_gen 生成海报，Logo 必须来自第二张真实 Logo 输入图；不要生成二维码或伪扫码图案，保留干净扫码卡片。",
                "将生成图片保存到 output_path 后，按调用方 asset_mode 贴入真实二维码并继续二维码验证。",
            ]
        elif reference_images:
            agent_steps = [
                "按 reference_image_order 用 view_image 打开参考图，不能只把路径写进 prompt。",
                "调用内置 image_gen 生成海报；当前请求没有真实 Logo/二维码参考图时，不得生成假二维码。",
                "将生成图片保存到 output_path 后，按调用方 asset_mode 继续后续处理。",
            ]
        else:
            agent_steps = [
                "当前请求没有参考图，按 prompt 生成草图。",
                "不得生成假二维码或伪扫码图案。",
                "将生成图片保存到 output_path 后，按调用方 asset_mode 继续后续处理。",
            ]
        request_payload = {
            "provider": self.name,
            "status": "agent_handoff",
            "message": "外部图片 API 已回退到 Codex 内置 image_gen。请由 Codex agent 按 reference_images 顺序打开真实图片输入后调用 image_gen。",
            "prompt": prompt,
            "reference_images": [str(path) for path in reference_images],
            "reference_image_order": reference_order,
            "output_path": str(output_path),
            "size": size,
            "resolution": resolution,
            "agent_steps": agent_steps,
        }
        request_path.write_text(json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "status": "agent_handoff",
            "filepath": str(output_path),
            "url": None,
            "task_id": None,
            "provider": self.name,
            "model": model or self.model,
            "request_id": "",
            "latency_ms": _elapsed_ms(started),
            "error_code": "requires_agent_image_gen",
            "reference_image_count": len(reference_images),
            "imagegen_request_path": str(request_path),
            "request": {
                "endpoint": "codex_image_gen",
                "size": size,
                "resolution": resolution,
                "model": self.model,
            },
        }


def _build_provider(name: str) -> ImageProvider:
    name = _normalize_provider_name(name)
    if name == "kie":
        return KIEProvider()
    if name == "aihubmix":
        return AIHubMixProvider()
    if name == "apimart":
        return APIMartProvider()
    if name == BUILTIN_IMAGE_GEN_PROVIDER:
        return BuiltinImageGenProvider()
    raise ImageProviderError(
        f"未知图片 provider：{name}",
        provider=name,
        error_code="unknown_provider",
        fallback_allowed=False,
    )


def _log_provider_event(
    *,
    provider: str,
    model: str,
    request_id: str = "",
    latency_ms: int | None = None,
    error_code: str = "",
    fallback_used: bool = False,
    level: int = logging.INFO,
) -> None:
    fields = {
        "provider": provider,
        "model": model,
        "request_id": request_id,
        "latency_ms": latency_ms,
        "error_code": error_code,
        "fallback_used": fallback_used,
    }
    logger.log(level, "image_provider_event %s", json.dumps(fields, ensure_ascii=False, sort_keys=True))


def _format_provider_errors(errors: list[ImageProviderError]) -> str:
    parts = []
    for error in errors:
        parts.append(
            f"{error.provider}(model={error.model or '-'}, code={error.error_code or '-'}, "
            f"request_id={error.request_id or '-'}): {error}"
        )
    return "图片生成失败：" + "；".join(parts)


def require_api_key() -> str:
    missing: list[str] = []
    for provider_name in _configured_provider_names():
        provider = _build_provider(provider_name)
        if provider.api_key:
            return provider.api_key
        missing.append(provider_name)
    raise RuntimeError(
        "缺少图片生成 API Key："
        "KIE 请配置 KIE_API_KEY 或 config/fog_config.yaml 的 lx_haibao.image_api.kie.api_key。"
    )


def generate_image(
    *,
    prompt: str,
    reference_images: list[Path],
    output_path: Path,
    size: str = "9:16",
    resolution: str = "2k",
    model: str | None = None,
    skip_providers: list[str] | None = None,
) -> dict[str, Any]:
    skipped = {name.strip().lower() for name in (skip_providers or []) if name.strip()}
    provider_names = [name for name in _configured_provider_names() if name not in skipped]
    if not provider_names:
        skipped_text = ", ".join(sorted(skipped)) or "-"
        raise RuntimeError(f"图片生成失败：没有可用 provider；已跳过 provider={skipped_text}")
    errors: list[ImageProviderError] = []
    fallback_used = False

    for index, provider_name in enumerate(provider_names):
        provider = _build_provider(provider_name)
        try:
            result = provider.generate(
                prompt=prompt,
                reference_images=reference_images,
                output_path=output_path,
                size=size,
                resolution=resolution,
                model=model,
            )
            result["fallback_used"] = fallback_used
            if fallback_used:
                result["fallback_from"] = [error.provider for error in errors]
            _log_provider_event(
                provider=str(result.get("provider") or provider.name),
                model=str(result.get("model") or provider.model),
                request_id=str(result.get("request_id") or ""),
                latency_ms=int(result["latency_ms"]) if result.get("latency_ms") is not None else None,
                error_code=str(result.get("error_code") or ""),
                fallback_used=fallback_used,
                level=logging.INFO,
            )
            return result
        except ImageProviderError as exc:
            errors.append(exc)
            _log_provider_event(
                provider=exc.provider,
                model=exc.model,
                request_id=exc.request_id,
                latency_ms=exc.latency_ms,
                error_code=exc.error_code,
                fallback_used=fallback_used,
                level=logging.WARNING,
            )
            has_next = index + 1 < len(provider_names)
            if exc.fallback_allowed and has_next:
                fallback_used = True
                logger.warning(
                    "图片 provider 失败，准备 fallback：provider=%s error_code=%s next_provider=%s",
                    exc.provider,
                    exc.error_code,
                    provider_names[index + 1],
                )
                continue
            raise RuntimeError(_format_provider_errors(errors)) from exc

    raise RuntimeError(_format_provider_errors(errors))


def smoke_test_provider(
    *,
    provider_name: str,
    output_path: Path,
    prompt: str,
    size: str = "1024x1024",
    quality: str = "low",
    model: str | None = None,
) -> dict[str, Any]:
    provider = _build_provider(provider_name.strip().lower())
    if isinstance(provider, BuiltinImageGenProvider):
        raise RuntimeError("builtin_image_gen 不能通过 Python 脚本 smoke；它需要 Codex agent 直接调用内置 image_gen。")
    try:
        if isinstance(provider, AIHubMixProvider):
            result = provider.generate_text_image(
                prompt=prompt,
                output_path=output_path,
                size=size,
                quality=quality,
                model=model,
            )
        else:
            result = provider.generate(
                prompt=prompt,
                reference_images=[],
                output_path=output_path,
                size=size,
                resolution="",
                model=model,
            )
        _log_provider_event(
            provider=str(result.get("provider") or provider.name),
            model=str(result.get("model") or provider.model),
            request_id=str(result.get("request_id") or ""),
            latency_ms=int(result["latency_ms"]) if result.get("latency_ms") is not None else None,
            error_code=str(result.get("error_code") or ""),
            fallback_used=False,
            level=logging.INFO,
        )
        return result
    except ImageProviderError as exc:
        _log_provider_event(
            provider=exc.provider,
            model=exc.model,
            request_id=exc.request_id,
            latency_ms=exc.latency_ms,
            error_code=exc.error_code,
            fallback_used=False,
            level=logging.WARNING,
        )
        raise RuntimeError(_format_provider_errors([exc])) from exc


def generateImage(prompt: str, options: dict[str, Any]) -> dict[str, Any]:
    return generate_image(
        prompt=prompt,
        reference_images=[Path(path) for path in options.get("reference_images", [])],
        output_path=Path(options["output_path"]),
        size=str(options.get("size", "9:16")),
        resolution=str(options.get("resolution", "2k")),
        model=options.get("model"),
        skip_providers=[str(item) for item in options.get("skip_providers", [])],
    )


def check_providers() -> dict[str, Any]:
    provider_order = _configured_provider_names()
    providers: list[dict[str, Any]] = []
    provider_names: list[str] = []
    for provider_name in provider_order:
        normalized = _normalize_provider_name(provider_name)
        if normalized not in provider_names:
            provider_names.append(normalized)
    for provider_name in provider_names:
        try:
            provider = _build_provider(provider_name)
            item = provider.health_check()
        except ImageProviderError as exc:
            item = {
                "provider": provider_name,
                "model": exc.model,
                "base_url": "",
                "api_key_configured": False,
                "reachable": False,
                "http_status": None,
                "latency_ms": exc.latency_ms,
                "status": "error",
                "error": str(exc),
            }
        item["enabled_order"] = provider_order.index(provider_name) + 1 if provider_name in provider_order else None
        providers.append(item)

    usable = [item for item in providers if item["enabled_order"] and item["api_key_configured"] and item["reachable"]]
    return {
        "ok": bool(usable),
        "primary": provider_order[0] if provider_order else None,
        "fallback": provider_order[1] if len(provider_order) > 1 else None,
        "providers": providers,
        "note": "该检查只做配置和 base_url 连通性检查，不调用图片生成接口，不产生生图费用。",
    }
