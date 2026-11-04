from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from email.message import Message

from api_errors import APIError
from core import Model
from model_catalog import (BUILTIN, BUILTIN_BY_ID, cache_path, catalog_endpoint,
                           fetch_catalog, filter_catalog, load_catalog,
                           optional_thinking, parse_model, save_cache)
from settings import default_profile


def page(models, total):
    return io.BytesIO(json.dumps({'success': True, 'output': {'models': models, 'total': total}}).encode())


def item(name, inputs=('Text',), outputs=('Text',), capabilities=('TG',)):
    return {'model': name, 'inference_metadata': {'request_modality': list(inputs),
            'response_modality': list(outputs)}, 'capabilities': list(capabilities)}


class CatalogTests(unittest.TestCase):
    def test_offline_snapshot_covers_all_document_categories_and_families(self):
        self.assertGreaterEqual(len(BUILTIN), 500)
        self.assertEqual(len(BUILTIN), len(BUILTIN_BY_ID))
        for model in ('qwen3.8-max', 'qwen3.8-flash', 'qwen3.7-plus', 'qwen3-vl-flash',
                      'deepseek-v4.1-flash', 'kimi-k3', 'glm-5.3', 'MiniMax/MiniMax-M3.1-Flash',
                      'qwen-image-3.0-pro', 'wan3.0-video', 'qwen-audio-3.1-tts-plus',
                      'qwen3-vl-embedding', 'Tripo/Tripo-H3.1', 'decision-model-preview'):
            with self.subTest(model=model):
                self.assertIn(model, BUILTIN_BY_ID)
        self.assertNotIn('text-embedding-3-large', BUILTIN_BY_ID)  # Migration comparison, not an Alibaba model.

    def test_search_and_role_filters_exclude_incompatible_models(self):
        matches = filter_catalog(BUILTIN, 'qwen3.8 max', role='vision')
        self.assertTrue(matches)
        self.assertTrue(all('vision' in row.roles for row in matches))
        for model in ('qwen-image-3.0-pro', 'wan3.0-video', 'qwen3-vl-embedding',
                      'qwen3.5-ocr', 'qwen-voice-enrollment'):
            self.assertFalse(BUILTIN_BY_ID[model].roles)
        self.assertFalse(filter_catalog(BUILTIN, 'qwen-image', role='reply'))

    def test_known_incompatible_model_cannot_be_saved_for_chat_tasks(self):
        for field, model in [('vision_model', 'deepseek-v4.1-flash'),
                             ('reply_model', 'qwen-image-3.0-pro')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(default_profile('aliyun-beijing'), **{field: model}).validated(require_key=False)

    def test_fixed_thinking_models_do_not_receive_disable_thinking(self):
        self.assertTrue(optional_thinking('qwen3.8-max'))
        self.assertTrue(optional_thinking('qwen3-vl-flash'))
        model = 'qwen3-vl-32b-thinking'
        self.assertFalse(optional_thinking(model))
        self.assertEqual(BUILTIN_BY_ID[model].json_mode, 'prompt')
        with patch('core.open_request', return_value=io.BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}')) as transport:
            Model('test', model, protocol='chat', provider='aliyun-beijing').call('hello', [{'type':'input_text','text':'test'}])
        self.assertNotIn('enable_thinking', json.loads(transport.call_args.args[0].data))


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.base = 'https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
        self.key = 'test-key-private'
        self.cancel = threading.Event()

    def test_native_endpoint_keeps_origin_and_paginates_until_total(self):
        transport = Mock(side_effect=[page([item('new-text'), item('new-vision', ('Text','Image'), capabilities=('VU',))], 3),
                                      page([item('new-image', outputs=('Image',), capabilities=('IG',))], 3)])
        rows = fetch_catalog(self.base, self.key, self.cancel, transport)
        self.assertEqual({row.id for row in rows}, {'new-text','new-vision','new-image'})
        by_id = {row.id:row for row in rows}
        self.assertIn('vision', by_id['new-vision'].roles)
        self.assertFalse(by_id['new-image'].roles)
        self.assertEqual(transport.call_count, 2)
        for number, call in enumerate(transport.call_args_list, 1):
            request = call.args[0]
            url = urlsplit(request.full_url)
            self.assertEqual(url.netloc, 'workspace.cn-beijing.maas.aliyuncs.com')
            self.assertEqual(url.path, '/api/v1/models')
            self.assertEqual(parse_qs(url.query)['page_no'], [str(number)])
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.key)
            self.assertNotIn(self.key, request.full_url)
            self.assertEqual(call.kwargs['timeout'], 30)

    def test_repeated_incomplete_and_changing_pages_are_rejected(self):
        sequences = [
            [page([item('one-model')], 2), page([item('one-model')], 2)],
            [page([item('one-model')], 2), page([], 2)],
            [page([item('one-model')], 2), page([item('two-model')], 3)],
            [page([item('one-model'), item('two-model')], 1)],
        ]
        for sequence in sequences:
            with self.subTest(), self.assertRaises(ValueError):
                fetch_catalog(self.base, self.key, self.cancel, Mock(side_effect=sequence))

    def test_cancellation_never_publishes_partial_results(self):
        transport = Mock()
        self.cancel.set()
        with self.assertRaises(RuntimeError):
            fetch_catalog(self.base, self.key, self.cancel, transport)
        transport.assert_not_called()
        self.cancel.clear()
        def request(*args, **kwargs):
            self.cancel.set()
            return page([item('one-model')], 2)
        with self.assertRaises(RuntimeError):
            fetch_catalog(self.base, self.key, self.cancel, request)

    def test_http_errors_are_sanitized(self):
        error = HTTPError(self.base, 401, 'unauthorized', Message(),
                          io.BytesIO(json.dumps({'error':{'message':self.key}}).encode()))
        with self.assertRaises(APIError) as raised:
            fetch_catalog(self.base, self.key, self.cancel, Mock(side_effect=error))
        self.assertNotIn(self.key, str(raised.exception))
        self.assertIn('百炼', str(raised.exception))

    def test_cache_is_complete_nonsecret_and_invalid_cache_falls_back(self):
        secret_item = dict(item('new-vision', ('Text','Image'), capabilities=('VU',)),
                           description=self.key, api_key=self.key, features=['structured-outputs'])
        rows = fetch_catalog(self.base, self.key, self.cancel, Mock(return_value=page([secret_item],1)))
        with tempfile.TemporaryDirectory() as folder:
            path = cache_path(Path(folder)/'settings.json', self.base)
            save_cache(path, rows)
            self.assertNotIn(self.key, path.read_text())
            self.assertEqual(load_catalog(path), rows)
            path.write_text('{invalid')
            self.assertEqual(load_catalog(path), BUILTIN)

    def test_malformed_or_echoed_credential_model_ids_are_not_cached(self):
        for model in ('bad\nmodel', self.key):
            with self.subTest(model=model), self.assertRaises(ValueError):
                fetch_catalog(self.base, self.key, self.cancel, Mock(return_value=page([item(model)],1)))

    def test_sync_endpoint_rejects_wrong_paths_and_query_credentials(self):
        for base in ('https://example.org/v1', self.base+'?api_key=secret'):
            with self.assertRaises(ValueError):
                catalog_endpoint(base)


if __name__ == '__main__':
    unittest.main()
