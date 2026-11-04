import json
import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from urllib.error import HTTPError
from email.message import Message
from unittest.mock import patch
import diagnostics as log
from core import parse_view, Model


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(log.close)
        self.root = Path(self.temp.name)
        log.setup(self.root / 'logs')

    def test_keys_payloads_and_chat_fields_are_not_recorded(self):
        log.add_secret('custom-test-secret')
        log.record('test', reason='custom-test-secret Bearer another-token sk-abcdefghijk',
                   payload='private request', message='private chat', image='private screenshot',
                   details=dict(key='custom-test-secret', input_text='private draft', x=150))
        text = log.tail()
        for secret in ('custom-test-secret', 'another-token', 'sk-abcdefghijk', 'private'):
            self.assertNotIn(secret, text)
        self.assertIn('REDACTED', text)

    def test_coordinate_failure_records_specific_reason_and_values_without_message_text(self):
        data = dict(conversation='当前', message='私密消息内容', context='私密上下文', incoming=False,
                    confidence=.99, input_empty=True, input_text='',
                    list_box=dict(left=50, top=100, right=300, bottom=950),
                    unread=[dict(conversation='王进喜', x=900, y=400, confidence=.99,
                                 badge_type='red_number', badge_text='2')])
        result = parse_view(data, (2000, 1600))
        self.assertIn('x=900', result['navigation_issues'][0][1])
        rows = [json.loads(line) for line in log.tail().splitlines()]
        error = next(row for row in rows if row['event'] == 'row_invalid')
        self.assertEqual(error['conversation'], '王进喜')
        self.assertEqual(error['details']['x'], 900)
        self.assertEqual(error['list_box']['right'], 300)
        self.assertNotIn('私密', log.tail())

    def test_export_preserves_all_rows_and_scrubs_secrets_registered_later(self):
        for i in range(60):
            log.record('test', reason='late-secret-value', checked=i)
        log.add_secret('late-secret-value')
        target = self.root / 'bundle.zip'
        log.export_bundle(target)
        with zipfile.ZipFile(target) as bundle:
            text = bundle.read('diagnostics.jsonl').decode()
            self.assertGreater(len(text), 1500)
            self.assertNotIn('late-secret-value', text)
            self.assertEqual(len(text.splitlines()), 61)
            self.assertIn('system.json', bundle.namelist())

    def test_rotation_limits_retained_files(self):
        log._handler.maxBytes = 300
        for i in range(50):
            log.record('test', reason='x' * 100, checked=i)
        self.assertLessEqual(len(list((self.root / 'logs').iterdir())), 4)

    def test_error_records_stack_locations_but_not_local_variables(self):
        secret_local = 'unrecorded-local-value'
        try:
            raise RuntimeError('业务错误')
        except RuntimeError as error:
            log.record_exception('worker', error)
        text = log.tail()
        self.assertIn('test_diagnostics.py', text)
        self.assertIn('业务错误', text)
        self.assertNotIn(secret_local, text)

    def test_export_cannot_overwrite_active_log(self):
        with self.assertRaises(OSError):
            log.export_bundle(self.root / 'logs' / 'diagnostics.jsonl')
        self.assertIn('app_start', log.tail())

    def test_api_failure_records_timing_and_code_without_server_body(self):
        body = io.BytesIO(json.dumps({'error': {'code': 'rate_limit_exceeded',
                                               'message': 'private-server-response'}}).encode())
        error = HTTPError('https://example.test/v1', 429, 'limited', Message(), body)
        with patch('core.open_request', side_effect=error):
            with self.assertRaises(RuntimeError):
                Model('custom-api-secret').call('private request', [{'type': 'input_text', 'text': 'private chat'}])
        text = log.tail()
        self.assertIn('rate_limit_exceeded', text)
        self.assertIn('duration_ms', text)
        self.assertIn('429', text)
        for secret in ('private-server-response', 'private request', 'private chat', 'custom-api-secret'):
            self.assertNotIn(secret, text)
