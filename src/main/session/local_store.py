"""Local source-of-truth event log with a disposable SQLite projection.

A store-wide advisory lock serializes processes as well as rebuilds. This favors
simple, predictable local durability over concurrent writers to many sessions.
"""
import copy
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from .jsonl_store import JsonlEventStore
from .locking import lock_file, unlock_file
from .models import AppendResult, ResumeState, SessionEvent, SessionMetadata
from .sqlite_index import SQLiteSessionIndex


class LocalSessionStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events = JsonlEventStore(self.directory / "rollouts")
        self._mutex = RLock()
        self.errors: list[str] = []
        self._lifetime_lock = (self.directory / ".lifetime.lock").open("a+b")
        lock_file(self._lifetime_lock, shared=True)
        try:
            self._open_index()
        except Exception:
            unlock_file(self._lifetime_lock)
            self._lifetime_lock.close()
            raise

    def _open_index(self):
        with self._locked():
            # Do not silently replace a corrupt database; the explicit rebuild
            # command can repair it while retaining a diagnostic copy.
            self.index = SQLiteSessionIndex(self.directory / "index.sqlite3")
            self._reconcile_all()

    @contextmanager
    def _locked(self):
        with self._mutex:
            with (self.directory / ".store.lock").open("a+b") as lock:
                lock_file(lock)
                try:
                    yield
                finally:
                    unlock_file(lock)

    def close(self):
        with self._locked():
            self.index.close()
            if not self._lifetime_lock.closed:
                unlock_file(self._lifetime_lock)
                self._lifetime_lock.close()

    def _sync(self, session_id):
        self.events.repair_tail(session_id)
        current = self.index.get(session_id)
        offset = current.indexed_offset if current else 0
        if self.events.path(session_id).stat().st_size < offset:
            raise ValueError(f"Rollout shorter than indexed history: {session_id}")
        for event, start, end in self.events.iter_records(session_id, offset):
            self.index.apply(event, start, end, self.events.path(session_id))

    def _reconcile_all(self):
        for path in sorted(self.events.directory.glob("*.jsonl")):
            try:
                self._sync(path.stem)
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                self.errors.append(f"{path.name}: {exc}")
        rows = self.index.connection.execute("SELECT session_id FROM sessions").fetchall()
        for row in rows:
            if not self.events.path(row[0]).is_file():
                self.errors.append(f"Missing rollout: {row[0]}")

    def create_session(self, *, workspace: str, title: str, model: str | None = None,
                       session_id: str | None = None, **initial: Any) -> SessionMetadata:
        session_id = session_id or str(uuid4())
        payload = dict(initial, workspace=str(Path(workspace).expanduser().resolve()), title=title, model=model)
        event = SessionEvent(session_id, "session_start", payload, seq=1)
        with self._locked():
            if self.events.path(session_id).exists():
                self._sync(session_id)
                original = next(self.events.iter_events(session_id))
                if original.payload != payload:
                    raise ValueError("Session ID already exists with different initial state")
                return self.index.get(session_id)
            start, end = self.events.append(event, create=True)
            try:
                self.index.apply(event, start, end, self.events.path(session_id))
            except sqlite3.Error as exc:
                self.errors.append(f"Created {session_id}; index pending: {exc}")
                # Metadata is derivable without making the caller retry creation.
                return SessionMetadata(session_id, title, payload["workspace"], model,
                                       payload.get("created_at", event.timestamp), event.timestamp,
                                       str(self.events.path(session_id)), last_seq=1,
                                       last_event_id=event.event_id, last_event_at=event.timestamp)
            return self.index.get(session_id)

    def append_event(self, event: SessionEvent) -> AppendResult:
        with self._locked():
            self._sync(event.session_id)
            current = self.index.get(event.session_id)
            if current is None:
                raise KeyError(event.session_id)
            previous = self.index.event(event.session_id, event.event_id)
            if previous:
                saved = next(self.events.iter_events(event.session_id, previous["byte_offset"]))
                if replace(event, seq=saved.seq) != saved:
                    raise ValueError("Event ID reused with different content")
                return AppendResult(saved)
            if current.deleted:
                raise ValueError("Session has been deleted")
            if event.type == "session_start":
                raise ValueError("Session already started")
            if event.seq not in (0, current.last_seq + 1):
                raise ValueError("Unexpected sequence")
            committed = replace(event, seq=current.last_seq + 1)
            start, end = self.events.append(committed)
            try:
                self.index.apply(committed, start, end, self.events.path(event.session_id))
            except sqlite3.Error as exc:
                self.errors.append(f"Event {event.event_id} committed; index pending: {exc}")
                return AppendResult(committed, index_synced=False)
            return AppendResult(committed)

    def get_session(self, session_id: str) -> SessionMetadata:
        with self._locked():
            self._sync(session_id)
            value = self.index.get(session_id)
            if value is None:
                raise KeyError(session_id)
            return value

    def list_sessions(self, *, workspace: str | None = None, query: str | None = None,
                      archived: bool = False, limit: int | None = 50) -> list[SessionMetadata]:
        with self._locked():
            return self.index.list(workspace=workspace, query=query, archived=archived, limit=limit)

    def load_events(self, session_id: str, *, after_event_id: str | None = None) -> Iterator[SessionEvent]:
        # Capture a committed prefix, then release the writer lock before yielding.
        with self._locked():
            self._sync(session_id)
            offset = 0
            if after_event_id:
                row = self.index.event(session_id, after_event_id)
                if row is None:
                    raise KeyError(after_event_id)
                offset = row["end_offset"]
            end_offset = self.index.get(session_id).indexed_offset
        yield from self.events.iter_events(session_id, offset, end_offset)

    def rename_session(self, session_id: str, title: str) -> None:
        self.append_event(SessionEvent(session_id, "session_metadata_updated", {"title": title}))

    def archive_session(self, session_id: str, archived: bool = True) -> None:
        self.append_event(SessionEvent(session_id, "session_metadata_updated", {"archived": archived}))

    def delete_session(self, session_id: str) -> None:
        """Logical deletion; retains history and fork provenance for recovery."""
        self.append_event(SessionEvent(session_id, "session_deleted", {}))

    @staticmethod
    def _reduce(state, event):
        p = event.payload
        if event.type in {"session_start", "checkpoint", "context_compaction", "context_reset"}:
            state.checkpoint_seq = event.seq
            state.messages = copy.deepcopy(p.get("messages", []))
            state.input_history = list(p.get("input_history", []))
            state.auto_name_pending = p.get("auto_name_pending", False)
            state.reasoning_effort = p.get("reasoning_effort")
            state.thinking = p.get("thinking")
            state.reasoning_enabled = p.get("reasoning_enabled")
        elif event.type in {"user_message", "assistant_message", "tool_result", "system_message"}:
            state.messages.append(copy.deepcopy(p["message"]))
        elif event.type == "input_submitted":
            state.input_history.append(p["text"])
        elif event.type == "session_metadata_updated":
            if "auto_name_pending" in p:
                state.auto_name_pending = p["auto_name_pending"]
            for key in ("reasoning_effort", "thinking", "reasoning_enabled"):
                if key in p:
                    setattr(state, key, p[key])
        elif event.type not in {
            "tool_call", "token_usage", "response_metadata", "context_summary",
            "session_end", "session_deleted",
        }:
            raise ValueError(f"Unsupported context event: {event.type}")

    def resume_session(self, session_id: str) -> ResumeState:
        with self._locked():
            self._sync(session_id)
            metadata = self.index.get(session_id)
            if metadata.deleted:
                raise ValueError("Session has been deleted")
            point = self.index.checkpoint(session_id)
            offset = point["byte_offset"] if point else 0
            state = ResumeState(metadata)
            for event in self.events.iter_events(session_id, offset):
                self._reduce(state, event)
            return state

    def fork_session(self, session_id: str, *, at_event_id: str, title: str) -> SessionMetadata:
        # Full replay up to the selected event is only needed for arbitrary historical forks.
        metadata = self.get_session(session_id)
        state = ResumeState(metadata)
        found = False
        model = None
        for event in self.load_events(session_id):
            self._reduce(state, event)
            if event.type in {"session_start", "session_metadata_updated"}:
                model = event.payload.get("model", model)
            if event.event_id == at_event_id:
                found = True
                break
        if not found:
            raise KeyError(at_event_id)
        pending = set()
        for message in state.messages:
            if message.get("role") == "assistant":
                pending.update(call["id"] for call in message.get("tool_calls", []))
            elif message.get("role") == "tool":
                pending.discard(message.get("tool_call_id"))
        if pending:
            raise ValueError("Cannot fork with unresolved tool calls")
        return self.create_session(workspace=metadata.workspace, title=title, model=model,
                                   parent_session_id=session_id, parent_event_id=at_event_id,
                                   messages=state.messages, input_history=state.input_history,
                                   reasoning_effort=state.reasoning_effort, thinking=state.thinking,
                                   reasoning_enabled=state.reasoning_enabled, auto_name_pending=False)

    def rebuild_index(self):
        """Rebuild in place under the writer lock; never swaps a live WAL database."""
        with self._locked():
            with self.index.connection:
                self.index.connection.execute("DELETE FROM event_offsets")
                self.index.connection.execute("DELETE FROM sessions")
            self.errors.clear()
            self._reconcile_all()
            return list(self.errors)

    @classmethod
    def rebuild_from_disk(cls, directory):
        """Offline repair, including corrupt SQLite. Refuse while a store is open.

        Keep the previous database and WAL sidecars in a diagnostic directory.
        No live connection is ever redirected to a replacement database.
        """
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".lifetime.lock").open("a+b") as lifetime:
            try:
                lock_file(lifetime, blocking=False)
            except OSError as exc:
                raise RuntimeError("Close all sessions using this directory before rebuilding") from exc
            try:
                with (root / ".store.lock").open("a+b") as writer:
                    lock_file(writer)
                    try:
                        backup = root / f"index-backup-{uuid4()}"
                        backup.mkdir()
                        for name in ("index.sqlite3", "index.sqlite3-wal", "index.sqlite3-shm"):
                            path = root / name
                            if path.exists():
                                path.replace(backup / name)
                        # Construct the projection directly while the exclusive lifetime
                        # lock prevents every runtime from opening a connection.
                        instance = object.__new__(cls)
                        instance.directory = root
                        instance.events = JsonlEventStore(root / "rollouts")
                        instance.errors = []
                        instance.index = SQLiteSessionIndex(root / "index.sqlite3")
                        try:
                            instance._reconcile_all()
                        finally:
                            instance.index.close()
                        return instance.errors
                    finally:
                        unlock_file(writer)
            finally:
                unlock_file(lifetime)
