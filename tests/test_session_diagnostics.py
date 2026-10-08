"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import tempfile
import unittest
from pathlib import Path

from src.main.session import LocalSessionStore, SessionEvent


class SessionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.store = LocalSessionStore(self.root)
        self.addCleanup(lambda: self.store.close())
        self.messages = [{'role': 'system', 'content': 'test'},
                         {'role': 'user', 'content': 'hello'},
                         {'role': 'assistant', 'content': 'answer'}]
        self.session = self.store.create_session(
            workspace=str(self.root), title='diagnostics', model='test-model',
            messages=self.messages[:1], reasoning_effort='low',
        )
        sid = self.session.session_id
        self.store.append_event(SessionEvent(sid, 'user_message', {'message': self.messages[1]}))
        self.diagnostic = self.store.append_event(SessionEvent(sid, 'response_metadata', {
            'stream': True, 'transport_end': 'done', 'request_id': 'request-1',
            'finish_reasons': [{'index': 0, 'reason': 'stop'}],
        })).event
        self.answer = self.store.append_event(SessionEvent(sid, 'assistant_message', {
            'message': self.messages[2],
        })).event

    def test_reopen_and_resume_preserves_messages_and_diagnostic_log(self):
        self.store.close()
        self.store = LocalSessionStore(self.root)
        state = self.store.resume_session(self.session.session_id)
        self.assertEqual(state.messages, self.messages)
        self.assertEqual(state.reasoning_effort, 'low')
        diagnostics = [e for e in self.store.load_events(self.session.session_id)
                       if e.type == 'response_metadata']
        self.assertEqual(diagnostics, [self.diagnostic])

    def test_fork_can_replay_through_diagnostics(self):
        for boundary, messages in [(self.diagnostic, self.messages[:2]),
                                   (self.answer, self.messages)]:
            with self.subTest(boundary=boundary.type):
                fork = self.store.fork_session(self.session.session_id,
                                              at_event_id=boundary.event_id, title='fork')
                self.assertEqual(self.store.resume_session(fork.session_id).messages, messages)

    def test_unknown_context_events_still_fail(self):
        self.store.append_event(SessionEvent(self.session.session_id, 'unknown_context_change', {}))
        with self.assertRaisesRegex(ValueError, 'Unsupported context event'):
            self.store.resume_session(self.session.session_id)
