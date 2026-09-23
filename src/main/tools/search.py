from __future__ import annotations

import subprocess
from pathlib import Path

from .base import BaseTool, ToolError


class SearchTools(BaseTool):
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
                cwd=self.context.workspace,
                capture_output=True,
                text=True,
                timeout=self.context.command_timeout,
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
                        relative = file_path.relative_to(self.context.workspace)
                        matches.append(f"{relative}:{line_number}:{line}")
                        if len(matches) >= 500:
                            return "\n".join(matches) + "\n... 结果过多，已截断"
            except (OSError, UnicodeDecodeError):
                continue
        return "\n".join(matches) if matches else "未找到匹配内容。"
