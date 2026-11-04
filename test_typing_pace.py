import threading
import unittest
from unittest.mock import Mock, patch
import typing_pace


class TypingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(typing_pace.configure)
        self.stop = threading.Event()
        self.clock = 0
        self.pauses = []
        def wait(seconds):
            self.clock += seconds
            self.pauses.append(seconds)
            return self.stop.is_set()
        self.stop.wait = wait
        self.buffer = []
        self.check = Mock(side_effect=self.assert_running)
        self.rng = Mock()
        self.rng.uniform.side_effect = lambda lo, hi: (lo + hi) / 2
        self.rng.random.return_value = 0

    def assert_running(self):
        if self.stop.is_set():
            raise RuntimeError('stopped')

    def type(self, text, **options):
        with patch('typing_pace.time.monotonic', side_effect=lambda: self.clock):
            typing_pace.type_deliberately(text, self.stop, self.check, self.buffer.append,
                                         lambda: self.buffer.pop(), rng=self.rng, **options)

    def test_temporary_mistakes_are_removed_and_final_unicode_text_is_exact(self):
        self.type('你好😀，再见。', corrections=True)
        self.assertEqual(''.join(self.buffer), '你好😀，再见。')
        self.assertGreater(self.clock, len(self.buffer) * .13)

    def test_punctuation_adds_a_pause(self):
        self.type('。', corrections=False)
        self.assertGreater(self.clock, .4)

    def test_stop_after_wrong_character_prevents_further_input(self):
        def emit(char):
            self.buffer.append(char)
            self.stop.set()
        with patch('typing_pace.time.monotonic', side_effect=lambda: self.clock):
            with self.assertRaises(RuntimeError):
                typing_pace.type_deliberately('你好', self.stop, self.check, emit,
                                             lambda: self.buffer.pop(), corrections=True, rng=self.rng)
        self.assertEqual(self.buffer, ['x'])

    def test_corrections_can_be_disabled_and_explicit_zero_delay_is_deterministic(self):
        self.type('你好😀', delay=0, corrections=True)
        self.assertEqual(''.join(self.buffer), '你好😀')
        self.assertEqual(self.clock, 0)
        self.rng.random.assert_not_called()

    def test_interval_configuration_is_bounded(self):
        for value in (0, 79, 801, True, '180'):
            with self.assertRaises(ValueError):
                typing_pace.configure(value)
