"""Rebuildable metadata and byte offsets; never stores message bodies."""
import sqlite3
from pathlib import Path
from .models import SessionMetadata

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
 session_id TEXT PRIMARY KEY, title TEXT NOT NULL, workspace TEXT NOT NULL,
 model TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 jsonl_path TEXT NOT NULL UNIQUE, archived INTEGER NOT NULL DEFAULT 0,
 deleted INTEGER NOT NULL DEFAULT 0, last_event_at TEXT NOT NULL DEFAULT '',
 last_event_id TEXT NOT NULL DEFAULT '', last_seq INTEGER NOT NULL DEFAULT 0,
 indexed_offset INTEGER NOT NULL DEFAULT 0, message_count INTEGER NOT NULL DEFAULT 0,
 input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 cached_tokens INTEGER NOT NULL DEFAULT 0, parent_session_id TEXT, parent_event_id TEXT
);
CREATE INDEX IF NOT EXISTS sessions_workspace_updated
 ON sessions(workspace, archived, deleted, updated_at DESC);
CREATE TABLE IF NOT EXISTS event_offsets (
 session_id TEXT NOT NULL, event_id TEXT NOT NULL, seq INTEGER NOT NULL,
 kind TEXT NOT NULL, byte_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
 PRIMARY KEY(session_id, event_id), UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS recovery_offsets ON event_offsets(session_id, kind, seq DESC);
"""


class SQLiteSessionIndex:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported session index version: {version}")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.executescript(SCHEMA)
            self.connection.execute("PRAGMA user_version=1")
        except Exception:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def get(self, session_id):
        row = self.connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return SessionMetadata(**dict(row)) if row else None

    def list(self, *, workspace=None, query=None, archived=False, limit=50):
        sql = "SELECT * FROM sessions WHERE deleted=0 AND archived=?"
        args = [int(archived)]
        if workspace is not None:
            sql += " AND workspace=?"
            args.append(str(Path(workspace).expanduser().resolve()))
        if query is not None:
            sql += " AND instr(lower(title), lower(?)) > 0"
            args.append(query)
        sql += " ORDER BY updated_at DESC, session_id LIMIT ?"
        args.append(-1 if limit is None else limit)
        return [SessionMetadata(**dict(row)) for row in self.connection.execute(sql, args)]

    def event(self, session_id, event_id):
        return self.connection.execute(
            "SELECT * FROM event_offsets WHERE session_id=? AND event_id=?", (session_id, event_id)
        ).fetchone()

    def checkpoint(self, session_id):
        return self.connection.execute(
            "SELECT * FROM event_offsets WHERE session_id=? AND kind IN ('checkpoint', 'context_compaction', 'context_reset') ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()

    def apply(self, event, start, end, path):
        p = event.payload
        with self.connection:
            current = self.get(event.session_id)
            if event.seq != (current.last_seq + 1 if current else 1):
                raise ValueError("Non-contiguous event sequence")
            if event.type == "session_start":
                self.connection.execute(
                    "INSERT INTO sessions(session_id,title,workspace,model,created_at,updated_at,jsonl_path,parent_session_id,parent_event_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    (event.session_id, p["title"], p["workspace"], p.get("model"),
                     p.get("created_at", event.timestamp), event.timestamp, str(path),
                     p.get("parent_session_id"), p.get("parent_event_id")),
                )
            elif current is None:
                raise ValueError("First event must be session_start")
            if event.type == "session_metadata_updated":
                for key in ("title", "workspace", "model", "archived", "deleted"):
                    if key in p:
                        self.connection.execute(f"UPDATE sessions SET {key}=? WHERE session_id=?", (p[key], event.session_id))
            if event.type == "session_deleted":
                self.connection.execute("UPDATE sessions SET deleted=1 WHERE session_id=?", (event.session_id,))
            if event.type in {"user_message", "assistant_message"}:
                self.connection.execute("UPDATE sessions SET message_count=message_count+1 WHERE session_id=?", (event.session_id,))
            if event.type == "session_start" and p.get("messages"):
                count = sum(m.get("role") in {"user", "assistant"} for m in p["messages"])
                self.connection.execute("UPDATE sessions SET message_count=? WHERE session_id=?", (count, event.session_id))
            if event.type == "token_usage":
                details = p.get("prompt_tokens_details") or {}
                self.connection.execute(
                    "UPDATE sessions SET input_tokens=input_tokens+?, output_tokens=output_tokens+?, cached_tokens=cached_tokens+? WHERE session_id=?",
                    (p.get("prompt_tokens", p.get("input_tokens", 0)) or 0,
                     p.get("completion_tokens", p.get("output_tokens", 0)) or 0,
                     details.get("cached_tokens", p.get("prompt_cache_hit_tokens", 0)) or 0, event.session_id),
                )
            self.connection.execute(
                "UPDATE sessions SET updated_at=?,last_event_at=?,last_event_id=?,last_seq=?,indexed_offset=? WHERE session_id=?",
                (event.timestamp, event.timestamp, event.event_id, event.seq, end, event.session_id),
            )
            self.connection.execute("INSERT INTO event_offsets VALUES(?,?,?,?,?,?)", (event.session_id, event.event_id, event.seq, event.type, start, end))
