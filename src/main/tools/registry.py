from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from .base import tool_definition
from .command import CommandTool
from .filesystem import FilesystemTools
from .search import SearchTools


if TYPE_CHECKING:
    from src.main.agent.agent import Agent


def build_tool_handlers(context: Agent) -> dict[str, Callable[..., Any]]:
    filesystem = FilesystemTools(context)
    search = SearchTools(context)
    command = CommandTool(context)
    return {
        "list_directory": filesystem._list_directory,
        "read_file": filesystem._read_file,
        "search_files": search._search_files,
        "write_file": filesystem._write_file,
        "replace_in_file": filesystem._replace_in_file,
        "run_command": command._run_command,
    }


def build_tool_definitions() -> list[dict[str, Any]]:
    return [
        tool_definition(
            "list_directory",
            "List files and directories. Outside-workspace paths require user approval unless Full Control Mode is enabled. Use this first when the project structure is unknown.",
            {
                "path": {"type": "string", "description": "Directory path, absolute or relative to the workspace; use '.' for root. External paths trigger approval."},
                "depth": {"type": "integer", "description": "Recursion depth from 1 to 4.", "minimum": 1, "maximum": 4},
            },
            ["path"],
        ),
        tool_definition(
            "read_file",
            "Read a UTF-8, GBK, or BOM-marked Unicode text file. Existing files must be read before they can be modified.",
            {
                "path": {"type": "string", "description": "File path, absolute or relative to the workspace. External paths trigger approval."},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            ["path"],
        ),
        tool_definition(
            "search_files",
            "Search text in project files before guessing where a symbol or behavior is defined.",
            {
                "query": {"type": "string", "description": "Literal text to search for."},
                "path": {"type": "string", "description": "File or directory, absolute or relative to the workspace; defaults to '.'. External paths trigger approval."},
            },
            ["query"],
        ),
        tool_definition(
            "write_file",
            "Create a text file or overwrite one that has already been read. Requires user approval unless auto-approve is enabled.",
            {
                "path": {"type": "string", "description": "File path, absolute or relative to the workspace. External paths trigger approval."},
                "content": {"type": "string", "description": "Complete new file content."},
            },
            ["path", "content"],
        ),
        tool_definition(
            "replace_in_file",
            "Replace one exact, unique text block in a file that has already been read. Requires user approval unless auto-approve is enabled.",
            {
                "path": {"type": "string", "description": "File path, absolute or relative to the workspace. External paths trigger approval."},
                "old_content": {"type": "string", "description": "Exact existing text; it must occur exactly once."},
                "new_content": {"type": "string", "description": "Replacement text."},
            },
            ["path", "old_content", "new_content"],
        ),
        tool_definition(
            "run_command",
            "Run a non-interactive command in the workspace for inspection, tests, linting, builds, or internet research with curl. When external or current information is needed, use curl through this tool. Read-only low-risk commands run without approval; other commands require user approval. Shell operators are not supported.",
            {
                "command": {"type": "string", "description": "Command line parsed without a shell."},
            },
            ["command"],
        ),
    ]
