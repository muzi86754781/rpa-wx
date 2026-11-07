"""Stateful desktop simulation: multiple pages, unread flags and row reordering."""
import copy
import hashlib
import threading
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from api_errors import APIError
from automation import ConversationSession
from core import parse_view


BOX = dict(left=50, top=100, right=300, bottom=950)
BOUNDS = (100, 50, 1100, 850)
POINTS = {'input': (600, 700), 'send': (1000, 800)}


class FakeWeChat:
    def __init__(self, names, page_size=2):
        self.order = ['当前'] + list(names)
        self.chats = {name: dict(message='来自' + name, incoming=True,
                                 unread=True, draft='', confidence=.99) for name in names}
        self.chats['当前'] = dict(message='我已回复', incoming=False, unread=False, draft='', confidence=.99)
        self.current = '当前'
        self.offset, self.page_size = 0, page_size
        self.sent, self.opened, self.scrolls = [], [], []
        self.on_send = None
        self.redirect_next = None
        self.bounds = BOUNDS

    def rect(self, hwnd):
        return self.bounds

    def activate(self, hwnd):
        pass

    def check(self, hwnd, stop):
        if stop.is_set():
            raise RuntimeError('已停止')

    def visible(self):
        return self.order[self.offset:self.offset + self.page_size]

    def row_y(self, index):
        return round(100 + (index + .5) * 850 / self.page_size)

    def capture(self, hwnd):
        chat = self.chats[self.current]
        rows = [dict(conversation=name, x=150, y=self.row_y(i),
                     confidence=self.chats[name].get('row_confidence', .99),
                     badge_type=self.chats[name].get('badge_type', 'red_number'),
                     badge_text=self.chats[name].get('badge_text', '1'))
                for i, name in enumerate(self.visible()) if self.chats[name]['unread']]
        view = dict(conversation=self.current, message=chat['message'], incoming=chat['incoming'],
                    confidence=chat['confidence'], context='', input_empty=not chat['draft'],
                    input_text=chat['draft'], list_box=BOX, unread=rows,
                    list_end_visible=self.offset + self.page_size >= len(self.order))
        image = Image.new('RGB', (200, 160), tuple(hashlib.sha256(repr(view).encode()).digest()[:3]))
        # Only the sidebar affects page-end detection. Changing chat bodies must not.
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 16, 60, 152), fill='white')
        for i, name in enumerate(self.visible()):
            y = round(16 + i * 136 / self.page_size)
            digest = hashlib.sha256(name.encode()).digest()
            draw.rectangle((13, y + 4, 26, y + 28), fill=tuple(digest[:3]))
            for j in range(8):
                draw.line((30, y + j * 3 + 3, 30 + digest[j] % 25, y + j * 3 + 3), fill=(50, 50, 50))
            if self.chats[name]['unread']:
                draw.ellipse((23, y + 2, 29, y + 8), fill='red')
        image.info['view'] = copy.deepcopy(view)
        image.info['scroll_offset'] = self.offset
        return image

    def scroll(self, hwnd, point, lines, stop):
        self.check(hwnd, stop)
        self.scrolls.append(lines)
        if lines > 0:
            self.offset = max(0, self.offset - lines)
        else:
            self.offset = min(max(0, len(self.order) - self.page_size), self.offset + 1)

    def click(self, hwnd, point, stop):
        self.check(hwnd, stop)
        if point == POINTS['input']:
            return
        if point == POINTS['send']:
            chat = self.chats[self.current]
            self.sent.append((self.current, chat['draft']))
            chat.update(draft='', incoming=False, unread=False)
            self.order.remove(self.current)
            self.order.insert(0, self.current)  # Real clients reorder on new messages / sends.
            if self.on_send:
                self.on_send(self.current)
            return
        candidates = [(abs(point[1] - round(50 + 800*self.row_y(i)/1000)), name)
                      for i, name in enumerate(self.visible())]
        name = min(candidates)[1]
        if self.redirect_next:
            name, self.redirect_next = self.redirect_next, None
        self.current = name
        self.opened.append(name)
        self.chats[name]['unread'] = False

    def type_text(self, hwnd, text, stop):
        self.check(hwnd, stop)
        self.chats[self.current]['draft'] += text


class FakeClient:
    def __init__(self, desktop):
        self.desktop = desktop
        self.reply_hook = None
        self.inspect_hook = None
        self.replies = []

    def inspect(self, image):
        result = copy.deepcopy(image.info['view'])
        if self.inspect_hook:
            self.inspect_hook(result)
        return result

    def reply(self, view, persona):
        self.replies.append(view['conversation'])
        if self.reply_hook:
            self.reply_hook(view)
        return '回复' + view['conversation']


