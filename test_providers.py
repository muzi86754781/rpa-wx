import io
import json
import unittest
from urllib.error import HTTPError
from email.message import Message
from unittest.mock import patch

from PIL import Image

from api_errors import APIError, api_error
from core import Model, NoRedirect, SCHEMA, chat_text


def chat_response(text='你好', finish='stop', **extra):
    return io.BytesIO(json.dumps({'choices': [{'finish_reason': finish,
                         'message': dict(content=text, **extra)}]}).encode())


class ProviderTests(unittest.TestCase):
    def aliyun(self):
        return Model('aliyun-test-key', 'qwen3-vl-flash', reply_model='qwen-plus',
                     provider='aliyun-beijing', protocol='chat', json_mode='object',
                     base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')

    def test_aliyun_vision_uses_chat_image_parts_and_validates_json(self):
        view = dict(conversation='甲', message='你好', incoming=True, confidence=.99,
                    context='', input_empty=True, input_text='', list_box=None, unread=[])
        with patch('core.open_request', return_value=chat_response(json.dumps(view))) as transport:
            self.assertEqual(self.aliyun().inspect(Image.new('RGB', (40, 30))), view)
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer aliyun-test-key')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'qwen3-vl-flash')
        self.assertFalse(payload['enable_thinking'])
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertIn('JSON Schema', payload['messages'][0]['content'])
        image = payload['messages'][1]['content'][1]
        self.assertEqual(image['type'], 'image_url')
        self.assertTrue(image['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn('store', payload)
        self.assertNotIn('input', payload)

    def test_reply_uses_separate_text_model_without_vision_format(self):
        with patch('core.open_request', return_value=chat_response('好的')) as transport:
            self.assertEqual(self.aliyun().reply({'conversation': '甲', 'message': '你好'}, '简短'), '好的')
        payload = json.loads(transport.call_args.args[0].data)
        self.assertEqual(payload['model'], 'qwen-plus')
        self.assertNotIn('response_format', payload)
        self.assertEqual(payload['messages'][1]['content'][0]['type'], 'text')

    def test_openai_responses_preserves_existing_wire_format(self):
        response = {'output': [{'content': [{'type': 'output_text', 'text': 'ok'}]}]}
        with patch('core.open_request', return_value=io.BytesIO(json.dumps(response).encode())) as transport:
            self.assertEqual(Model('test-key').call('instructions', [{'type': 'input_text', 'text': 'read'}], SCHEMA), 'ok')
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        payload = json.loads(request.data)
        self.assertFalse(payload['store'])
        self.assertEqual(payload['text']['format']['type'], 'json_schema')

    def test_custom_chat_json_modes_and_endpoint_are_respected(self):
        for mode in ('schema', 'object', 'prompt'):
            with self.subTest(mode=mode):
                client = Model('custom-key', 'vision-model', provider='custom', protocol='chat',
                               base_url='https://models.example.test/v1', json_mode=mode)
                with patch('core.open_request', return_value=chat_response('{}')) as transport:
                    client.call('read', [{'type': 'input_text', 'text': 'test'}], SCHEMA)
                request = transport.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(request.full_url, 'https://models.example.test/v1/chat/completions')
                self.assertNotIn('enable_thinking', payload)
                if mode == 'prompt':
                    self.assertNotIn('response_format', payload)
                else:
                    self.assertEqual(payload['response_format']['type'], 'json_schema' if mode == 'schema' else 'json_object')

    def test_truncated_refused_and_reasoning_only_results_are_rejected(self):
        for body in (chat_response('partial', finish='length'), chat_response('unsafe', refusal='refused'),
                     chat_response(None, reasoning_content='do not expose'), io.BytesIO(b'{"choices":[]}')):
            with self.subTest(), self.assertRaises(ValueError):
                chat_text(json.load(body))

    def test_untrusted_or_invalid_vision_json_is_not_used(self):
        for text in ('not json', '{}', '{"incoming":"false"}'):
            with self.subTest(text=text), patch('core.open_request', return_value=chat_response(text)):
                with self.assertRaises(ValueError):
                    self.aliyun().inspect(Image.new('RGB', (40, 30)))

    def test_free_tier_stop_is_not_retried_and_names_the_actual_provider(self):
        body = io.BytesIO(json.dumps({'error': {'code': 'AllocationQuota.FreeTierOnly',
                                               'message': 'secret-do-not-print'}}).encode())
        failure = HTTPError('https://dashscope.aliyuncs.com', 403, 'forbidden', Message(), body)
        with patch('core.open_request', side_effect=failure):
            with self.assertRaises(APIError) as raised:
                self.aliyun().call('read', [{'type': 'input_text', 'text': 'test'}])
        self.assertFalse(raised.exception.retryable)
        self.assertIn('阿里云百炼', str(raised.exception))
        self.assertIn('免费额度', str(raised.exception))
        self.assertNotIn('OpenAI', str(raised.exception))
        self.assertNotIn('secret', str(raised.exception))

    def test_aliyun_rate_limits_are_transient_but_allocation_limits_are_not(self):
        for code, retryable in [('Throttling.RateQuota', True), ('Throttling.BurstRate', True),
                                ('Throttling.AllocationQuota', False)]:
            with self.subTest(code=code):
                failure = HTTPError('https://example.org', 429, 'limited', Message(),
                                    io.BytesIO(json.dumps({'code': code}).encode()))
                self.assertEqual(api_error(failure, provider='阿里云百炼').retryable, retryable)

    def test_redirect_does_not_forward_api_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 307, 'redirect', {}, 'https://other.example/v1'))


if __name__ == '__main__':
    unittest.main()
