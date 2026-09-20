"""Idempotent, non-destructive import of legacy snapshot files."""
import json
from uuid import NAMESPACE_URL, uuid5
from .models import now


def import_legacy(store, directory):
    errors = []
    for path in sorted(directory.glob("*.session")):
        try:
            # Identity follows source path, not mutable snapshot contents.
            session_id = str(uuid5(NAMESPACE_URL, path.resolve().as_uri()))
            if store.events.path(session_id).exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            history = data.get("input_history", [])
            if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
                raise ValueError("Invalid legacy messages")
            if not isinstance(history, list) or not all(isinstance(m, str) for m in history):
                raise ValueError("Invalid legacy input history")
            store.create_session(
                session_id=session_id, title=data["name"], workspace=data.get("workspace") or str(directory.parent),
                messages=messages, input_history=history,
                created_at=data.get("created_at") or now(),
                auto_name_pending=data.get("auto_name_pending", data["name"] == "default" or data["name"].startswith("session-")),
                legacy_source=str(path),
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"{path.name}: {exc}")
    return errors
