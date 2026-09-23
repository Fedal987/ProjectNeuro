from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import requests

from src.main.ui.i18n import tr
from src.main.tools.approval import ApprovalPolicy
from src.main.tools.base import ToolError
from src.main.tools.dispatcher import ToolDispatcher
from src.main.tools.registry import build_tool_definitions, build_tool_handlers
from .context import AgentContext
from .exceptions import ConversationInterrupted
from .loop import AgentLoop

if TYPE_CHECKING:
    from src.main.api.usage import UsageTracker

BASE_URL = ""
MODEL = ""
MAX_STEPS = 12
REASONING_MODE = ""


class Agent(AgentContext, AgentLoop):
    def __init__(
        self,
        api_key: str,
        workspace: Path,
        system_prompt: str,
        model: str = MODEL,
        base_url: str = BASE_URL,
        thinking: bool = True,
        reasoning_effort: str = REASONING_MODE,
        auto_approve: bool = False,
        max_steps: int = MAX_STEPS,
        temperature: float = 0.2,
        command_timeout: int = 60,
        confirm: Callable[[str], bool] | None = None,
        usage_tracker: UsageTracker | None = None,
    ):
        self.api_key = api_key
        self.workspace = Path(workspace).expanduser().resolve()
        self.system_prompt = system_prompt
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.auto_approve = auto_approve
        self.max_steps = max(1, int(max_steps))
        self.temperature = temperature
        self.command_timeout = max(1, int(command_timeout))
        self.approval = ApprovalPolicy(self)
        self.confirm = confirm or self.approval._terminal_confirm
        self.usage_tracker = usage_tracker

        if not self.workspace.is_dir():
            raise ValueError(tr("workspace_invalid", path=self.workspace))
        if not self.system_prompt.strip():
            raise ValueError("system_prompt 不能为空")
        if not self.base_url:
            raise ValueError(tr("base_url_empty"))
        if not self.model:
            raise ValueError(tr("model_empty"))

        self._initialize_context()
        self.session = requests.Session()
        self.session.headers.update({
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        })

        self.tool_handlers = build_tool_handlers(self)
        self.tools = build_tool_definitions()
        self.dispatcher = ToolDispatcher(self)

    def _request_completion(self, use_tools: bool = True) -> dict[str, Any]:
        if self._cancel_event.is_set():
            raise ConversationInterrupted
        payload = self._build_payload(use_tools=use_tools, stream=False)

        endpoint = self._completion_endpoint()
        response = None
        try:
            response = self.session.post(
                endpoint, json=payload, stream=True, timeout=120
            )
            self._set_active_response(response)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            if self._cancel_event.is_set():
                raise ConversationInterrupted from exc
            detail = ""
            if exc.response is not None:
                detail = f": {exc.response.text[:2000]}"
            raise ToolError(tr("model_api_request_failed", error=exc, detail=detail)) from exc
        except ValueError as exc:
            raise ToolError(tr("model_api_invalid_json")) from exc
        finally:
            self._clear_active_response(response)
            if response is not None:
                response.close()

        if self._cancel_event.is_set():
            raise ConversationInterrupted

        self._record_usage(data.get("usage"))
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise ToolError(tr("model_api_missing_message", data=str(data)[:2000]))
        return choices[0]["message"]

    def _request_completion_stream(self, use_tools: bool = True):
        if self._cancel_event.is_set():
            raise ConversationInterrupted
        payload = self._build_payload(use_tools=use_tools, stream=True)
        response = None
        try:
            response = self.session.post(
                self._completion_endpoint(),
                json=payload,
                stream=True,
                timeout=120,
            )
            self._set_active_response(response)
            response.raise_for_status()
            response.encoding = "utf-8"
            for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if self._cancel_event.is_set():
                    raise ConversationInterrupted
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ToolError(tr("model_api_invalid_sse", data=data[:500])) from exc
                if isinstance(chunk, dict):
                    if chunk.get("error"):
                        raise ToolError(
                            tr("model_api_returned_error", error=str(chunk["error"])[:2000])
                        )
                    self._record_usage(chunk.get("usage"))
                    yield chunk
            if self._cancel_event.is_set():
                raise ConversationInterrupted
        except requests.RequestException as exc:
            if self._cancel_event.is_set():
                raise ConversationInterrupted from exc
            detail = ""
            if exc.response is not None:
                detail = f": {exc.response.text[:2000]}"
            raise ToolError(
                tr("model_api_stream_failed", error=exc, detail=detail)
            ) from exc
        finally:
            self._clear_active_response(response)
            if response is not None:
                response.close()

    def _build_payload(self, use_tools: bool, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "stream": stream,
            "temperature": self.temperature,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if use_tools:
            payload["tools"] = self.tools
            payload["tool_choice"] = "auto"
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if "api.deepseek.com" in self.base_url:
            payload["thinking"] = {"type": "enabled" if self.thinking else "disabled"}
        elif "siliconflow" in self.base_url:
            payload["enable_thinking"] = self.thinking
        return payload

    def _completion_endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def _execute_tool_call(self, tool_call: dict[str, Any]) -> str:
        return self.dispatcher._execute_tool_call(tool_call)

    @staticmethod
    def _describe_tool_call(tool_call: dict[str, Any]) -> str:
        return ToolDispatcher._describe_tool_call(tool_call)
