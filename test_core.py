import unittest
from unittest.mock import patch
from core import Model, fingerprint, response_text


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


if __name__ == '__main__':
    unittest.main()
