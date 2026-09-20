"""Durable JSONL operations. The caller holds the store's writer lock."""
import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4
from .models import SessionEvent


class JsonlEventStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.sync_directory(directory.parent)
        self.sync_directory(directory.parent.parent)

    def path(self, session_id: str) -> Path:
        if str(UUID(session_id)) != session_id:
            raise ValueError("Session ID must be a canonical UUID")
        return self.directory / f"{session_id}.jsonl"

    def sync_directory(self, directory: Path | None = None):
        if os.name == "nt":
            # Windows does not expose directory fsync through os.open.
            return
        fd = os.open(directory or self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def append(self, event: SessionEvent, *, create: bool = False) -> tuple[int, int]:
        # Serialize before opening: an invalid payload must not create an empty file.
        data = (json.dumps(asdict(event), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        path = self.path(event.session_id)
        with path.open("xb" if create else "ab") as stream:
            start = stream.tell()
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            end = stream.tell()
        if create:
            self.sync_directory()
        return start, end

    def repair_tail(self, session_id: str):
        path = self.path(session_id)
        with path.open("rb+") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            if not size:
                raise ValueError(f"Empty rollout: {path}")
            stream.seek(size - 1)
            if stream.read(1) == b"\n":
                return
            # Only an unterminated final line is uncommitted. Never skip middle corruption.
            end = size
            cut = 0
            while end:
                start = max(0, end - 65536)
                stream.seek(start)
                block = stream.read(end - start)
                found = block.rfind(b"\n")
                if found >= 0:
                    cut = start + found + 1
                    break
                end = start
            stream.seek(cut)
            tail = stream.read()
            backup = path.with_suffix(f".damaged-{uuid4()}")
            with backup.open("xb") as saved:
                saved.write(tail)
                saved.flush()
                os.fsync(saved.fileno())
            self.sync_directory()
            stream.truncate(cut)
            stream.flush()
            os.fsync(stream.fileno())
            if not cut:
                raise ValueError(f"Incomplete session_start: {path}; preserved in {backup}")

    def iter_records(self, session_id: str, offset: int = 0, end_offset: int | None = None):
        with self.path(session_id).open("rb") as stream:
            stream.seek(offset)
            while True:
                start = stream.tell()
                if end_offset is not None and start >= end_offset:
                    break
                line = stream.readline(-1 if end_offset is None else end_offset - start)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    raise ValueError(f"Incomplete JSONL tail at {start}")
                event = SessionEvent(**json.loads(line))
                if event.session_id != session_id:
                    raise ValueError(f"Session ID mismatch at {start}")
                yield event, start, stream.tell()

    def iter_events(self, session_id: str, offset: int = 0, end_offset: int | None = None):
        for event, _, _ in self.iter_records(session_id, offset, end_offset):
            yield event
