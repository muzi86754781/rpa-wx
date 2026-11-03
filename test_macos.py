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

    def test_binding_finds_background_wechat_without_using_frontmost_app(self):
        app = Mock()
        app.bundleIdentifier.return_value = 'com.tencent.xinWeChat'
        app.processIdentifier.return_value = 42
        workspace = Mock()
        workspace.runningApplications.return_value = [app]
        self.kit.NSWorkspace = Mock()
        self.kit.NSWorkspace.sharedWorkspace.return_value = workspace
        self.kit.NSApplicationActivateIgnoringOtherApps = 1
        with patch.object(self.backend, 'main_window', return_value=123):
            self.assertEqual(self.backend.find_wechat(), 123)
        app.activateWithOptions_.assert_called_once_with(1)
        workspace.frontmostApplication.assert_not_called()

    def test_largest_main_window_wins_over_floating_panel(self):
        for name in ('kCGWindowBounds', 'kCGWindowOwnerPID', 'kCGWindowLayer', 'kCGWindowNumber'):
            setattr(self.quartz, name, name)
        def window(identifier, width, height, pid=42, layer=0):
            return {'kCGWindowNumber': identifier, 'kCGWindowOwnerPID': pid,
                    'kCGWindowLayer': layer,
                    'kCGWindowBounds': {'Width': width, 'Height': height}}
        with patch.object(self.backend, 'windows', return_value=[
                window(1, 400, 320), window(2, 1000, 800),
                window(3, 2000, 1600, pid=99), window(4, 2000, 1600, layer=1)]):
            self.assertEqual(self.backend.main_window(42), 2)

    def test_enterprise_wechat_and_unrelated_apps_are_rejected(self):
        app = Mock()
        for bundle in ('com.tencent.weworkmac', 'com.github.Electron', 'com.fake.wechat'):
            app.bundleIdentifier.return_value = bundle
            self.assertFalse(self.backend.wechat_app(app))
        app.bundleIdentifier.return_value = 'com.tencent.xinWeixin'
        self.assertTrue(self.backend.wechat_app(app))

    def test_scroll_moves_without_clicking_and_honors_stop(self):
        self.quartz.CGEventCreateScrollWheelEvent = Mock(return_value='scroll')
        self.quartz.kCGScrollEventUnitLine = 1
        stop = threading.Event()
        with patch.object(self.backend, 'move') as move, patch.object(self.backend, 'check'):
            self.backend.scroll(1, (100, 200), -8, stop)
        move.assert_called_once_with(1, (100, 200), stop)
        self.quartz.CGEventPost.assert_called_once_with(0, 'scroll')


if __name__ == '__main__':
    unittest.main()
