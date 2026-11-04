import io
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from api_errors import APIError, Cooldown, api_error, retry_delay, run_operation
from core import Model


def http_error(status=429, code='rate_limit_exceeded', kind=None, headers=None, body=None):
    if body is None:
        body = json.dumps({'error': {'code': code, 'type': kind,
                                    'message': 'private chat and secret-sk-DO-NOT-PRINT'}}).encode()
    values = Message()
    for name, value in (headers or {}).items():
        values[name] = value
    return HTTPError('https://api.openai.com/v1/responses', status,
                     'Too Many Requests', values, io.BytesIO(body))


class ErrorTests(unittest.TestCase):
    def test_billing_429_is_not_retried_even_with_retry_header(self):
        for code in ('insufficient_quota', 'credit_balance_exhausted',
                     'organization_spend_limit_exceeded', 'project_spend_limit_exceeded',
                     'organization_usage_limit_exceeded'):
            with self.subTest(code=code):
                error = api_error(http_error(code=code, headers={'Retry-After': '10'}))
                self.assertFalse(error.retryable)
                self.assertEqual(error.code, code)
                self.assertNotIn('private', str(error))
                self.assertNotIn('secret-sk', str(error))

    def test_quota_type_fallback_and_unknown_codes_do_not_leak(self):
        error = api_error(http_error(code='secret-sk-DO-NOT-PRINT', kind='insufficient_quota'))
        self.assertFalse(error.retryable)
        self.assertEqual(error.code, 'insufficient_quota')
        self.assertNotIn('secret-sk', str(error))

    def test_rate_limit_honors_retry_after(self):
        error = api_error(http_error(headers={'Retry-After': '65'}))
        self.assertTrue(error.retryable)
        self.assertEqual(error.retry_after, 65)
        self.assertIn('65', str(error))

    def test_unknown_429_is_not_assumed_to_be_transient(self):
        error = api_error(http_error(body=b'<html>private secret-sk</html>'))
        self.assertFalse(error.retryable)
        self.assertIn('Billing', str(error))
        self.assertNotIn('secret-sk', str(error))

    def test_retry_after_dates_and_invalid_numbers(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=300)
        delay = retry_delay({'Retry-After': format_datetime(future, usegmt=True)})
        self.assertGreater(delay, 298)
        self.assertLessEqual(delay, 300)
        for value in ('nan', 'inf', '-5', 'invalid'):
            self.assertEqual(retry_delay({'Retry-After': value}), 0)

    def test_model_translates_http_error_without_retrying_stale_payload(self):
        with patch('core.open_request', side_effect=http_error(code='insufficient_quota')) as open_url:
            with self.assertRaises(APIError) as raised:
                Model('not-a-real-key').call('test', [{'type': 'input_text', 'text': 'test'}])
        self.assertEqual(open_url.call_count, 1)
        self.assertEqual(raised.exception.code, 'insufficient_quota')


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.clock = patch('api_errors.time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.random = patch('api_errors.random.uniform', return_value=0)
        self.random.start()
        self.addCleanup(self.random.stop)
        self.cancel = threading.Event()
        self.cancel.wait = Mock(side_effect=self.advance)
        self.cooldown = Cooldown()
        self.notify = Mock()

    def advance(self, seconds):
        self.now += seconds
        return self.cancel.is_set()

    def test_transient_failure_restarts_operation_after_server_delay(self):
        operation = Mock(side_effect=[APIError(429, 'rate limit', retryable=True, retry_after=40), 'ok'])
        prepare = Mock(return_value=True)
        self.assertEqual(run_operation(operation, self.cancel, self.cooldown, self.notify, prepare), 'ok')
        self.assertEqual(self.now, 40)
        self.assertEqual(operation.call_count, 2)
        prepare.assert_called_once()

    def test_retries_are_bounded_and_back_off(self):
        operation = Mock(side_effect=APIError(429, 'rate limit', retryable=True))
        with self.assertRaises(APIError):
            run_operation(operation, self.cancel, self.cooldown, self.notify, lambda: True)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(self.now, 45)  # 15 then 30, never immediately loop.
        self.assertEqual(self.cooldown.remaining(), 60)

    def test_quota_failure_does_not_retry_or_enter_cooldown(self):
        operation = Mock(side_effect=APIError(429, 'quota', code='insufficient_quota'))
        with self.assertRaises(APIError):
            run_operation(operation, self.cancel, self.cooldown, self.notify, lambda: True)
        operation.assert_called_once()
        self.cancel.wait.assert_not_called()
        self.assertEqual(self.cooldown.remaining(), 0)

    def test_after_typing_or_manual_send_does_not_repeat(self):
        for prepare in (None, lambda: False):
            self.cooldown = Cooldown()
            operation = Mock(side_effect=APIError(429, 'rate limit', retryable=True))
            with self.assertRaises(APIError):
                run_operation(operation, self.cancel, self.cooldown, self.notify, prepare)
            operation.assert_called_once()

    def test_stop_during_cooldown_prevents_retry(self):
        def stop(seconds):
            self.cancel.set()
            return True
        self.cancel.wait.side_effect = stop
        operation = Mock(side_effect=APIError(429, 'rate limit', retryable=True))
        with self.assertRaisesRegex(RuntimeError, '已停止'):
            run_operation(operation, self.cancel, self.cooldown, self.notify, lambda: True)
        operation.assert_called_once()

    def test_long_retry_after_does_not_retry_early(self):
        operation = Mock(side_effect=APIError(429, 'rate limit', retryable=True, retry_after=600))
        with self.assertRaises(APIError):
            run_operation(operation, self.cancel, self.cooldown, self.notify, lambda: True)
        operation.assert_called_once()
        self.assertEqual(self.cooldown.remaining(), 600)
        self.cancel.wait.assert_not_called()


if __name__ == '__main__':
    unittest.main()
