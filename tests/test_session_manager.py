import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.main.msg.session_manager import SessionManager


class FakeHandler:
    def __init__(self):
        self.history = [{"role": "system", "content": "system"}]
        self.agent = SimpleNamespace(model="test-model", reasoning_effort="low", event_sink=None)

    def reset(self):
        self.history = [{"role": "system", "content": "system"}]

    def message(self, role, content):
        message = {"role": role, "content": content}
        if self.agent.event_sink:
            self.agent.event_sink(f"{role}_message", {"message": message})
        self.history.append(message)


class SessionManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = self.make_manager()

    def make_manager(self):
        return SessionManager(session_factory=FakeHandler, storage_root=self.root,
                              workspace_provider=lambda: self.root,
                              name_generator=lambda messages: "generated")

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def test_temporary_does_not_persist(self):
        self.manager.save_current_session()
        self.assertEqual(self.manager.store.list_sessions(), [])

    def test_restart_lazy_loading_and_no_duplicate_messages(self):
        self.manager.append_input_history("hello")
        self.manager.current_handler.message("user", "hello")
        self.manager.current_handler.message("assistant", "world")
        self.manager.save_current_session()
        sid = self.manager.current_session.session_id
        self.assertEqual(self.manager.current_name, "generated")
        self.manager.close()
        self.manager = self.make_manager()
        session = next(s for s in self.manager.list_sessions() if s.session_id == sid)
        self.assertIsNone(session.handler)
        self.manager.switch_session("generated")
        self.assertEqual(len(self.manager.current_handler.history), 3)
        self.assertEqual(self.manager.current_session.input_history, ["hello"])
        self.assertEqual(self.manager.current_handler.agent.workspace, self.root)
        self.assertEqual(self.manager.store.get_session(sid).message_count, 2)

    def test_reset_preserves_event_history(self):
        self.manager.activate_current_session()
        self.manager.current_handler.message("user", "hello")
        sid = self.manager.current_session.session_id
        self.manager.reset_current_session()
        state = self.manager.store.resume_session(sid)
        self.assertEqual(len(state.messages), 1)
        self.assertIn("user_message", [e.type for e in self.manager.store.load_events(sid)])

    def test_model_settings_survive_resume(self):
        self.manager.create_session("named")
        self.manager.current_handler.agent.model = "other-model"
        self.manager.current_handler.agent.reasoning_effort = "high"
        self.manager.current_handler.agent.thinking = True
        self.manager.current_handler.reasoning_enabled = True
        self.manager.save_current_session()
        self.manager.close()
        self.manager = self.make_manager()
        self.manager.switch_session("named")
        self.assertEqual(self.manager.current_handler.agent.model, "other-model")
        self.assertEqual(self.manager.current_handler.agent.reasoning_effort, "high")
        self.assertTrue(self.manager.current_handler.agent.thinking)
        self.assertTrue(self.manager.current_handler.reasoning_enabled)

    def test_checkpoint_threshold_survives_restarts(self):
        self.manager.create_session("long")
        sid = self.manager.current_session.session_id
        for i in range(60):
            self.manager.current_handler.message("user", str(i))
        self.manager.close()
        self.manager = self.make_manager()
        self.manager.switch_session("long")
        for i in range(40):
            self.manager.current_handler.message("user", str(i))
        self.manager.save_current_session()
        self.assertIsNotNone(self.manager.store.index.checkpoint(sid))

    def test_stale_runtime_cannot_overwrite_other_writer_context(self):
        from src.main.session import SessionEvent
        self.manager.create_session("shared")
        sid = self.manager.current_session.session_id
        self.manager.store.append_event(SessionEvent(sid, "user_message", {
            "message": {"role": "user", "content": "another writer"}
        }))
        with self.assertRaisesRegex(ValueError, "sequence"):
            self.manager.current_handler.message("user", "stale")
        with self.assertRaises(ValueError):
            self.manager.close()
        self.manager = self.make_manager()
        self.manager.switch_session("shared")
        self.assertEqual(self.manager.current_handler.history[-1]["content"], "another writer")


if __name__ == "__main__":
    unittest.main()
