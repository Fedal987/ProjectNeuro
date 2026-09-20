import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.main.session import LocalSessionStore, SessionEvent
from src.main.session.migration import import_legacy


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LocalSessionStore(self.root)
        self.meta = self.store.create_session(title="test", workspace=str(self.root), messages=[{"role": "system", "content": "system"}])
        self.sid = self.meta.session_id

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def append(self, kind="user_message", payload=None):
        return self.store.append_event(SessionEvent(self.sid, kind, payload or {"message": {"role": "user", "content": "hello"}})).event

    def test_append_only_and_resume(self):
        path = self.store.events.path(self.sid)
        prefix = path.read_bytes()
        self.append()
        self.assertTrue(path.read_bytes().startswith(prefix))
        self.assertEqual(len(self.store.resume_session(self.sid).messages), 2)
        self.assertEqual(self.store.get_session(self.sid).message_count, 1)

    def test_index_failure_commits_once_and_recovers(self):
        event = SessionEvent(self.sid, "user_message", {"message": {"role": "user", "content": "once"}})
        with patch.object(self.store.index, "apply", side_effect=sqlite3.OperationalError("disk full")):
            self.assertFalse(self.store.append_event(event).index_synced)
        self.assertTrue(self.store.append_event(event).index_synced)
        self.assertEqual(self.store.get_session(self.sid).message_count, 1)
        self.assertEqual(len(list(self.store.load_events(self.sid))), 2)

    def test_conflicting_event_id_rejected(self):
        event = self.append()
        from dataclasses import replace
        with self.assertRaises(ValueError):
            self.store.append_event(replace(event, payload={"message": {"role": "user", "content": "changed"}}))

    def test_tail_recovery_preserves_damaged_bytes(self):
        path = self.store.events.path(self.sid)
        with path.open("ab") as stream:
            stream.write(b'{"broken":')
        self.append()
        self.assertEqual(len(list(self.store.load_events(self.sid))), 2)
        self.assertEqual(next(path.parent.glob("*.damaged-*")).read_bytes(), b'{"broken":')

    def test_middle_corruption_is_not_skipped(self):
        self.append()
        path = self.store.events.path(self.sid)
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(lines[0] + b'bad json\n' + lines[1])
        errors = self.store.rebuild_index()
        self.assertTrue(errors)
        with self.assertRaises(ValueError):
            self.store.resume_session(self.sid)

    def test_checkpoint_uses_offset(self):
        self.append()
        checkpoint = self.append("checkpoint", {"messages": [{"role": "system", "content": "summary"}], "input_history": ["hello"]})
        self.append()
        with patch.object(self.store.events, "iter_events", wraps=self.store.events.iter_events) as read:
            state = self.store.resume_session(self.sid)
            self.assertGreater(read.call_args.args[1], 0)
        self.assertEqual(state.messages[0]["content"], "summary")
        self.assertEqual(state.input_history, ["hello"])
        self.assertEqual(self.store.index.checkpoint(self.sid)["event_id"], checkpoint.event_id)

    def test_fork_is_independent_and_rebuildable(self):
        boundary = self.append()
        self.append("assistant_message", {"message": {"role": "assistant", "content": "later"}})
        branch = self.store.fork_session(self.sid, at_event_id=boundary.event_id, title="branch")
        self.store.rename_session(branch.session_id, "renamed")
        self.store.archive_session(self.sid)
        self.assertEqual(self.store.rebuild_index(), [])
        self.assertEqual(len(self.store.resume_session(branch.session_id).messages), 2)
        self.assertEqual(self.store.get_session(branch.session_id).title, "renamed")
        self.assertTrue(self.store.get_session(self.sid).archived)

    def test_unresolved_tool_fork_rejected(self):
        event = self.append("assistant_message", {"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "call1"}]}})
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.store.fork_session(self.sid, at_event_id=event.event_id, title="bad")

    def test_legacy_import_is_idempotent_and_preserves_source(self):
        path = self.root / "old.session"
        data = {"name": "old", "messages": [{"role": "user", "content": "old input"}], "input_history": ["old input"], "created_at": "2026-01-01T00:00:00"}
        path.write_text(json.dumps(data))
        self.assertEqual(import_legacy(self.store, self.root), [])
        self.assertEqual(import_legacy(self.store, self.root), [])
        sessions = self.store.list_sessions(query="old")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(self.store.resume_session(sessions[0].session_id).input_history, ["old input"])
        self.assertEqual(json.loads(path.read_text()), data)

    def test_delete_is_preserved_after_rebuild(self):
        self.store.delete_session(self.sid)
        self.store.rebuild_index()
        self.assertEqual(self.store.list_sessions(), [])
        with self.assertRaises(ValueError):
            self.store.resume_session(self.sid)

    def test_orphan_log_recovered_on_restart(self):
        with self.store.index.connection:
            self.store.index.connection.execute("DELETE FROM event_offsets")
            self.store.index.connection.execute("DELETE FROM sessions")
        self.store.close()
        self.store = LocalSessionStore(self.root)
        self.assertEqual(self.store.get_session(self.sid).title, "test")

    def test_corrupt_sqlite_rebuilt_offline(self):
        self.append()
        self.store.close()
        (self.root / "index.sqlite3").write_bytes(b"not a database")
        self.assertEqual(LocalSessionStore.rebuild_from_disk(self.root), [])
        self.store = LocalSessionStore(self.root)
        self.assertEqual(len(self.store.resume_session(self.sid).messages), 2)
        self.assertTrue(list(self.root.glob("index-backup-*")))

    def test_offline_rebuild_refuses_live_store(self):
        with self.assertRaisesRegex(RuntimeError, "Close all sessions"):
            LocalSessionStore.rebuild_from_disk(self.root)

    def test_concurrent_store_writers_serialize_sequences(self):
        from concurrent.futures import ThreadPoolExecutor
        other = LocalSessionStore(self.root)
        try:
            def write(i):
                store = self.store if i % 2 else other
                return store.append_event(SessionEvent(self.sid, "input_submitted", {"text": str(i)}))
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(write, range(20)))
            events = list(self.store.load_events(self.sid))
            self.assertEqual([e.seq for e in events], list(range(1, 22)))
        finally:
            other.close()

    def test_invalid_payload_does_not_poison_log(self):
        before = self.store.events.path(self.sid).read_bytes()
        with self.assertRaises(ValueError):
            self.append("user_message", {"message": {"role": "tool"}})
        self.assertEqual(self.store.events.path(self.sid).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
