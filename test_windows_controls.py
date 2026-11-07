"""Regression checks for Windows input takeover without moving the real mouse."""
import ctypes
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch


class WindowsControlsTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('windows_controls_under_test',
                                                     Path(__file__).with_name('desktop_windows.py'))
        self.desktop = importlib.util.module_from_spec(spec)
        self.user32 = Mock()
        self.user32.GetAsyncKeyState.return_value = 0
        self.user32.GetForegroundWindow.return_value = 1
        self.user32.GetAncestor.return_value = 1
        self.user32.SendInput.side_effect = lambda count, batch, size: count
        with patch.object(ctypes, 'WinDLL', return_value=self.user32, create=True):
            spec.loader.exec_module(self.desktop)
        self.position = (50, 50)
        self.desktop.cursor = lambda: self.position
        self.user32.SetCursorPos.side_effect = self.set_position
        self.cancel = threading.Event()
        self.desktop.start_input_watch(self.cancel)

    def set_position(self, x, y):
        self.position = (x, y)
        return True

    def test_injected_motion_is_ignored_and_manual_motion_stops_before_next_wheel(self):
        point = (80, 90)
        self.desktop.scroll(1, point, 80, self.cancel)
        self.desktop.scroll(1, point, -1, self.cancel)
        self.user32.SetCursorPos.assert_called_once_with(*point)
        self.assertIsNone(self.desktop.poll_input_watch(self.cancel))
        wheel_deltas = [ctypes.c_int32(call.args[1][0].mi.mouseData).value
                        for call in self.user32.SendInput.call_args_list]
        self.assertEqual(wheel_deltas, [480, -120])
        self.position = (91, 90)
        self.assertIn('鼠标移动', self.desktop.poll_input_watch(self.cancel))
        with self.assertRaisesRegex(RuntimeError, '鼠标移动'):
            self.desktop.scroll(1, point, -1, self.cancel)
        self.assertEqual(self.position, (91, 90))
        self.assertEqual(self.user32.SendInput.call_count, 2)

    def test_escape_can_cancel_from_the_ui_watch_while_worker_is_idle(self):
        self.user32.GetAsyncKeyState.return_value = -32768
        self.assertIn('Esc', self.desktop.poll_input_watch(self.cancel))
        self.assertTrue(self.cancel.is_set())
        self.user32.SendInput.assert_not_called()
