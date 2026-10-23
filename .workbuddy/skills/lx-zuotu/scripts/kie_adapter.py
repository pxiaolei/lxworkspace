"""KIE-only reuse of lx-haibao's upload and polling implementation."""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
import time
from pathlib import Path


class KIEAdapterError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool, task_id: str | None = None, model: str | None = None):
        super().__init__(message)
        self.uncertain = uncertain
        self.task_id = task_id
        self.model = model


class HiddenProviderLog(logging.Filter):
    def filter(self, record):
        # The reused module has its own name/logger, so this never changes
        # lx-haibao's normal logger or another provider's logging behavior.
        record.msg = 'KIE internal operation; raw response and URL details hidden'
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


def load_shared_client():
    name = '_lx_zuotu_shared_kie'
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[2] / 'lx-haibao/scripts/image2_client.py'
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ValueError('缺少 lx-haibao KIE 客户端')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module.logger.addFilter(HiddenProviderLog())
    return sys.modules[name]


def config_status() -> dict:
    provider = load_shared_client().KIEProvider()
    return {'provider': 'kie', 'api_key_configured': bool(provider.api_key),
            'models_configured': bool(provider.text_model and provider.image_model),
            'model_compatible': provider.image_model == 'gpt-image-2-image-to-image' and provider.text_model == 'gpt-image-2-text-to-image'}


class RequestSession:
    """Supply documented new-image fields without changing the old client."""
    def __init__(self, session, owner, target, on_submitted):
        self.session, self.owner = session, owner
        self.target, self.on_submitted = target, on_submitted
        self.task_id = None
        self.terminal_failure = False

    def post(self, url, **kwargs):
        creating = url.endswith('/jobs/createTask')
        if creating:
            body = kwargs['json']
            body.pop('callBackUrl', None)
            body['input']['aspect_ratio'] = self.target['generation_ratio']
            body['input']['resolution'] = self.target['resolution']
            if self.target['resolution'] == '1K':
                body['input']['background'] = 'transparent' if self.target.get('transparent') else 'opaque'
            self.owner.submissions += 1
            self.on_submitted(None)
        response = self.session.post(url, **kwargs)
        if creating:
            try:
                task = response.json().get('data', {}).get('taskId')
            except (ValueError, AttributeError):
                task = None
            if isinstance(task, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', task):
                self.task_id = task
                self.on_submitted(task)
        return response

    def get(self, url, **kwargs):
        response = self.session.get(url, **kwargs)
        if url.endswith('/jobs/recordInfo'):
            try:
                self.terminal_failure = response.json().get('data', {}).get('state') == 'fail'
            except (ValueError, AttributeError):
                pass
        return response


class KIEAdapter:
    name = 'kie'

    def __init__(self):
        self.submissions = 0

    def _provider(self, target, on_submitted, *, use_text=False, model=None):
        provider = load_shared_client().KIEProvider()
        if not provider.api_key:
            raise ValueError('缺少 KIE 配置；只检查存在性，不输出密钥')
        selected = model if model is not None else provider.text_model if use_text else provider.image_model
        expected = 'gpt-image-2-text-to-image' if use_text else 'gpt-image-2-image-to-image'
        if selected != expected:
            raise ValueError('现有 KIE 模型与已核对协议不兼容；停止，不修改配置或切换模型')
        wrapper = RequestSession(provider.session, self, target, on_submitted)
        provider.session = wrapper
        return provider, wrapper, selected

    def generate(self, *, prompt: str, references: list[Path], output_path: Path, target: dict, on_submitted) -> dict:
        if any(not path.is_file() for path in references):
            raise ValueError('明确提供的参考图缺失，不能退回无参考图生成')
        provider, wrapper, model = self._provider(target, on_submitted, use_text=not references)
        try:
            result = provider.generate(prompt=prompt, reference_images=references, output_path=output_path,
                                       size=target['generation_ratio'], resolution=target['resolution'], model=model)
        except Exception:
            # Upstream exceptions may contain raw responses or signed URLs.
            raise KIEAdapterError('KIE 生成未完成；响应详情已隐藏。状态不明时禁止重复提交。',
                                  uncertain=not wrapper.terminal_failure, task_id=wrapper.task_id, model=model) from None
        return {key: result[key] for key in ('provider', 'model', 'task_id', 'latency_ms') if key in result}

    def resume(self, *, task_id: str, output_path: Path, target: dict, model: str | None = None) -> dict:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', task_id):
            raise ValueError('无效 task ID')
        provider, wrapper, selected = self._provider(target, lambda task: None,
                                                    use_text=model == 'gpt-image-2-text-to-image', model=model)
        wrapper.task_id = task_id
        try:
            result = provider._poll_task(task_id=task_id, headers={'Authorization': f'Bearer {provider.api_key}'},
                                        output_path=output_path, model_id=selected,
                                        request_id='', started=time.perf_counter(), request_timeout=120)
        except Exception:
            raise KIEAdapterError('KIE 旧任务尚未得到可交付结果；不提交新任务。',
                                  uncertain=not wrapper.terminal_failure, task_id=task_id, model=selected) from None
        return {key: result[key] for key in ('provider', 'model', 'task_id', 'latency_ms') if key in result}
