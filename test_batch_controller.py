"""The primary action and monitor must dispatch the same complete sweep."""
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import App
from automation import SweepStats


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class BatchControllerTests(unittest.TestCase):
    def test_primary_action_starts_a_whole_list_session(self):
        ui = SimpleNamespace(busy=False, auto=Value(False), status=Value(''),
                             validate_batch=Mock(), hwnd=1, bounds=(0,0,1000,800),
                             points={'input':(600,700),'send':(900,750)},
                             cancel=threading.Event(), seen=set(), launch_sweep=Mock())
        session = Mock()
        with patch('app.ConversationSession', return_value=session):
            App.scan(ui)
        ui.validate_batch.assert_called_once()
        ui.launch_sweep.assert_called_once_with(session)

    def test_monitor_dispatches_the_same_sweep_entry(self):
        session = Mock()
        ui = SimpleNamespace(auto_session=session, launch_sweep=Mock())
        App.poll_auto(ui)
        ui.launch_sweep.assert_called_once_with(session)

    def test_sweep_reports_per_conversation_events_and_completion_to_ui(self):
        session, model = Mock(), Mock()
        ui = SimpleNamespace(persona=Value('简短'), events=queue.Queue(), launch=Mock())
        App.launch_sweep(ui, session)
        worker = ui.launch.call_args.args[0]
        worker(model)
        args = session.sweep.call_args.args
        self.assertEqual(args[:2], (model, '简短'))
        args[2]('complete', SweepStats(checked=5, opened=3, sent=3, skipped=0))
        kind, stats = ui.events.get_nowait()
        self.assertEqual(kind, 'batch_complete')
        self.assertEqual(stats.sent, 3)
        self.assertEqual(ui.launch.call_args.kwargs['prepare_retry'], session.prepare_retry)


if __name__ == '__main__':
    unittest.main()
