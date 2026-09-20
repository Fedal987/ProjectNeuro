"""Versioned persistence values, independent of the agent and UI."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionEvent:
    session_id: str
    type: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=now)
    version: int = 1
    seq: int = 0

    def __post_init__(self):
        UUID(self.session_id)
        UUID(self.event_id)
        if self.version != 1:
            raise ValueError(f"Unsupported event version: {self.version}")
        if not self.type or not isinstance(self.payload, dict):
            raise ValueError("Invalid event type or payload")
        if type(self.seq) is not int or self.seq < 0:
            raise ValueError("Invalid event sequence")
        datetime.fromisoformat(self.timestamp)
        p = self.payload
        if self.type in {"session_start", "checkpoint", "context_compaction", "context_reset"}:
            if self.type != "session_start" and "messages" not in p:
                raise ValueError("Recovery boundaries require complete messages")
            if not isinstance(p.get("messages", []), list) or not all(
                isinstance(m, dict) and m.get("role") in {"system", "user", "assistant", "tool"}
                for m in p.get("messages", [])
            ):
                raise ValueError("Invalid context messages")
            if not isinstance(p.get("input_history", []), list) or not all(
                isinstance(v, str) for v in p.get("input_history", [])
            ):
                raise ValueError("Invalid input history")
        if self.type == "session_start":
            if not all(isinstance(p.get(key), str) and p[key] for key in ("title", "workspace")):
                raise ValueError("Session start requires title and workspace")
            if p.get("model") is not None and not isinstance(p["model"], str):
                raise ValueError("Invalid model")
            if "created_at" in p:
                datetime.fromisoformat(p["created_at"])
        roles = {"user_message": "user", "assistant_message": "assistant", "tool_result": "tool", "system_message": "system"}
        if self.type in roles:
            if not isinstance(p.get("message"), dict) or p["message"].get("role") != roles[self.type]:
                raise ValueError("Event message role mismatch")
        if self.type == "input_submitted" and not isinstance(p.get("text"), str):
            raise ValueError("Input must be text")
        if self.type == "token_usage":
            values = [p.get(k, 0) for k in ("prompt_tokens", "input_tokens", "completion_tokens", "output_tokens", "prompt_cache_hit_tokens")]
            details = p.get("prompt_tokens_details") or {}
            if not isinstance(details, dict):
                raise ValueError("Invalid token details")
            values.append(details.get("cached_tokens", 0))
            if any(v is not None and (type(v) is not int or not 0 <= v <= 2**63 - 1) for v in values):
                raise ValueError("Invalid token count")
        if self.type == "session_metadata_updated":
            for key in ("title", "workspace"):
                if key in p and (not isinstance(p[key], str) or not p[key]):
                    raise ValueError(f"Invalid {key}")
            for key in ("model", "reasoning_effort"):
                if key in p and p[key] is not None and not isinstance(p[key], str):
                    raise ValueError(f"Invalid {key}")
            for key in ("archived", "deleted", "auto_name_pending"):
                if key in p and type(p[key]) is not bool:
                    raise ValueError(f"Invalid {key}")


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    title: str
    workspace: str
    model: str | None
    created_at: str
    updated_at: str
    jsonl_path: str
    archived: bool = False
    deleted: bool = False
    last_event_at: str = ""
    last_event_id: str = ""
    last_seq: int = 0
    indexed_offset: int = 0
    message_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    parent_session_id: str | None = None
    parent_event_id: str | None = None


@dataclass(frozen=True)
class AppendResult:
    event: SessionEvent
    index_synced: bool = True


@dataclass
class ResumeState:
    metadata: SessionMetadata
    messages: list[dict[str, Any]] = field(default_factory=list)
    input_history: list[str] = field(default_factory=list)
    auto_name_pending: bool = False
    reasoning_effort: str | None = None
    checkpoint_seq: int = 0
    thinking: bool | None = None
    reasoning_enabled: bool | None = None
