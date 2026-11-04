import unittest
import json
import math
from unittest.mock import patch
from core import Model, fingerprint, response_text, validate_view, parse_view, numbered_badge
from PIL import Image


class CoreTests(unittest.TestCase):
    def test_extracts_only_output_text(self):
        self.assertEqual(response_text({'output': [
            {'type': 'reasoning'}, {'content': [{'type': 'output_text', 'text': '你好'}]}]}), '你好')

    def test_refusal_does_not_become_reply(self):
        with self.assertRaises(ValueError):
            response_text({'output': [{'content': [{'type': 'refusal', 'refusal': 'no'}]}]})

    def test_dedupe_is_scoped_to_conversation(self):
        self.assertNotEqual(fingerprint({'conversation': '甲', 'message': '你好'}),
                            fingerprint({'conversation': '乙', 'message': '你好'}))

    def test_invalid_vision_data_rejected(self):
        with patch.object(Model, 'call', return_value='{"conversation":"甲","message":"hi","incoming":"false","confidence":1}'):
            with self.assertRaises(ValueError):
                Model('unused').inspect(Image.new('RGB', (400, 300)))

    def test_unread_coordinate_cannot_escape_the_chat_list(self):
        view = dict(conversation='甲', message='你好', context='', incoming=True,
                    confidence=.99, input_empty=True, input_text='',
                    list_box=dict(left=50, top=100, right=300, bottom=950),
                    unread=[dict(conversation='乙', x=900, y=300, confidence=.99, badge_type='red_number', badge_text='1')])
        with self.assertRaises(ValueError):
            validate_view(view)
        view['unread'][0]['x'] = 150
        validate_view(view)

    def test_contradictory_empty_input_is_rejected(self):
        view = dict(conversation='甲', message='你好', context='', incoming=True,
                    confidence=.99, input_empty=True, input_text='已有草稿',
                    list_box=None, unread=[])
        with self.assertRaises(ValueError):
            validate_view(view)

    def navigation_view(self, **overrides):
        data = dict(conversation='甲', message='你好', context='', incoming=True,
                    confidence=.99, input_empty=True, input_text='',
                    list_box=dict(left=50, top=100, right=300, bottom=950),
                    unread=[dict(conversation='乙', x=150, y=300, confidence=.99, badge_type='red_number', badge_text='1')])
        data.update(overrides)
        return data

    def test_declared_pixel_coordinates_convert_using_original_image_dimensions(self):
        data = self.navigation_view(coordinate_space='image_pixels',
                                   list_box=dict(left=100, top=160, right=600, bottom=1520),
                                   unread=[dict(conversation='乙', x=300, y=480, confidence=.99, badge_type='red_number', badge_text='1')])
        parsed = parse_view(data, (2000, 1600))
        self.assertEqual(parsed['list_box'], self.navigation_view()['list_box'])
        self.assertEqual(parsed['unread'], self.navigation_view()['unread'])
        self.assertEqual(parsed['coordinate_space'], 'normalized_1000')
        validate_view(parsed)

    def test_normalized_decimal_coordinates_are_validated_and_rounded(self):
        data = self.navigation_view(coordinate_space='normalized_1000')
        data['list_box']['left'] = 50.0
        data['unread'][0].update(x=150.4, y=300.2)
        parsed = parse_view(data, (2000, 1600))
        self.assertEqual(parsed['unread'][0]['x'], 150)
        validate_view(parsed)

    def test_units_are_never_inferred_from_out_of_range_values(self):
        for unit in ('normalized_1000', 'percent', None):
            data = self.navigation_view()
            if unit is not None:
                data['coordinate_space'] = unit
            data['list_box']['bottom'] = 1520
            parsed = parse_view(data, (2000, 1600))
            self.assertIsNone(parsed['list_box'])
            self.assertFalse(parsed['unread'])
            self.assertIn('navigation_error', parsed)

    def test_bad_row_is_isolated_and_never_clamped_into_the_list(self):
        data = self.navigation_view()
        data['unread'].append(dict(conversation='丙', x=900, y=300, confidence=.99, badge_type='red_number', badge_text='1'))
        parsed = parse_view(data, (2000, 1600))
        self.assertEqual(parsed['unread'], self.navigation_view()['unread'])
        self.assertEqual(parsed['navigation_issues'][0][0], '丙')
        validate_view(parsed)

    def test_nonfinite_bool_and_boundary_coordinates_are_not_clickable(self):
        for field, value in [('x', math.nan), ('y', math.inf), ('x', True),
                             ('x', 50), ('x', 300), ('y', 100), ('y', 950), ('x', -1)]:
            with self.subTest(field=field, value=value):
                data = self.navigation_view()
                data['unread'][0][field] = value
                parsed = parse_view(data, (2000, 1600))
                self.assertEqual(parsed['unread'], [])
                self.assertIn('navigation_issues', parsed)

    def test_pixel_bounds_are_checked_before_normalization(self):
        data = self.navigation_view(coordinate_space='image_pixels')
        data['unread'][0]['x'] = 2001
        self.assertFalse(parse_view(data, (2000, 1600))['unread'])

    def test_bad_sidebar_does_not_invalidate_current_input_verification(self):
        data = self.navigation_view(input_empty=False, input_text='已输入的回复', list_box=None)
        parsed = parse_view(data, (2000, 1600))
        self.assertEqual(parsed['input_text'], '已输入的回复')
        self.assertEqual(parsed['conversation'], '甲')
        self.assertTrue(parsed['navigation_issues'])
        validate_view(parsed, navigation=False)

    def test_untrusted_navigation_diagnostics_are_replaced(self):
        data = self.navigation_view(navigation_error='不可信错误', navigation_issues=[('不可信', '跳过')])
        parsed = parse_view(data, (2000, 1600))
        self.assertNotIn('navigation_error', parsed)
        self.assertNotIn('navigation_issues', parsed)

    def test_vision_prompt_declares_actual_image_size_and_coordinate_rules(self):
        image = Image.new('RGB', (2000, 1600))
        with patch.object(Model, 'call', return_value=json.dumps(self.navigation_view())) as call:
            parsed = Model('unused').inspect(image)
        text = call.call_args.args[1][0]['text']
        self.assertIn('2000×1600', text)
        self.assertIn('coordinate_space=image_pixels', text)
        self.assertIn('list_box.left < x < list_box.right', text)
        self.assertEqual(parsed, self.navigation_view())

    def test_only_positive_numbered_red_badges_are_eligible(self):
        for text in ('1', '12', '99+', '１２'):
            self.assertTrue(numbered_badge(dict(badge_type='red_number', badge_text=text)))
        for kind, text in [('red_dot', ''), ('other', '3'), ('red_number', '0'),
                           ('red_number', '·'), ('red_number', '有人@我'), ('red_number', ''),
                           ('red_number', '3条消息'), ('red_number', '-1')]:
            self.assertFalse(numbered_badge(dict(badge_type=kind, badge_text=text)))
        self.assertFalse(numbered_badge({}))

    def test_plain_red_dots_and_other_badges_are_removed_before_navigation(self):
        data = self.navigation_view()
        for kind in ('red_dot', 'other'):
            row = dict(data['unread'][0], conversation=kind, badge_type=kind, badge_text='')
            data['unread'].append(row)
        parsed = parse_view(data, (2000, 1600))
        self.assertEqual([r['conversation'] for r in parsed['unread']], ['乙'])
        self.assertNotIn('navigation_issues', parsed)

    def test_missing_badge_evidence_is_not_silently_accepted(self):
        data = self.navigation_view()
        del data['unread'][0]['badge_type']
        parsed = parse_view(data, (2000, 1600))
        self.assertEqual(parsed['unread'], [])
        self.assertTrue(parsed['navigation_issues'])


if __name__ == '__main__':
    unittest.main()
