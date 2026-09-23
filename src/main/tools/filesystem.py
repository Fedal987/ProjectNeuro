from __future__ import annotations

import os
from pathlib import Path
from src.main.ui.i18n import tr
from src.main.encoding import encode_text, read_text_file

from .base import BaseTool, ToolError


class FilesystemTools(BaseTool):
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
            relative_root = self._display_path(root_path)
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
            content, _ = read_text_file(target)
        except UnicodeDecodeError as exc:
            raise ToolError(f"文件不是可读取的 UTF-8、GBK 或带 BOM 的 Unicode 文本: {path}") from exc
        except OSError as exc:
            raise ToolError(f"读取文件失败: {exc}") from exc
        self.context._read_paths.add(target)
        lines = content.splitlines()
        start = max(int(start_line), 1)
        end = len(lines) if end_line is None else min(int(end_line), len(lines))
        if end < start:
            raise ToolError("end_line 不能小于 start_line")
        numbered = [f"{index}: {lines[index - 1]}" for index in range(start, end + 1)]
        return self._truncate("\n".join(numbered))

    def _write_file(self, path: str, content: str) -> str:
        target = self._resolve_path(path)
        if target.exists() and target not in self.context._read_paths:
            raise ToolError(f"修改已有文件前必须先读取它: {path}")
        self.context.approval._require_approval(
            tr("approval_write_file", path=self._display_path(target))
        )
        try:
            original, encoding = read_text_file(target) if target.exists() else ("", "utf-8")
            data = encode_text(content, encoding, original)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except (OSError, UnicodeError) as exc:
            raise ToolError(f"写入文件失败: {exc}") from exc
        self.context._read_paths.add(target)
        return f"成功写入 {target}"

    def _replace_in_file(self, path: str, old_content: str, new_content: str) -> str:
        target = self._resolve_path(path)
        if target not in self.context._read_paths:
            raise ToolError(f"修改已有文件前必须先读取它: {path}")
        try:
            content, encoding = read_text_file(target)
        except (OSError, UnicodeDecodeError) as exc:
            raise ToolError(f"读取文件失败: {exc}") from exc
        if "\r\n" in content and "\n" not in content.replace("\r\n", ""):
            old_content = old_content.replace("\r\n", "\n").replace("\n", "\r\n")
            new_content = new_content.replace("\r\n", "\n").replace("\n", "\r\n")
        occurrences = content.count(old_content)
        if not old_content or occurrences != 1:
            raise ToolError(f"old_content 必须在文件中恰好出现一次，当前出现 {occurrences} 次")
        self.context.approval._require_approval(
            tr("approval_modify_file", path=self._display_path(target))
        )
        try:
            data = encode_text(content.replace(old_content, new_content, 1), encoding, content)
            target.write_bytes(data)
        except (OSError, UnicodeError) as exc:
            raise ToolError(f"写入文件失败: {exc}") from exc
        return f"成功修改 {target}"
