import base64
import copy
import io
import json
import unittest
from unittest.mock import patch
from PIL import Image
from core import Model, LIST_SCHEMA, parse_view
from test_unread_sweep import FakeClient, BOX
import test_unread_sweep as sweep_fixture


class FastListModelTests(unittest.TestCase):
    def raw(self):
        return dict(coordinate_space='normalized_1000', list_box=dict(left=30, top=20, right=960, bottom=980),
                    list_end_visible=False, unread=[dict(conversation='王进喜', x=300, y=400, confidence=.99,
                                                        badge_type='red_number', badge_text=2)])

    def test_sidebar_crop_maps_points_back_to_full_retina_image(self):
        image = Image.new('RGB', (2000, 1600))
        client = Model('unused')
        with patch.object(client, 'call', return_value=json.dumps(self.raw())) as call:
            result = client.inspect_list(image, BOX)
        row = result['unread'][0]
        self.assertEqual((row['x'], row['y']), (123, 438))
        self.assertEqual(row['badge_text'], '2')
        self.assertEqual(result['conversation'], '')
        self.assertFalse(result['input_empty'])
        parts = call.call_args.args[1]
        encoded = parts[1]['image_url'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as crop:
            self.assertEqual(crop.size, (540, 1392))
            self.assertLess(crop.width * crop.height, image.width * image.height * .3)
        self.assertIs(call.call_args.args[2], LIST_SCHEMA)

    def test_invalid_local_coordinate_is_never_mapped_to_a_click(self):
        raw = self.raw()
        raw['unread'][0]['x'] = 999
        client = Model('unused')
        with patch.object(client, 'call', return_value=json.dumps(raw)):
            result = client.inspect_list(Image.new('RGB', (2000, 1600)), BOX)
        self.assertFalse(result['unread'])
        self.assertIn('x=999', result['navigation_issues'][0][1])

    def test_numeric_strings_and_integer_badge_are_unambiguously_normalized(self):
        raw = self.raw()
        raw.update(conversation='当前', message='', context='', incoming=False,
                   confidence='0.99', input_empty=True, input_text='')
        raw['unread'][0].update(x='300.0', y='400', confidence='0.99')
        result = parse_view(raw, (2000, 1600))
        self.assertEqual(len(result['unread']), 1)
        self.assertEqual(result['unread'][0]['badge_text'], '2')


class FastClient(FakeClient):
    def __init__(self, desktop):
        super().__init__(desktop)
        self.full_calls = self.list_calls = 0

    def inspect(self, image):
        self.full_calls += 1
        return super().inspect(image)

    def inspect_list(self, image, box):
        self.list_calls += 1
        data = copy.deepcopy(image.info['view'])
        data.update(conversation='', incoming=False, message='', context='', confidence=0,
                    input_empty=False, input_text='')
        return data


class FastListSweepTests(unittest.TestCase):
    def setup_scene(self, names, page_size=2):
        scene = sweep_fixture.SweepTests()
        scene.create_session(names, page_size)
        scene.client = FastClient(scene.desktop)
        return scene

    def test_fast_list_sweep_keeps_full_send_checks_and_dedupe(self):
        scene = self.setup_scene(['甲', '乙', '丙'])
        self.assertEqual(scene.sweep().sent, 3)
        self.assertGreater(scene.client.list_calls, 0)
        self.assertGreaterEqual(scene.client.full_calls, 3 * 3)
        self.assertEqual(scene.sweep().sent, 0)
        scene.desktop.chats['甲'].update(incoming=True, unread=True)
        self.assertEqual(scene.sweep().sent, 1)

    def test_right_chat_changes_do_not_invalidate_sidebar_cache(self):
        scene = self.setup_scene(['甲'])
        scene.session.read_page(scene.client)
        scene.session.read_page(scene.client)
        calls = scene.client.list_calls
        scene.desktop.chats['当前']['message'] = '右侧变化，不影响列表'
        scene.session.read_page(scene.client)
        self.assertEqual(scene.client.list_calls, calls)

    def test_pre_input_recheck_still_catches_message_changes_in_fast_mode(self):
        scene = self.setup_scene(['甲'])
        def during_generation(view):
            scene.desktop.chats['甲']['message'] = '生成期间新到的消息'
        scene.client.reply_hook = during_generation
        self.assertEqual(scene.sweep().sent, 0)
        self.assertEqual(scene.desktop.sent, [])
        self.assertEqual(scene.desktop.chats['甲']['draft'], '')

    def test_invalid_full_view_row_is_recovered_with_focused_list_read(self):
        scene = self.setup_scene(['王进喜'])
        def faulty_full(result):
            if result['unread']:
                result['unread'][0]['x'] = 900
                result.update(parse_view(result, (200, 160)))
        scene.client.inspect_hook = faulty_full
        _, result = scene.session.read_page(scene.client)
        self.assertEqual(scene.client.full_calls, 1)
        self.assertEqual(scene.client.list_calls, 1)
        self.assertEqual(result['unread'][0]['conversation'], '王进喜')
        self.assertFalse(scene.session.skipped)
