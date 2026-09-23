from __future__ import annotations

import json
from typing import Any

from src.main.ui.i18n import tr
from .base import BaseTool, ToolError


class ToolDispatcher(BaseTool):
    def _execute_tool_call(self, tool_call: dict[str, Any]) -> str:
        function = tool_call.get("function") or {}
        name = function.get("name", "")
        signature = f"{name}:{function.get('arguments') or '{}'}"
        if signature == self.context._last_failed_call:
            return (
                f"工具 {name} 的相同调用刚刚已经失败，已阻止无变化的重复执行。"
                "请先分析错误、获取新信息或采用不同方案。"
            )
        handler = self.context.tool_handlers.get(name)
        if handler is None:
            self.context._last_failed_call = signature
            return f"工具执行失败: 未知工具 {name!r}"
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("工具参数必须是 JSON 对象")
            result = handler(**arguments)
            self.context._last_failed_call = None
            return self._truncate(str(result))
        except (TypeError, ValueError, ToolError, OSError) as exc:
            self.context._last_failed_call = signature
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
