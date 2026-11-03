"""Backend contract tests with mocked OS bindings; no actual input is posted."""
import importlib.util
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import Mock, patch
from PIL import Image  # Load image dependencies before patching sys.modules.


class MacBackendTests(unittest.TestCase):
    def setUp(self):
        self.quartz = types.ModuleType('Quartz')
        self.kit = types.ModuleType('AppKit')
        self.ax = types.ModuleType('ApplicationServices')
        for name, value in {'kCGEventSourceStateCombinedSessionState': 0,
                            'kCGHIDEventTap': 0, 'kCGEventFlagMaskShift': 2}.items():
            setattr(self.quartz, name, value)
        self.quartz.CGEventSourceKeyState = Mock(return_value=False)
        self.quartz.CGEventCreateKeyboardEvent = Mock(side_effect=lambda *args: object())
        self.quartz.CGEventKeyboardSetUnicodeString = Mock()
        self.quartz.CGEventSetFlags = Mock()
        self.quartz.CGEventPost = Mock()
        spec = importlib.util.spec_from_file_location('mac_backend_test', Path(__file__).with_name('desktop_macos.py'))
        self.backend = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'Quartz': self.quartz, 'AppKit': self.kit, 'ApplicationServices': self.ax}):
            spec.loader.exec_module(self.backend)

    def test_focus_change_prevents_input(self):
        with patch.object(self.backend, 'foreground', return_value=2):
            with self.assertRaises(RuntimeError):
                self.backend.type_text(1, '不要发送', threading.Event(), delay=0)
        self.quartz.CGEventPost.assert_not_called()

    def test_cancel_prevents_input(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(RuntimeError):
            self.backend.type_text(1, '不要发送', stop, delay=0)
        self.quartz.CGEventPost.assert_not_called()

    def test_unicode_emoji_uses_utf16_units(self):
        with patch.object(self.backend, 'foreground', return_value=1):
            self.backend.type_text(1, '你😀', threading.Event(), delay=0)
        lengths = [call.args[1] for call in self.quartz.CGEventKeyboardSetUnicodeString.call_args_list]
        self.assertEqual(lengths, [1, 1, 2, 2])
        self.assertEqual(self.quartz.CGEventPost.call_count, 4)

    def test_newline_uses_shift_enter(self):
        with patch.object(self.backend, 'foreground', return_value=1):
            self.backend.type_text(1, '\n', threading.Event(), delay=0)
        self.quartz.CGEventKeyboardSetUnicodeString.assert_not_called()
        self.assertEqual(self.quartz.CGEventSetFlags.call_count, 2)
        self.assertEqual([c.args[1] for c in self.quartz.CGEventCreateKeyboardEvent.call_args_list], [36, 36])


if __name__ == '__main__':
    unittest.main()
