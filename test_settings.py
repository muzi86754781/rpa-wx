from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from settings import SettingsStore, default_profile, normalize_url


class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'RpaWx' / 'settings.json'
        env = patch.dict(os.environ, {'OPENAI_API_KEY': '', 'DASHSCOPE_API_KEY': ''})
        env.start()
        self.addCleanup(env.stop)
        self.store = SettingsStore(self.path)

    def test_provider_models_persist_but_keys_never_reach_disk(self):
        aliyun = replace(default_profile('aliyun-beijing'), key='secret-aliyun-key',
                         vision_model='qwen3-vl-plus', reply_model='qwen-turbo')
        self.store.save(aliyun)
        self.assertEqual(self.store.current.key, 'secret-aliyun-key')
        self.assertNotIn('secret-aliyun-key', repr(self.store.current))
        text = self.path.read_text()
        self.assertNotIn('secret-aliyun-key', text)
        self.assertNotIn('"key"', text)
        loaded = SettingsStore(self.path)
        self.assertEqual(loaded.active, 'aliyun-beijing')
        self.assertEqual(loaded.current.vision_model, 'qwen3-vl-plus')
        self.assertEqual(loaded.current.reply_model, 'qwen-turbo')
        self.assertEqual(loaded.current.key, '')

    def test_switching_provider_does_not_copy_credentials(self):
        self.store.save(replace(default_profile('openai'), key='openai-only'))
        self.store.save(replace(default_profile('aliyun-beijing'), key='aliyun-only'))
        self.assertEqual(self.store.profiles['openai'].key, 'openai-only')
        self.assertEqual(self.store.profiles['aliyun-beijing'].key, 'aliyun-only')
        self.assertEqual(self.store.profiles['aliyun-singapore'].key, '')
        self.assertEqual(self.store.profiles['custom'].key, '')

    def test_imports_only_provider_specific_environment_keys(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'openai-env', 'DASHSCOPE_API_KEY': 'dashscope-env'}):
            store = SettingsStore(self.path)
        self.assertEqual(store.profiles['openai'].key, 'openai-env')
        self.assertEqual(store.profiles['aliyun-beijing'].key, 'dashscope-env')
        self.assertEqual(store.profiles['aliyun-us'].key, '')
        self.assertEqual(store.profiles['custom'].key, '')

    def test_unknown_or_malformed_preferences_fall_back(self):
        self.path.parent.mkdir()
        self.path.write_text('{invalid')
        store = SettingsStore(self.path)
        self.assertTrue(store.notice)
        self.assertEqual(store.active, 'openai')
        self.path.write_text(json.dumps({'active': 'evil', 'profiles': {
            'openai': {'base_url': 'http://example.org', 'key': 'never-load-me'},
            'evil': {'key': 'never-load-me'}}}))
        store = SettingsStore(self.path)
        self.assertEqual(store.current.base_url, 'https://api.openai.com/v1')
        self.assertEqual(store.current.key, '')
        self.path.write_text(json.dumps({'active': [], 'profiles': {}}))
        self.assertEqual(SettingsStore(self.path).active, 'openai')

    def test_failed_save_preserves_current_profile_and_cleans_temporary_file(self):
        self.store.save(default_profile('openai'))
        original = self.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                self.store.save(default_profile('aliyun-beijing'))
        self.assertEqual(self.store.active, 'openai')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob('.settings-*')))

    def test_endpoint_rejects_embedded_credentials_and_bad_protocols(self):
        for url in ('https://user:secret@example.org/v1', 'https://example.org/v1?key=secret',
                    'http://example.org/v1', 'file:///tmp/model', 'https://{WorkspaceId}.example.org/v1',
                    'https://example.org/v1/chat/completions', 'https://example.org/v1/responses'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)
        self.assertEqual(normalize_url(' https://example.org/compatible-mode/v1/ '),
                         'https://example.org/compatible-mode/v1')
        self.assertEqual(normalize_url('http://127.0.0.1:8000/v1'), 'http://127.0.0.1:8000/v1')

    def test_missing_key_is_allowed_when_saving_but_not_when_calling(self):
        self.store.save(default_profile('aliyun-beijing'))
        with self.assertRaisesRegex(ValueError, 'API Key'):
            self.store.current.validated()
        with self.assertRaises(ValueError):
            self.store.save(replace(default_profile('aliyun-beijing'), key='key\nsecret'))

    def test_input_options_persist_atomically_without_credentials(self):
        self.store.save(default_profile('openai'), typing_interval_ms=260, typing_corrections=False)
        restored = SettingsStore(self.path)
        self.assertEqual(restored.typing_interval_ms, 260)
        self.assertFalse(restored.typing_corrections)
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.save(default_profile('openai'), typing_interval_ms=20)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