class SweepTests(unittest.TestCase):
    def create_session(self, names, page_size=2):
        self.desktop = FakeWeChat(names, page_size)
        self.client = FakeClient(self.desktop)
        self.stop = threading.Event()
        self.stop.wait = Mock(side_effect=lambda seconds: self.stop.is_set())
        self.session = ConversationSession(self.desktop, 1, BOUNDS, POINTS, self.stop, set())
        self.events = []

    def report(self, kind, value):
        self.events.append((kind, value))

    def test_return_to_top_does_not_repeat_reverse_probes_after_direction_is_known(self):
        self.create_session(['甲', '乙', '丙', '丁', '戊'], page_size=2)
        self.desktop.offset = 1
        self.session.scroll_direction_known = True
        original_scroll = self.desktop.scroll

        def uneven_wheel(hwnd, point, lines, stop):
            # An opposing detent moves two rows, while the small restoring step moves one.
            original_scroll(hwnd, point, lines, stop)
            if lines < 0:
                self.desktop.offset = min(len(self.desktop.order) - 2, self.desktop.offset + 1)

        self.desktop.scroll = uneven_wheel
        self.session.go_to_top(self.desktop.capture(1), BOX)
        self.assertEqual(self.desktop.offset, 0)
        self.assertEqual(self.desktop.scrolls, [4, 4, 4])

    def sweep(self):
        return self.session.sweep(self.client, '简短回复', self.report)

    def test_all_unreads_across_pages_are_replied_when_current_chat_is_outgoing(self):
        self.create_session(['甲','乙','丙','丁'])
        result = self.sweep()
        self.assertEqual({name for name, text in self.desktop.sent}, {'甲','乙','丙','丁'})
        self.assertEqual(result.sent, 4)
        self.assertEqual(result.opened, 4)
        self.assertEqual(self.desktop.offset, 0)
        self.assertEqual([kind for kind, value in self.events].count('complete'), 1)

    def test_bad_coordinate_is_rechecked_and_recovered_before_opening(self):
        self.create_session(['甲', '乙'], page_size=3)
        calls = 0
        def recognize(result):
            nonlocal calls
            calls += 1
            if calls == 1:
                result['unread'][0]['x'] = 900
            result.update(parse_view(result, (200, 160)))
        self.client.inspect_hook = recognize
        stats = self.sweep()
        self.assertEqual(stats.sent, 2)
        self.assertEqual(stats.skipped, 0)
        self.assertTrue(any(kind == 'status' and '复核' in value for kind, value in self.events))

    def test_bad_coordinate_is_skipped_while_other_unreads_are_replied(self):
        self.create_session(['甲', '乙'], page_size=3)
        def recognize(result):
            for row in result['unread']:
                if row['conversation'] == '甲':
                    row['x'] = 900
            result.update(parse_view(result, (200, 160)))
        self.client.inspect_hook = recognize
        stats = self.sweep()
        self.assertEqual(stats.sent, 1)
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(self.desktop.opened, ['乙'])
        self.assertTrue(self.desktop.chats['甲']['unread'])
        self.assertIn('甲', self.session.skipped)

    def test_unusable_list_is_rechecked_once_and_never_scrolled_or_clicked(self):
        self.create_session(['甲'])
        calls = 0
        def recognize(result):
            nonlocal calls
            calls += 1
            result['list_box'] = None
            result.update(parse_view(result, (200, 160)))
        self.client.inspect_hook = recognize
        with self.assertRaisesRegex(RuntimeError, '重新识别后仍无法定位'):
            self.sweep()
        self.assertEqual(calls, 2)
        self.assertEqual(self.desktop.opened, [])
        self.assertEqual(self.desktop.scrolls, [])
        self.assertEqual(self.desktop.sent, [])

    def test_unrelated_bad_sidebar_does_not_abort_post_typing_verification(self):
        self.create_session(['甲'])
        def recognize(result):
            if result['input_text']:
                result['unread'] = [dict(conversation='其他会话', x=900, y=300, confidence=.99, badge_type='red_number', badge_text='1')]
            result.update(parse_view(result, (200, 160)))
        self.client.inspect_hook = recognize
        self.assertEqual(self.sweep().sent, 1)
        self.assertEqual(self.desktop.sent, [('甲', '回复甲')])

    def test_stop_during_coordinate_recheck_prevents_all_mouse_actions(self):
        self.create_session(['甲'])
        calls = 0
        def recognize(result):
            nonlocal calls
            calls += 1
            if calls == 1:
                result['unread'][0]['x'] = 900
                result.update(parse_view(result, (200, 160)))
            else:
                self.stop.set()
        self.client.inspect_hook = recognize
        with self.assertRaises(RuntimeError):
            self.sweep()
        self.assertEqual(self.desktop.opened, [])
        self.assertEqual(self.desktop.scrolls, [])
        self.assertEqual(self.desktop.sent, [])

    def test_old_read_incoming_message_is_not_replied_just_because_chat_is_open(self):
        self.create_session(['甲','乙'])
        self.desktop.chats['当前'].update(message='以前读过的消息', incoming=True, unread=False)
        self.assertEqual(self.sweep().sent, 2)
        self.assertNotIn('当前', [name for name, text in self.desktop.sent])

    def test_starting_mid_list_still_visits_unreads_above_and_below(self):
        self.create_session(['甲','乙','丙','丁','戊'])
        self.desktop.offset = 3
        self.assertEqual(self.sweep().sent, 5)
        self.assertEqual(len(self.desktop.sent), 5)

    def test_more_than_fifty_pages_are_not_silently_truncated(self):
        self.create_session(['会话' + str(i) for i in range(65)])
        for name in self.desktop.order[1:-1]:
            self.desktop.chats[name]['unread'] = False
            self.desktop.chats[name]['incoming'] = False
        result = self.sweep()
        self.assertEqual(self.desktop.sent[0][0], '会话64')
        self.assertEqual(result.sent, 1)
        self.assertGreater(self.desktop.scrolls.count(-1), 50)

    def test_list_reorder_and_new_sender_do_not_skip_other_unreads(self):
        self.create_session(['甲','乙','丙'])
        def incoming(name):
            if name == '甲' and '新联系人' not in self.desktop.chats:
                self.desktop.chats['新联系人'] = dict(message='新消息', incoming=True, unread=True, draft='', confidence=.99)
                self.desktop.order.insert(0,'新联系人')
        self.desktop.on_send = incoming
        self.assertEqual(self.sweep().sent, 4)
        self.assertEqual(len({name for name, text in self.desktop.sent}), 4)

    def test_existing_draft_is_preserved_and_remaining_unreads_continue(self):
        self.create_session(['甲','乙','丙'])
        self.desktop.chats['甲']['draft'] = '用户未完成的草稿'
        result = self.sweep()
        self.assertEqual({name for name, text in self.desktop.sent}, {'乙','丙'})
        self.assertEqual(self.desktop.chats['甲']['draft'], '用户未完成的草稿')
        self.assertEqual(result.skipped, 1)
        self.assertTrue(any(kind == 'skip' and value[0] == '甲' for kind, value in self.events))

    def test_low_confidence_chat_does_not_stop_other_unreads(self):
        self.create_session(['甲','乙'])
        self.desktop.chats['甲']['confidence'] = .6
        result = self.sweep()
        self.assertEqual(result.sent, 1)
        self.assertEqual(self.desktop.sent[0][0], '乙')
        self.assertEqual(result.skipped, 1)

    def test_wrong_opened_chat_is_never_sent_and_other_chats_continue(self):
        self.create_session(['甲','乙','丙'])
        self.desktop.redirect_next = '丙'
        result = self.sweep()
        self.assertEqual([name for name, text in self.desktop.sent], ['乙'])
        self.assertGreaterEqual(result.skipped, 2)

    def test_stop_after_first_reply_prevents_all_subsequent_replies(self):
        self.create_session(['甲','乙','丙'])
        self.desktop.on_send = lambda name: self.stop.set()
        with self.assertRaises(RuntimeError):
            self.sweep()
        self.assertEqual(len(self.desktop.sent), 1)
        self.assertFalse(any(kind == 'complete' for kind, value in self.events))

    def test_rate_limit_after_opening_does_not_lose_message_when_badge_clears(self):
        self.create_session(['甲','乙'])
        failed = set()
        def reply(view):
            if view['conversation'] == '甲' and '甲' not in failed:
                failed.add('甲')
                raise APIError(429, '限流', retryable=True)
        self.client.reply_hook = reply
        with self.assertRaises(APIError):
            self.sweep()
        self.assertFalse(self.desktop.chats['甲']['unread'])
        self.assertTrue(self.session.prepare_retry())
        self.assertEqual(self.sweep().sent, 2)
        self.assertEqual({name for name, text in self.desktop.sent}, {'甲','乙'})

    def test_rate_limit_after_typing_preserves_draft_and_never_continues(self):
        self.create_session(['甲','乙'])
        def inspect(view):
            if view['conversation'] == '甲' and view['input_text']:
                raise APIError(429, '限流', retryable=True)
        self.client.inspect_hook = inspect
        with self.assertRaises(APIError) as error:
            self.sweep()
        self.assertTrue(error.exception.input_pending)
        self.assertFalse(self.session.prepare_retry())
        self.assertEqual(self.desktop.chats['甲']['draft'], '回复甲')
        self.assertFalse(self.desktop.sent)
        self.assertNotIn('乙', self.desktop.opened)

    def test_list_changes_during_recognition_are_re_read_before_click(self):
        self.create_session(['甲','乙','丙'])
        changed = set()
        def inspect(view):
            if view['unread'] and not changed:
                changed.add(True)
                self.desktop.order[1], self.desktop.order[2] = self.desktop.order[2], self.desktop.order[1]
        self.client.inspect_hook = inspect
        self.assertEqual(self.sweep().sent, 3)
        self.assertEqual(len(self.desktop.sent), len({name for name, text in self.desktop.sent}))

    def test_subsequent_monitor_round_does_not_repeat_prior_replies(self):
        self.create_session(['甲','乙'])
        self.assertEqual(self.sweep().sent, 2)
        self.assertEqual(self.sweep().sent, 0)
        self.assertEqual(len(self.desktop.sent), 2)
        self.desktop.chats['乙'].update(message='又来了新消息', incoming=True, unread=True)
        self.assertEqual(self.sweep().sent, 1)
        self.assertEqual(len(self.desktop.sent), 3)

    def test_current_chat_without_a_numbered_badge_is_not_auto_replied(self):
        self.create_session([])
        self.assertEqual(self.sweep().sent, 0)
        self.desktop.chats['当前'].update(message='监控开始后刚收到的消息', incoming=True, unread=False)
        self.assertEqual(self.sweep().sent, 0)
        self.assertEqual(self.desktop.sent, [])

    def test_plain_dot_badges_are_ignored_and_lower_numbered_badges_are_replied(self):
        self.create_session(['纯红点', '灰色数字', '数字红角标'])
        self.desktop.chats['纯红点'].update(badge_type='red_dot', badge_text='')
        self.desktop.chats['灰色数字'].update(badge_type='other', badge_text='3')
        self.assertEqual(self.sweep().sent, 1)
        self.assertEqual(self.desktop.opened, ['数字红角标'])

    def test_inverted_device_scroll_is_corrected_and_all_lower_chats_are_processed(self):
        self.create_session(['甲', '乙', '丙', '丁', '戊'], page_size=4)
        original_scroll = self.desktop.scroll
        self.desktop.scroll = lambda hwnd, point, lines, stop: original_scroll(hwnd, point, -lines, stop)
        self.assertEqual(self.sweep().sent, 5)
        self.assertEqual(self.session.scroll_sign, -1)
        self.assertEqual({name for name, _ in self.desktop.sent}, {'甲', '乙', '丙', '丁', '戊'})
        self.assertTrue(any(kind == 'status' and '校正' in value for kind, value in self.events))

    def test_nonresponsive_list_with_more_rows_never_reports_complete(self):
        self.create_session(['甲', '乙', '丙'])
        for chat in self.desktop.chats.values():
            chat['unread'] = False
        self.desktop.scroll = lambda *args: None
        with self.assertRaisesRegex(RuntimeError, '尚未确认到底'):
            self.sweep()
        self.assertNotIn('complete', [kind for kind, _ in self.events])

    def test_wrong_direction_at_top_is_detected_by_opposite_probe(self):
        self.create_session(['甲', '乙', '丙', '丁', '戊'], page_size=4)
        for name in ('甲', '乙', '丙'):
            self.desktop.chats[name]['unread'] = False
        original_scroll = self.desktop.scroll
        self.desktop.scroll = lambda hwnd, point, lines, stop: original_scroll(hwnd, point, -lines, stop)
        self.assertEqual(self.session.poll(self.client, '', unread_only=True)[0], 'status')
        self.assertEqual(self.session.scroll_sign, -1)
        self.assertEqual(self.desktop.offset, 1)
        self.assertEqual(self.sweep().sent, 2)

    def test_identical_new_text_after_observed_outgoing_is_not_lost(self):
        self.create_session(['甲'])
        self.assertEqual(self.sweep().sent, 1)
        self.desktop.chats['甲'].update(incoming=True, unread=True)  # Same message text, new incoming turn.
        self.assertEqual(self.sweep().sent, 1)
        self.assertEqual(len(self.desktop.sent), 2)


if __name__ == '__main__':
    unittest.main()
