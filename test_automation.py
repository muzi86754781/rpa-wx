import threading
import unittest
from unittest.mock import Mock

from PIL import Image

from automation import ConversationSession
from api_errors import APIError
from core import fingerprint


def view(name='甲', message='你好', incoming=True, input_text='', **overrides):
    result = dict(conversation=name, message=message, incoming=incoming,
                  confidence=.99, context='', input_text=input_text,
                  input_empty=not input_text, unread=[],
                  list_box=dict(left=50, top=100, right=300, bottom=950))
    result.update(overrides)
    return result


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.desktop = Mock()
        self.bounds = (100, 50, 1100, 850)
        self.desktop.rect.return_value = self.bounds
        self.desktop.capture.return_value = Image.new('RGB', (2000, 1600))
        self.stop = threading.Event()
        self.stop.wait = Mock(side_effect=lambda seconds: self.stop.is_set())
        self.desktop.check.side_effect = self.check
        self.seen = set()
        self.session = ConversationSession(self.desktop, 1, self.bounds,
                                           {'input': (600, 700), 'send': (1000, 800)},
                                           self.stop, self.seen)
        self.client = Mock()
        self.client.reply.return_value = '你好！'

    def check(self, hwnd, stop):
        if stop.is_set():
            raise RuntimeError('stopped')

    def test_current_incoming_message_is_generated_and_sent_automatically(self):
        original = view()
        self.client.inspect.side_effect = [original, view(), view(input_text='你好！')]
        kind, payload = self.session.poll(self.client, '简短回复')
        self.assertEqual(kind, 'sent')
        self.assertEqual(payload, (original, '你好！'))
        self.assertIn(fingerprint(original), self.seen)
        self.desktop.type_text.assert_called_once_with(1, '你好！', self.stop)
        self.assertEqual(self.desktop.click.call_args_list[-1].args[1], (1000, 800))

    def test_unread_chat_opens_at_logical_coordinates_on_retina(self):
        initial = view(incoming=False, unread=[dict(conversation='乙', x=150, y=200, confidence=.99)])
        selected = view('乙')
        self.client.inspect.side_effect = [initial, selected,
                                           view('乙'), view('乙', input_text='你好！')]
        kind, payload = self.session.poll(self.client, '')
        self.assertEqual(kind, 'sent')
        self.assertEqual(payload[0]['conversation'], '乙')
        self.assertEqual(self.desktop.click.call_args_list[0].args[1], (250, 210))

    def test_wrong_conversation_is_never_replied_to(self):
        initial = view(incoming=False, unread=[dict(conversation='乙', x=150, y=200, confidence=.99)])
        self.client.inspect.side_effect = [initial, view('丙')]
        self.assertEqual(self.session.poll(self.client, '')[0], 'status')
        self.assertIn('乙', self.session.skipped)
        self.assertIn('丙', self.session.skipped)
        self.client.reply.assert_not_called()
        self.desktop.type_text.assert_not_called()

    def test_existing_draft_is_skipped_without_overwriting(self):
        self.client.inspect.return_value = view(input_text='不要覆盖')
        self.assertEqual(self.session.poll(self.client, '')[0], 'status')
        self.assertIn('甲', self.session.skipped)
        self.desktop.click.assert_not_called()
        self.desktop.type_text.assert_not_called()

    def test_new_message_during_generation_prevents_typing(self):
        self.client.inspect.side_effect = [view(), view(message='刚收到的新消息')]
        self.assertEqual(self.session.poll(self.client, '')[0], 'status')
        self.assertIn('甲', self.session.skipped)
        self.desktop.type_text.assert_not_called()

    def test_message_change_after_typing_prevents_send(self):
        self.client.inspect.side_effect = [view(), view(), view(message='新消息', input_text='你好！')]
        with self.assertRaisesRegex(RuntimeError, '输入期间'):
            self.session.poll(self.client, '')
        self.desktop.type_text.assert_called_once()
        self.assertEqual(self.desktop.click.call_count, 1)  # Only the input box.
        self.assertFalse(self.seen)

    def test_partial_or_changed_input_is_never_sent(self):
        self.client.inspect.side_effect = [view(), view(), view(input_text='你好')]
        with self.assertRaisesRegex(RuntimeError, '草稿变化'):
            self.session.poll(self.client, '')
        self.assertEqual(self.desktop.click.call_count, 1)

    def test_stop_while_api_request_is_running_prevents_all_input(self):
        def inspect(image):
            self.stop.set()
            return view()
        self.client.inspect.side_effect = inspect
        with self.assertRaises(RuntimeError):
            self.session.poll(self.client, '')
        self.desktop.type_text.assert_not_called()
        self.desktop.click.assert_not_called()

    def test_duplicate_is_not_sent_again_and_list_is_scrolled(self):
        self.seen.add(fingerprint(view()))
        self.client.inspect.return_value = view()
        self.assertEqual(self.session.poll(self.client, '')[0], 'status')
        self.client.reply.assert_not_called()
        self.desktop.scroll.assert_called_once()

    def test_bottom_requires_two_stable_scrolls_without_clicking_a_row(self):
        self.client.inspect.return_value = view(incoming=False)
        self.assertEqual(self.session.poll(self.client, '')[0], 'status')
        self.assertEqual(self.session.poll(self.client, '')[0], 'bottom')
        self.assertEqual([call.args[2] for call in self.desktop.scroll.call_args_list], [-8, -8])
        self.desktop.click.assert_not_called()

    def test_resized_window_prevents_any_action(self):
        self.desktop.rect.return_value = (0, 0, 500, 400)
        with self.assertRaisesRegex(RuntimeError, '尺寸改变'):
            self.session.poll(self.client, '')
        self.desktop.activate.assert_not_called()
        self.client.inspect.assert_not_called()

    def test_rate_limit_after_typing_preserves_draft_and_disables_retry(self):
        self.client.inspect.side_effect = [view(), view(), APIError(429, '限流', retryable=True)]
        with self.assertRaises(APIError) as raised:
            self.session.poll(self.client, '')
        self.assertTrue(raised.exception.input_pending)
        self.assertFalse(self.session.prepare_retry())
        self.assertIn('草稿', str(raised.exception))
        self.assertEqual(self.desktop.click.call_count, 1)

    def test_retry_before_typing_invalidates_cached_screenshot(self):
        original = view()
        self.client.inspect.return_value = original
        self.client.reply.side_effect = APIError(429, '限流', retryable=True)
        with self.assertRaises(APIError):
            self.session.poll(self.client, '')
        self.assertIsNotNone(self.session.cached_digest)
        self.assertTrue(self.session.prepare_retry())
        self.assertIsNone(self.session.cached_digest)
        self.desktop.type_text.assert_not_called()


if __name__ == '__main__':
    unittest.main()
