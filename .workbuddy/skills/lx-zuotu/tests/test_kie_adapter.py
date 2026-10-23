import tempfile
import unittest
import io
import logging
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from _support import FIXTURE, require


class KIEAdapterTests(unittest.TestCase):
    def setUp(self):
        self.api = require(self, 'kie_adapter')
        self.target = require(self, 'input_plan').build_plan(FIXTURE, types='popup')['targets'][0]

    def test_wire_payload_has_resolution_transparency_no_callback_and_no_fallback(self):
        client = self.api.load_shared_client()
        captured = []
        class Response:
            status_code = 200
            headers = {}
            def json(self): return {'code': 200, 'data': {'taskId': 'offline-task'}}
        class Session:
            def post(self, url, **kwargs): captured.append(kwargs['json']); return Response()
        def polled(provider, **kwargs):
            Image.new('RGBA', (534, 712), (0, 0, 0, 0)).save(kwargs['output_path'])
            return {'provider': 'kie', 'task_id': kwargs['task_id'], 'url': 'https://private.invalid/?secret=do-not-keep'}
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp)/'ref.png'; Image.new('RGB', (10,10)).save(ref)
            with patch.object(client, '_provider_value', side_effect=lambda provider,key,names,default='',**kw: {'api_key':'offline-placeholder','callback_url':'https://callback.invalid','aspect_ratio':'9:16'}.get(key,default)), patch.object(client, '_build_session', return_value=Session()), patch.object(client.KIEProvider, '_upload_reference_images', return_value=['https://reference.invalid']), patch.object(client.KIEProvider, '_poll_task', polled):
                adapter = self.api.KIEAdapter()
                result = adapter.generate(prompt='data', references=[ref], output_path=Path(tmp)/'out.png', target=self.target, on_submitted=lambda task: None)
        body = captured[0]
        self.assertEqual(body['model'], 'gpt-image-2-image-to-image')
        self.assertEqual(body['input']['aspect_ratio'], '3:4')
        self.assertEqual(body['input']['resolution'], '1K')
        self.assertEqual(body['input']['background'], 'transparent')
        self.assertNotIn('callBackUrl', body)
        self.assertNotIn('url', result)
        self.assertEqual(adapter.submissions, 1)

    def test_errors_do_not_leak_response_or_secret_and_are_not_resubmitted(self):
        client = self.api.load_shared_client()
        with tempfile.TemporaryDirectory() as tmp:
            ref=Path(tmp)/'ref.png'; Image.new('RGB',(10,10)).save(ref)
            with patch.object(client, '_provider_value', side_effect=lambda provider,key,names,default='',**kw: {'api_key':'offline-placeholder'}.get(key,default)), patch.object(client.KIEProvider, 'generate', side_effect=RuntimeError('Authorization: private-secret')):
                adapter=self.api.KIEAdapter()
                with self.assertRaises(self.api.KIEAdapterError) as context:
                    adapter.generate(prompt='x', references=[ref], output_path=Path(tmp)/'out.png', target=self.target, on_submitted=lambda task: None)
                self.assertNotIn('private-secret', str(context.exception))
                self.assertTrue(context.exception.uncertain)

    def test_missing_reference_fails_before_network(self):
        adapter=self.api.KIEAdapter()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
            with self.assertRaises(ValueError):
                adapter.generate(prompt='x', references=[Path('/missing-reference')], output_path=Path('/unused'), target=self.target, on_submitted=lambda task: None)

    def test_no_reference_uses_text_model_payload_without_uploads(self):
        client=self.api.load_shared_client();captured=[]
        class Response:
            status_code=200
            headers={}
            def json(self):return {'code':200,'data':{'taskId':'text-task'}}
        class Session:
            def post(self,url,**kwargs):captured.append(kwargs['json']);return Response()
        def polled(provider,**kwargs):
            return {'provider':'kie','model':kwargs['model_id'],'task_id':kwargs['task_id']}
        with tempfile.TemporaryDirectory() as tmp,patch.object(client,'_provider_value',side_effect=lambda provider,key,names,default='',**kw:{'api_key':'offline-placeholder'}.get(key,default)),patch.object(client,'_build_session',return_value=Session()),patch.object(client.KIEProvider,'_upload_reference_images',side_effect=AssertionError('no upload expected')),patch.object(client.KIEProvider,'_poll_task',polled):
            result=self.api.KIEAdapter().generate(prompt='免佣活动',references=[],output_path=Path(tmp)/'out.png',target=self.target,on_submitted=lambda task:None)
        self.assertEqual(captured[0]['model'],'gpt-image-2-text-to-image')
        self.assertNotIn('input_urls',captured[0]['input'])
        self.assertNotIn('callBackUrl',captured[0])
        self.assertEqual(captured[0]['input']['background'],'transparent')
        self.assertEqual(captured[0]['input']['resolution'],'1K')
        self.assertEqual(result['model'],'gpt-image-2-text-to-image')

    def test_unknown_text_model_is_rejected_before_network(self):
        client=self.api.load_shared_client()
        with patch.object(client,'_provider_value',side_effect=lambda provider,key,names,default='',**kw:{'api_key':'offline-placeholder','text_model':'unsupported-model'}.get(key,default)),patch('requests.sessions.Session.request',side_effect=AssertionError('network forbidden')):
            with self.assertRaisesRegex(ValueError,'模型'):
                self.api.KIEAdapter().generate(prompt='x',references=[],output_path=Path('/unused'),target=self.target,on_submitted=lambda task:None)

    def test_text_task_resume_uses_original_model_without_new_submission(self):
        client=self.api.load_shared_client()
        captured=[]
        def polled(provider,**kwargs):captured.append(kwargs);return {'provider':'kie','task_id':kwargs['task_id'],'model':kwargs['model_id']}
        with patch.object(client,'_provider_value',side_effect=lambda provider,key,names,default='',**kw:{'api_key':'offline-placeholder'}.get(key,default)),patch.object(client.KIEProvider,'_poll_task',polled),patch('requests.sessions.Session.request',side_effect=AssertionError('no new request')):
            adapter=self.api.KIEAdapter()
            result=adapter.resume(task_id='existing-text-task',output_path=Path('/unused'),target=self.target,model='gpt-image-2-text-to-image')
        self.assertEqual(result['model'],'gpt-image-2-text-to-image')
        self.assertEqual(captured[0]['task_id'],'existing-text-task')
        self.assertEqual(adapter.submissions,0)

    def test_unknown_model_is_rejected_before_any_network_call(self):
        client=self.api.load_shared_client()
        with patch.object(client,'_provider_value',side_effect=lambda provider,key,names,default='',**kw: {'api_key':'offline-placeholder','image_model':'unsupported-model'}.get(key,default)):
            with patch('requests.sessions.Session.request',side_effect=AssertionError('network forbidden')):
                self.assertFalse(self.api.config_status().get('model_compatible',True))

    def test_shared_download_warning_never_exposes_signed_url(self):
        client=self.api.load_shared_client(); capture=io.StringIO()
        handler=logging.StreamHandler(capture); client.logger.addHandler(handler)
        private='https://download.invalid/image.png?signature=FAKE-SIGNED-TOKEN'
        try:
            with tempfile.TemporaryDirectory() as tmp,patch.object(client,'_provider_value',side_effect=lambda provider,key,names,default='',**kw: {'api_key':'offline-placeholder'}.get(key,default)),patch.object(client,'_env_int',return_value=1),patch.object(client.time,'sleep'),patch.object(client.requests,'get',side_effect=RuntimeError(private)),patch.object(client.KIEProvider,'generate',side_effect=lambda **kwargs:client._download(private,kwargs['output_path'])):
                ref=Path(tmp)/'ref.png'; Image.new('RGB',(10,10)).save(ref)
                with self.assertRaises(self.api.KIEAdapterError):
                    self.api.KIEAdapter().generate(prompt='x',references=[ref],output_path=Path(tmp)/'out.png',target=self.target,on_submitted=lambda task:None)
            self.assertNotIn('FAKE-SIGNED-TOKEN',capture.getvalue())
            self.assertNotIn('signature=',capture.getvalue())
        finally:
            client.logger.removeHandler(handler)
