import threading
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from api_errors import APIError
from automation import ConversationSession
from badges import numbered_candidates
from test_automation import view


BOX = dict(left=100, top=110, right=530, bottom=950)
IMAGE_SIZE = (563, 667)
BOUNDS = (0, 0, *IMAGE_SIZE)
POINTS = {'input': (330, 570), 'send': (525, 630)}


def sidebar(*, badge=True, selected=False):
    image = Image.new('RGB', IMAGE_SIZE, '#f5f5f5')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 16, 56, 660), fill='#dcdcdc')
    draw.rectangle((57, 60, 296, 660), fill='#e6e6e8')
    if selected:
        draw.rectangle((57, 67, 296, 125), fill='#cfd0d1')
    draw.rectangle((69, 78, 102, 111), fill='#6094be')
    draw.rectangle((69, 521, 102, 552), fill='#48444e')
    draw.ellipse((30, 79, 44, 93), fill='#fa5151')  # Navigation total.
    draw.rectangle((35, 83, 38, 88), fill='white')
    draw.ellipse((94, 518, 108, 532), fill='#fa5151')  # Plain red dot.
    if badge:
        draw.ellipse((94, 70, 108, 84), fill='#fa5151')
        draw.line(((98, 74), (103, 74), (103, 77), (98, 80), (103, 80)), fill='white')
    return image


class VisualBadgeTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.stop.wait = Mock(return_value=False)
        self.desktop = Mock(visual_badges=True)
        self.desktop.rect.return_value = BOUNDS
        self.image = sidebar()
        self.desktop.capture.side_effect = lambda hwnd: self.image
        self.client = Mock()
        self.client.reply.return_value = '收到'
        self.session = ConversationSession(self.desktop, 1, BOUNDS, POINTS, self.stop, set())
        self.target = dict(conversation='老公最喜...', x=260, y=145, confidence=.55,
                           badge_type='red_number', badge_text='2')
        self.initial = view('原有会话', incoming=False, list_box=BOX, unread=[self.target])
        self.opened = view('实际完整标题', list_box=BOX)
        self.filled = dict(self.opened, input_text='收到', input_empty=False)

    def click_correct_row(self, hwnd, point, stop):
        if point == (85, 95):
            self.image = sidebar(badge=False, selected=True)

    def test_crop_finds_avatar_badge_but_ignores_navigation_total_and_plain_dot(self):
        candidates = numbered_candidates(self.image, BOX)
        self.assertEqual([badge.point for badge in candidates], [(85, 95)])

    def test_truncated_preview_is_replaced_only_after_verified_open_and_send(self):
        self.desktop.click.side_effect = self.click_correct_row
        self.client.inspect.side_effect = [self.initial, self.opened, self.opened, self.filled]
        kind, payload = self.session.poll(self.client, '', unread_only=True)
        self.assertEqual(kind, 'sent')
        self.assertEqual(payload[0]['conversation'], '实际完整标题')
        self.desktop.click.assert_any_call(1, (85, 95), self.stop)
        self.desktop.type_text.assert_called_once_with(1, '收到', self.stop)

    def test_model_row_with_no_numbered_badge_is_never_clicked(self):
        false_row = dict(self.target, conversation='瑛^Bo^', y=790, confidence=.99)
        self.client.inspect.return_value = dict(self.initial, unread=[false_row])
        self.session.poll(self.client, '', allow_scroll=False, unread_only=True)
        self.desktop.click.assert_not_called()
        self.assertIn('角标', self.session.skipped['瑛^Bo^'])

    def test_wrong_click_leaving_original_badge_does_not_type(self):
        self.client.inspect.return_value = self.initial
        kind, _ = self.session.poll(self.client, '', unread_only=True)
        self.assertEqual(kind, 'status')
        self.desktop.type_text.assert_not_called()
        self.assertIn('角标清除', self.session.skipped['老公最喜...'])

    def test_selection_change_during_generation_does_not_type(self):
        self.desktop.click.side_effect = self.click_correct_row
        self.client.inspect.side_effect = [self.initial, self.opened]
        self.client.reply.side_effect = self.select_other_row
        self.assertEqual(self.session.poll(self.client, '', unread_only=True)[0], 'status')
        self.desktop.type_text.assert_not_called()

    def test_disappearing_badge_alone_cannot_override_a_title_mismatch(self):
        self.desktop.click.side_effect = lambda *args: setattr(self, 'image', sidebar(badge=False))
        self.client.inspect.side_effect = [self.initial, self.opened]
        self.assertEqual(self.session.poll(self.client, '', unread_only=True)[0], 'status')
        self.desktop.type_text.assert_not_called()
        self.assertIn('标题不匹配', self.session.skipped['老公最喜...'])

    def select_other_row(self, view, persona):
        self.image = sidebar(badge=False, selected=False)
        return '收到'

    def test_api_retry_reuses_proven_selection_after_badge_clears(self):
        self.desktop.click.side_effect = self.click_correct_row
        self.client.inspect.side_effect = [self.initial, APIError(429, '限流'), self.opened, self.opened, self.filled]
        with self.assertRaises(APIError):
            self.session.poll(self.client, '', unread_only=True)
        self.assertTrue(self.session.prepare_retry())
        self.assertEqual(self.session.poll(self.client, '', unread_only=True)[0], 'sent')
        self.assertEqual(sum(call.args[1] == (85, 95) for call in self.desktop.click.call_args_list), 1)
