from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.main.encoding import decode_output, read_text_file

from .base import BaseTool, ToolError


class SearchTools(BaseTool):
    def _search_files(self, query: str, path: str = ".") -> str:
        if not query:
            raise ToolError("搜索内容不能为空")
        target = self._resolve_path(path)
        if not target.exists():
            raise ToolError(f"搜索路径不存在: {path}")

        if sys.platform == "win32":
            return self._search_files_python(query, target)

        command = ["rg", "--line-number", "--fixed-strings", "--glob", "!.git/**", "--", query, str(target)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.context.workspace,
                capture_output=True,
                timeout=self.context.command_timeout,
                check=False,
            )
        except FileNotFoundError:
            return self._search_files_python(query, target)
        if completed.returncode == 1:
            return "未找到匹配内容。"
        if completed.returncode != 0:
            raise ToolError(decode_output(completed.stderr).strip() or f"rg 退出码 {completed.returncode}")
        return self._truncate(decode_output(completed.stdout))

    def _search_files_python(self, query: str, target: Path) -> str:
        files = [target] if target.is_file() else target.rglob("*")
        matches: list[str] = []
        for file_path in files:
            if not file_path.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in file_path.parts):
                continue
            try:
                for line_number, line in enumerate(read_text_file(file_path)[0].splitlines(), 1):
                    if query in line:
                        relative = self._display_path(file_path)
                        matches.append(f"{relative}:{line_number}:{line}")
                        if len(matches) >= 500:
                            return "\n".join(matches) + "\n... 结果过多，已截断"
            except (OSError, UnicodeDecodeError):
                continue
        return "\n".join(matches) if matches else "未找到匹配内容。"
