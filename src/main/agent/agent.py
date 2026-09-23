from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.main.api.provider import ModelProvider
from src.main.api.exceptions import ProviderInterrupted

from src.main.ui.i18n import tr
from src.main.tools.approval import ApprovalPolicy
from src.main.tools.dispatcher import ToolDispatcher
from src.main.tools.registry import build_tool_definitions, build_tool_handlers
from .context import AgentContext
from .exceptions import ConversationInterrupted
from .loop import AgentLoop

MODEL = ""
MAX_STEPS = 12
REASONING_MODE = ""


class Agent(AgentContext, AgentLoop):
    def __init__(
        self,
        provider: ModelProvider,
        workspace: Path,
        system_prompt: str,
        model: str = MODEL,
        thinking: bool = True,
        reasoning_effort: str = REASONING_MODE,
        auto_approve: bool = False,
        max_steps: int = MAX_STEPS,
        temperature: float | None = None,
        command_timeout: int = 60,
        confirm: Callable[[str], bool] | None = None,
    ):
        self.provider = provider
        self.workspace = Path(workspace).expanduser().resolve()
        self.system_prompt = system_prompt
        self.model = model
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.auto_approve = auto_approve
        self.max_steps = max(1, int(max_steps))
        self.temperature = temperature
        self.command_timeout = max(1, int(command_timeout))
        self.approval = ApprovalPolicy(self)
        self.confirm = confirm or self.approval._terminal_confirm

        if not self.workspace.is_dir():
            raise ValueError(tr("workspace_invalid", path=self.workspace))
        if not self.system_prompt.strip():
            raise ValueError("system_prompt 不能为空")
        if not self.model:
            raise ValueError(tr("model_empty"))

        self._initialize_context()
        self.tool_handlers = build_tool_handlers(self)
        self.tools = build_tool_definitions()
        self.dispatcher = ToolDispatcher(self)

    def _request_options(self, use_tools: bool) -> dict[str, Any]:
        return dict(model=self.model, temperature=self.temperature,
                    tools=self.tools if use_tools else None, thinking=self.thinking,
                    reasoning_effort=self.reasoning_effort, request_context=self._request_context)

    def _request_completion(self, use_tools: bool = True) -> dict[str, Any]:
        if self._cancel_event.is_set():
            raise ConversationInterrupted
        try:
            return self.provider.complete(self.messages, **self._request_options(use_tools))
        except ProviderInterrupted as exc:
            raise ConversationInterrupted from exc

    def _request_completion_stream(self, use_tools: bool = True):
        if self._cancel_event.is_set():
            raise ConversationInterrupted
        try:
            yield from self.provider.stream(self.messages, **self._request_options(use_tools))
        except ProviderInterrupted as exc:
            raise ConversationInterrupted from exc

    def _execute_tool_call(self, tool_call: dict[str, Any]) -> str:
        return self.dispatcher._execute_tool_call(tool_call)

    @staticmethod
    def _describe_tool_call(tool_call: dict[str, Any]) -> str:
        return ToolDispatcher._describe_tool_call(tool_call)
