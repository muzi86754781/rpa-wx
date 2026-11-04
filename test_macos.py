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
        self.quartz.CGEventSetLocation = Mock()
        self.quartz.kCGScrollEventUnitPixel = 0
        stop = threading.Event()
        with patch.object(self.backend, 'move') as move, patch.object(self.backend, 'check'), \
                patch.object(self.backend, 'rect', return_value=(0, 0, 1000, 800)):
            self.backend.scroll(1, (100, 200), -8, stop)
        move.assert_called_once_with(1, (100, 200), stop)
        self.quartz.CGEventPost.assert_called_once_with(0, 'scroll')
        self.quartz.CGEventCreateScrollWheelEvent.assert_called_once_with(None, 0, 1, -360)
        self.quartz.CGEventSetLocation.assert_called_once_with('scroll', (100, 200))

    def prepare_hit_test(self, hit=1, primary_height=1200):
        self.kit.NSWindow = Mock()
        self.kit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.return_value = hit
        self.kit.NSMakePoint = lambda x, y: (x, y)
        self.kit.NSScreen = Mock()
        screen = Mock()
        screen.frame.return_value = types.SimpleNamespace(
            origin=types.SimpleNamespace(x=0, y=0),
            size=types.SimpleNamespace(width=1920, height=primary_height))
        self.kit.NSScreen.screens.return_value = [screen]
        self.quartz.kCGWindowOwnerName = 'owner'

    def test_click_through_overlays_do_not_override_native_hit(self):
        self.prepare_hit_test()
        # The Dock can report an opaque-looking full-screen bounding rectangle;
        # only the native mouse-down hit result determines the recipient.
        with patch.object(self.backend, 'rect', return_value=(0, 25, 1920, 1137)), \
                patch.object(self.backend, 'windows', return_value=[
                    {'owner': 'Dock', 'bounds': (0, 0, 1920, 1200)},
                    {'owner': 'Input Method', 'bounds': (500, 500, 525, 520)}]) as windows:
            self.backend.check_target(1, (510, 510))
        windows.assert_not_called()
        self.kit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.assert_called_once_with((510, 690), 0)

    def test_hit_coordinates_use_primary_screen_points_for_all_displays(self):
        self.prepare_hit_test(primary_height=1200)
        # Left, above and below secondary displays; no Retina scaling and no
        # dependency on which display currently hosts the key window.
        with patch.object(self.backend, 'rect', return_value=(-3000, -2000, 4000, 4000)):
            for point, expected in [((-600, 200), (-600, 1000)),
                                    ((500, -300), (500, 1500)),
                                    ((500, 1500), (500, -300))]:
                self.backend.check_target(1, point)
                self.assertEqual(self.kit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.call_args.args,
                                 (expected, 0))
        self.kit.NSScreen.mainScreen.assert_not_called()

    def test_real_blocker_reports_owner_and_window_number(self):
        self.prepare_hit_test(hit=99)
        with patch.object(self.backend, 'rect', return_value=(0, 0, 1000, 800)), \
                patch.object(self.backend, 'window', return_value={'owner': '真实弹窗'}):
            with self.assertRaisesRegex(RuntimeError, '真实弹窗.*99'):
                self.backend.check_target(1, (500, 500))

    def test_other_window_in_same_app_is_still_blocked(self):
        self.prepare_hit_test(hit=2)
        with patch.object(self.backend, 'rect', return_value=(0, 0, 1000, 800)), \
                patch.object(self.backend, 'window', return_value={'owner': '微信', 'pid': 42}):
            with self.assertRaisesRegex(RuntimeError, '微信.*ID 2'):
                self.backend.check_target(1, (500, 500))

    def test_missing_native_hit_fails_without_allowing_click(self):
        self.prepare_hit_test(hit=0)
        with patch.object(self.backend, 'rect', return_value=(0, 0, 1000, 800)):
            with self.assertRaisesRegex(RuntimeError, '无法确认'):
                self.backend.check_target(1, (500, 500))

    def test_outside_target_is_rejected_before_hit_query(self):
        self.prepare_hit_test()
        with patch.object(self.backend, 'rect', return_value=(100, 100, 1000, 800)):
            for point in [(99, 200), (1000, 200), (500, 800)]:
                with self.assertRaisesRegex(RuntimeError, '不在绑定窗口内'):
                    self.backend.check_target(1, point)
        self.kit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.assert_not_called()

    def test_no_screens_fails_without_hit_query(self):
        self.prepare_hit_test()
        self.kit.NSScreen.screens.return_value = []
        with patch.object(self.backend, 'rect', return_value=(0, 0, 1000, 800)):
            with self.assertRaisesRegex(RuntimeError, '屏幕坐标'):
                self.backend.check_target(1, (500, 500))
        self.kit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.assert_not_called()

    def test_occlusion_aborts_before_mouse_down(self):
        self.prepare_hit_test(hit=2)
        self.quartz.kCGEventMouseMoved = 5
        self.quartz.kCGMouseButtonLeft = 0
        self.quartz.kCGEventLeftMouseDown = 1
        self.quartz.kCGEventLeftMouseUp = 2
        self.quartz.CGEventCreateMouseEvent = Mock(return_value='move')
        with patch.object(self.backend, 'rect', return_value=(0, 0, 1000, 800)), \
                patch.object(self.backend, 'window', return_value={'owner': '弹窗'}), \
                patch.object(self.backend, 'cursor', return_value=(100, 100)), \
                patch.object(self.backend, 'foreground', return_value=1), \
                patch.object(self.backend.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '弹窗'):
                self.backend.click(1, (500, 500), threading.Event())
        self.assertEqual(self.quartz.CGEventCreateMouseEvent.call_count, 30)
        self.assertEqual({c.args[1] for c in self.quartz.CGEventCreateMouseEvent.call_args_list}, {5})


if __name__ == '__main__':
    unittest.main()
