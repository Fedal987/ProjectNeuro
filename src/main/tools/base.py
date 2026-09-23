from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.main.ui.i18n import tr

if TYPE_CHECKING:
    from src.main.agent.agent import Agent


class ToolError(Exception):
    pass


class BaseTool:
    def __init__(self, context: Agent):
        self.context = context

    def _resolve_path(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.context.workspace / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.context.workspace)
        except ValueError:
            self.context.approval._require_approval(
                f"路径超出工作目录，允许本次访问 {resolved} 吗？"
            )
        return resolved

    def _display_path(self, path: Path) -> Path:
        try:
            return path.relative_to(self.context.workspace)
        except ValueError:
            return path

    @staticmethod
    def _truncate(text: str, limit: int = 20000) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "\n" + tr(
            "output_truncated", count=len(text) - limit
        )


def tool_definition(
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
