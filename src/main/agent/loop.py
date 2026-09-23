from __future__ import annotations

from typing import Any

from src.main.ui.i18n import tr
from src.main.api.exceptions import ProviderError
from .context import StreamEvent
from .exceptions import ConversationInterrupted


class AgentLoop:
    def run(self, user_input: str | None = None) -> str:
        self._cancel_event.clear()
        self._append_pending_user_messages()
        if user_input:
            self.add_user_message(user_input)
        if not any(message["role"] == "user" for message in self.messages):
            raise ValueError("缺少用户消息")

        self._read_paths.clear()
        self._last_failed_call = None
        completed_answers: list[str] = []
        for _ in range(self.max_steps):
            try:
                message = self._request_completion()
            except ConversationInterrupted:
                self._append_pending_user_messages()
                return tr("conversation_interrupted")
            except ProviderError as exc:
                return self._record_error(tr("agent_api_error", error=exc))
            assistant_message = {
                "role": "assistant",
                "content": message.get("content") or "",
            }
            if message.get("reasoning_content") is not None:
                assistant_message["reasoning_content"] = message["reasoning_content"]
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self._append_message(assistant_message)

            if tool_calls:
                for tool_call in tool_calls:
                    if self._cancel_event.is_set():
                        return tr("conversation_interrupted")
                    self._emit_event("tool_call", {"tool_call_id": tool_call.get("id"), "call": tool_call})
                    result = self._execute_tool_call(tool_call)
                    self._append_message(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", "unknown"),
                            "content": result,
                        }
                    )

            pending = self._append_pending_user_messages()
            if pending:
                if not tool_calls and assistant_message["content"]:
                    completed_answers.append(assistant_message["content"])
                continue
            if not tool_calls:
                completed_answers.append(assistant_message["content"])
                return "\n\n".join(filter(None, completed_answers))

        limit_message = (
            f"已达到最大执行步数 {self.max_steps}。请总结已完成的工作、验证结果和仍未解决的问题，"
            "不要继续调用工具。"
        )
        self._append_message({"role": "user", "content": limit_message})
        try:
            message = self._request_completion(use_tools=False)
        except ConversationInterrupted:
            self._append_pending_user_messages()
            return tr("conversation_interrupted")
        except ProviderError as exc:
            return self._record_error(tr("agent_api_error", error=exc))
        content = message.get("content") or limit_message
        self._append_message({"role": "assistant", "content": content})
        return content

    def run_stream(self, user_input: str | None = None):
        for event in self.run_stream_events(user_input):
            if event.kind in {"content", "error", "interrupted"}:
                yield event.content
            elif event.kind == "queued_user":
                yield f"\n\n**{tr('user_prompt').strip()}** {event.content}\n\n"

    def run_stream_events(self, user_input: str | None = None):
        self._cancel_event.clear()
        self._append_pending_user_messages()
        if user_input:
            self.add_user_message(user_input)
        if not any(message["role"] == "user" for message in self.messages):
            raise ValueError("缺少用户消息")

        self._read_paths.clear()
        self._last_failed_call = None
        for _ in range(self.max_steps):
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_call_parts: dict[int, dict[str, Any]] = {}
            received_choice = False
            try:
                for chunk in self._request_completion_stream():
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    received_choice = True
                    delta = choices[0].get("delta") or {}
                    reasoning_content = delta.get("reasoning_content")
                    if reasoning_content:
                        reasoning_parts.append(reasoning_content)
                        yield StreamEvent("reasoning", reasoning_content)
                    content = delta.get("content")
                    if content:
                        content_parts.append(content)
                        yield StreamEvent("content", content)
                    self._merge_tool_call_deltas(tool_call_parts, delta.get("tool_calls") or [])
            except ConversationInterrupted:
                pending = self._append_pending_user_messages()
                for text in pending:
                    yield StreamEvent("queued_user", text)
                yield StreamEvent("interrupted", tr("conversation_interrupted"))
                return
            except ProviderError as exc:
                yield StreamEvent(
                    "error", self._record_error(tr("agent_api_error", error=exc))
                )
                return

            if not received_choice:
                yield StreamEvent(
                    "error",
                    self._record_error(tr("agent_api_no_choices")),
                )
                return

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(content_parts),
            }
            if reasoning_parts:
                assistant_message["reasoning_content"] = "".join(reasoning_parts)
            tool_calls = [tool_call_parts[index] for index in sorted(tool_call_parts)]
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self._append_message(assistant_message)

            if tool_calls:
                for tool_call in tool_calls:
                    if self._cancel_event.is_set():
                        yield StreamEvent("interrupted", tr("conversation_interrupted"))
                        return
                    description = self._describe_tool_call(tool_call)
                    yield StreamEvent("tool", description)
                    self._emit_event("tool_call", {"tool_call_id": tool_call.get("id"), "call": tool_call})
                    result = self._execute_tool_call(tool_call)
                    self._append_message(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", "unknown"),
                            "content": result,
                        }
                    )
                    failed = "执行失败" in result or "已阻止" in result
                    status = tr("tool_failed") if failed else tr("tool_completed")
                    yield StreamEvent("tool_result", f"{status}: {description}")

            pending = self._append_pending_user_messages()
            for text in pending:
                yield StreamEvent("queued_user", text)
            if pending:
                continue
            if not tool_calls:
                return

        limit_message = (
            f"已达到最大执行步数 {self.max_steps}。请总结已完成的工作、验证结果和仍未解决的问题，"
            "不要继续调用工具。"
        )
        self._append_message({"role": "user", "content": limit_message})
        try:
            content_parts = []
            for chunk in self._request_completion_stream(use_tools=False):
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("delta") or {}).get("content")
                if content:
                    content_parts.append(content)
                    yield StreamEvent("content", content)
        except ConversationInterrupted:
            pending = self._append_pending_user_messages()
            for text in pending:
                yield StreamEvent("queued_user", text)
            yield StreamEvent("interrupted", tr("conversation_interrupted"))
            return
        except ProviderError as exc:
            yield StreamEvent(
                "error", self._record_error(tr("agent_api_error", error=exc))
            )
            return
        content = "".join(content_parts) or limit_message
        self._append_message({"role": "assistant", "content": content})

    @staticmethod
    def _merge_tool_call_deltas(
        accumulated: dict[int, dict[str, Any]],
        deltas: list[dict[str, Any]],
    ) -> None:
        for position, delta in enumerate(deltas):
            index = int(delta.get("index", position))
            tool_call = accumulated.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if delta.get("id"):
                tool_call["id"] += delta["id"]
            if delta.get("type"):
                tool_call["type"] = delta["type"]
            function = delta.get("function") or {}
            if function.get("name"):
                tool_call["function"]["name"] += function["name"]
            if function.get("arguments"):
                tool_call["function"]["arguments"] += function["arguments"]
