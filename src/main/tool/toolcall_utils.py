"""
    NeuroCode
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/neurocode-py
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Any, Callable

import requests
from src.main.ui.i18n import tr

if TYPE_CHECKING:
    from src.main.api.usage import UsageTracker

BASE_URL = ""
MODEL = ""
MAX_STEPS = 12
REASONING_MODE = ""


def get_current_path() -> str:
    return os.getcwd()


class ToolError(Exception):
    pass


class ConversationInterrupted(Exception):
    pass


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    content: str


class Agent:
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
        self.confirm = confirm or self._terminal_confirm
        self.usage_tracker = usage_tracker
        self.event_sink: Callable[[str, dict[str, Any]], None] | None = None
        self._read_paths: set[Path] = set()
        self._last_failed_call: str | None = None
        self._cancel_event = Event()
        self._response_lock = Lock()
        self._active_response: requests.Response | None = None
        self._interaction_paused: Callable[[], None] | None = None
        self._interaction_resumed: Callable[[], None] | None = None
        self._pending_user_messages: deque[str] = deque()
        self._pending_messages_lock = Lock()

        if not self.workspace.is_dir():
            raise ValueError(tr("workspace_invalid", path=self.workspace))
        if not self.system_prompt.strip():
            raise ValueError("system_prompt 不能为空")
        if not self.base_url:
            raise ValueError(tr("base_url_empty"))
        if not self.model:
            raise ValueError(tr("model_empty"))

        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self.system_prompt + f"\n\n当前工作目录: {self.workspace}"
            }
        ]
        self.session = requests.Session()
        self.session.headers.update({
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        })

        self.tool_handlers: dict[str, Callable[..., Any]] = {
            "list_directory": self._list_directory,
            "read_file": self._read_file,
            "search_files": self._search_files,
            "write_file": self._write_file,
            "replace_in_file": self._replace_in_file,
            "run_command": self._run_command,
        }
        self.tools = self._build_tool_definitions()

    def _build_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            self._tool(
                "list_directory",
                "List files and directories inside the workspace. Use this first when the project structure is unknown.",
                {
                    "path": {"type": "string", "description": "Workspace-relative directory path; use '.' for root."},
                    "depth": {"type": "integer", "description": "Recursion depth from 1 to 4.", "minimum": 1, "maximum": 4},
                },
                ["path"],
            ),
            self._tool(
                "read_file",
                "Read a UTF-8 text file. Existing files must be read before they can be modified.",
                {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                ["path"],
            ),
            self._tool(
                "search_files",
                "Search text in project files before guessing where a symbol or behavior is defined.",
                {
                    "query": {"type": "string", "description": "Literal text to search for."},
                    "path": {"type": "string", "description": "Workspace-relative file or directory; defaults to '.'."},
                },
                ["query"],
            ),
            self._tool(
                "write_file",
                "Create a text file or overwrite one that has already been read. Requires user approval unless auto-approve is enabled.",
                {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "content": {"type": "string", "description": "Complete new file content."},
                },
                ["path", "content"],
            ),
            self._tool(
                "replace_in_file",
                "Replace one exact, unique text block in a file that has already been read. Requires user approval unless auto-approve is enabled.",
                {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "old_content": {"type": "string", "description": "Exact existing text; it must occur exactly once."},
                    "new_content": {"type": "string", "description": "Replacement text."},
                },
                ["path", "old_content", "new_content"],
            ),
            self._tool(
                "run_command",
                "Run a non-interactive command in the workspace for inspection, tests, linting, builds, or internet research with curl. When external or current information is needed, use curl through this tool. Read-only low-risk commands run without approval; other commands require user approval. Shell operators are not supported.",
                {
                    "command": {"type": "string", "description": "Command line parsed without a shell."},
                },
                ["command"],
            ),
        ]

    @staticmethod
    def _tool(
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    def reset(self) -> None:
        self.messages = [
            {
                "role": "system",
                "content": self.system_prompt + f"\n\n当前工作目录: {self.workspace}",
            }
        ]
        self._read_paths.clear()
        self._last_failed_call = None

    def _emit_event(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(kind, payload)

    def _append_message(self, message: dict[str, Any]) -> None:
        kind = {"user": "user_message", "assistant": "assistant_message", "tool": "tool_result", "system": "system_message"}[message["role"]]
        self._emit_event(kind, {"message": message})
        self.messages.append(message)

    def add_user_message(self, text: str) -> None:
        self._append_message({"role": "user", "content": text})

    def queue_user_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._pending_messages_lock:
            self._pending_user_messages.append(text)

    def _append_pending_user_messages(self) -> tuple[str, ...]:
        with self._pending_messages_lock:
            pending = tuple(self._pending_user_messages)
            self._pending_user_messages.clear()
        for text in pending:
            self.add_user_message(text)
        return pending

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
            except ToolError as exc:
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
        except ToolError as exc:
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
            except ToolError as exc:
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
        except ToolError as exc:
            yield StreamEvent(
                "error", self._record_error(tr("agent_api_error", error=exc))
            )
            return
        content = "".join(content_parts) or limit_message
        self._append_message({"role": "assistant", "content": content})

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
            # SSE is UTF-8; requests may default text/event-stream to Latin-1.
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

    def _record_usage(self, usage: Any) -> None:
        if isinstance(usage, dict):
            self._emit_event("token_usage", usage)
        if self.usage_tracker is not None and isinstance(usage, dict):
            self.usage_tracker.record(usage)

    def interrupt(self) -> None:
        self._cancel_event.set()
        with self._response_lock:
            response = self._active_response
        if response is not None:
            response.close()

    def set_interaction_callbacks(
        self,
        paused: Callable[[], None] | None,
        resumed: Callable[[], None] | None,
    ) -> None:
        self._interaction_paused = paused
        self._interaction_resumed = resumed

    def _set_active_response(self, response: requests.Response) -> None:
        with self._response_lock:
            self._active_response = response

    def _clear_active_response(self, response: requests.Response | None) -> None:
        with self._response_lock:
            if self._active_response is response:
                self._active_response = None

    def _completion_endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

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

    def _execute_tool_call(self, tool_call: dict[str, Any]) -> str:
        function = tool_call.get("function") or {}
        name = function.get("name", "")
        signature = f"{name}:{function.get('arguments') or '{}'}"
        if signature == self._last_failed_call:
            return (
                f"工具 {name} 的相同调用刚刚已经失败，已阻止无变化的重复执行。"
                "请先分析错误、获取新信息或采用不同方案。"
            )
        handler = self.tool_handlers.get(name)
        if handler is None:
            self._last_failed_call = signature
            return f"工具执行失败: 未知工具 {name!r}"
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("工具参数必须是 JSON 对象")
            result = handler(**arguments)
            self._last_failed_call = None
            return self._truncate(str(result))
        except (TypeError, ValueError, ToolError, OSError) as exc:
            self._last_failed_call = signature
            return f"工具 {name} 执行失败: {exc}。请分析原因并调整下一步，不要原样重复失败操作。"

    @staticmethod
    def _describe_tool_call(tool_call: dict[str, Any]) -> str:
        function = tool_call.get("function") or {}
        name = function.get("name", tr("unknown_tool"))
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        labels = {
            "list_directory": tr("tool_list_directory"),
            "read_file": tr("tool_read_file"),
            "search_files": tr("tool_search_files"),
            "write_file": tr("tool_write_file"),
            "replace_in_file": tr("tool_replace_file"),
            "run_command": tr("tool_run_command"),
        }
        detail = (
            arguments.get("path")
            or arguments.get("query")
            or arguments.get("command")
            or ""
        )
        description = labels.get(name, name)
        return f"{description}: {detail}" if detail else description

    def _record_error(self, content: str) -> str:
        self._append_message({"role": "assistant", "content": content})
        return content

    def _resolve_path(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ToolError(f"路径超出工作目录: {path}") from exc
        return resolved

    def _list_directory(self, path: str = ".", depth: int = 2) -> str:
        target = self._resolve_path(path)
        depth = min(max(int(depth), 1), 4)
        if not target.is_dir():
            raise ToolError(f"目录不存在: {path}")

        lines: list[str] = []
        base_parts = len(target.parts)
        for root, dirs, files in os.walk(target):
            root_path = Path(root)
            level = len(root_path.parts) - base_parts
            dirs[:] = sorted(
                name for name in dirs
                if name not in {".git", ".idea", ".venv", "__pycache__", "node_modules"}
                and level < depth
            )
            if level >= depth:
                dirs[:] = []
            relative_root = root_path.relative_to(self.workspace)
            if level == 0:
                lines.append(f"{relative_root or Path('.')} /")
            for directory in dirs:
                relative = (relative_root / directory).as_posix()
                lines.append(f"{relative}/")
            for filename in sorted(files):
                relative = (relative_root / filename).as_posix()
                lines.append(relative)
            if len(lines) >= 1000:
                lines.append("... 结果过多，已截断")
                break
        return "\n".join(lines)

    def _read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        target = self._resolve_path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"文件不是可读取的 UTF-8 文本: {path}") from exc
        except OSError as exc:
            raise ToolError(f"读取文件失败: {exc}") from exc
        self._read_paths.add(target)
        lines = content.splitlines()
        start = max(int(start_line), 1)
        end = len(lines) if end_line is None else min(int(end_line), len(lines))
        if end < start:
            raise ToolError("end_line 不能小于 start_line")
        numbered = [f"{index}: {lines[index - 1]}" for index in range(start, end + 1)]
        return self._truncate("\n".join(numbered))

    def _search_files(self, query: str, path: str = ".") -> str:
        if not query:
            raise ToolError("搜索内容不能为空")
        target = self._resolve_path(path)
        if not target.exists():
            raise ToolError(f"搜索路径不存在: {path}")

        command = ["rg", "--line-number", "--fixed-strings", "--glob", "!.git/**", query, str(target)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                check=False,
            )
        except FileNotFoundError:
            return self._search_files_python(query, target)
        if completed.returncode == 1:
            return "未找到匹配内容。"
        if completed.returncode != 0:
            raise ToolError(completed.stderr.strip() or f"rg 退出码 {completed.returncode}")
        return self._truncate(completed.stdout)

    def _search_files_python(self, query: str, target: Path) -> str:
        files = [target] if target.is_file() else target.rglob("*")
        matches: list[str] = []
        for file_path in files:
            if not file_path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in file_path.parts):
                continue
            try:
                for line_number, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), 1):
                    if query in line:
                        relative = file_path.relative_to(self.workspace)
                        matches.append(f"{relative}:{line_number}:{line}")
                        if len(matches) >= 500:
                            return "\n".join(matches) + "\n... 结果过多，已截断"
            except (OSError, UnicodeDecodeError):
                continue
        return "\n".join(matches) if matches else "未找到匹配内容。"

    def _write_file(self, path: str, content: str) -> str:
        target = self._resolve_path(path)
        if target.exists() and target not in self._read_paths:
            raise ToolError(f"修改已有文件前必须先读取它: {path}")
        self._require_approval(
            tr("approval_write_file", path=target.relative_to(self.workspace))
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"写入文件失败: {exc}") from exc
        self._read_paths.add(target)
        return f"成功写入 {target}"

    def _replace_in_file(self, path: str, old_content: str, new_content: str) -> str:
        target = self._resolve_path(path)
        if target not in self._read_paths:
            raise ToolError(f"修改已有文件前必须先读取它: {path}")
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ToolError(f"读取文件失败: {exc}") from exc
        occurrences = content.count(old_content)
        if not old_content or occurrences != 1:
            raise ToolError(f"old_content 必须在文件中恰好出现一次，当前出现 {occurrences} 次")
        self._require_approval(
            tr("approval_modify_file", path=target.relative_to(self.workspace))
        )
        try:
            target.write_text(content.replace(old_content, new_content, 1), encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"写入文件失败: {exc}") from exc
        return f"成功修改 {target}"

    def _run_command(self, command: str) -> str:
        try:
            arguments = shlex.split(command)
        except ValueError as exc:
            raise ToolError(f"命令解析失败: {exc}") from exc
        if not arguments:
            raise ToolError("命令不能为空")
        if arguments[0] in {"rm", "sudo", "su", "shutdown", "reboot", "mkfs", "dd"}:
            raise ToolError(f"出于安全原因不允许执行命令: {arguments[0]}")
        if not self._is_low_risk_command(arguments):
            self._require_approval(tr("approval_run_command", command=command))
        try:
            completed = subprocess.run(
                arguments,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ToolError(f"找不到命令: {arguments[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"命令执行超过 {self.command_timeout} 秒") from exc
        output = completed.stdout
        if completed.stderr:
            output += ("\n" if output else "") + completed.stderr
        result = f"退出码: {completed.returncode}\n{output.strip()}"
        if completed.returncode != 0:
            raise ToolError(self._truncate(result))
        return self._truncate(result)

    @staticmethod
    def _is_low_risk_command(arguments: list[str]) -> bool:
        command = Path(arguments[0]).name
        if any(
            argument.startswith(("/", "~")) or ".." in Path(argument).parts
            for argument in arguments[1:]
        ):
            return False

        if command in {
            "basename",
            "cat",
            "cut",
            "dirname",
            "du",
            "file",
            "grep",
            "head",
            "id",
            "ls",
            "pwd",
            "stat",
            "tail",
            "uname",
            "uniq",
            "wc",
            "whoami",
        }:
            return True

        if command == "rg":
            return not any(
                argument == "--pre" or argument.startswith("--pre=")
                for argument in arguments[1:]
            )

        if command == "sed":
            return not any(
                argument == "-i"
                or argument.startswith("-i")
                or argument == "--in-place"
                or argument.startswith("--in-place=")
                for argument in arguments[1:]
            )

        if command == "sort":
            return not any(
                argument == "-o"
                or argument.startswith("-o")
                or argument.startswith("--output=")
                for argument in arguments[1:]
            )

        if command == "git" and len(arguments) >= 2:
            read_only_subcommand = arguments[1] in {
                "diff",
                "grep",
                "log",
                "rev-parse",
                "show",
                "status",
            }
            unsafe_options = {
                "--ext-diff",
                "--textconv",
                "--open-files-in-pager",
            }
            writes_output = any(
                argument == "--output" or argument.startswith("--output=")
                for argument in arguments[2:]
            )
            return (
                read_only_subcommand
                and not writes_output
                and not unsafe_options.intersection(arguments[2:])
            )
        return False

    def _require_approval(self, description: str) -> None:
        if self.auto_approve:
            return
        if not self.confirm(description):
            raise ToolError(tr("operation_denied", description=description))

    def _terminal_confirm(self, description: str) -> bool:
        if self._interaction_paused is not None:
            self._interaction_paused()
        try:
            answer = input(
                "\n" + tr("agent_permission_prompt", description=description)
            ).strip().lower()
        finally:
            if self._interaction_resumed is not None:
                self._interaction_resumed()
        if answer == "fc":
            self.auto_approve = True
            return True
        return answer in {"y", "yes", "是", "はい", "oui", "ja", "sim", "да"}

    @staticmethod
    def _truncate(text: str, limit: int = 20000) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "\n" + tr(
            "output_truncated", count=len(text) - limit
        )
