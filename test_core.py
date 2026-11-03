import unittest
from unittest.mock import patch
from core import Model, fingerprint, response_text, validate_view


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
        class Image:
            def save(self, buffer, format):
                buffer.write(b'fake')
        with patch.object(Model, 'call', return_value='{"conversation":"甲","message":"hi","incoming":"false","confidence":1}'):
            with self.assertRaises(ValueError):
                Model('unused').inspect(Image())

    def test_unread_coordinate_cannot_escape_the_chat_list(self):
        view = dict(conversation='甲', message='你好', context='', incoming=True,
                    confidence=.99, input_empty=True, input_text='',
                    list_box=dict(left=50, top=100, right=300, bottom=950),
                    unread=[dict(conversation='乙', x=900, y=300, confidence=.99)])
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


if __name__ == '__main__':
    unittest.main()
