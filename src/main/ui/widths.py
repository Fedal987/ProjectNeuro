"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
import unicodedata
from collections.abc import Callable
from functools import lru_cache
from typing import Any

__all__ = [
    "ConsoleWidthOracle",
    "apply_width_overrides",
    "calibrate_console_widths",
    "clear_width_overrides",
    "display_width",
    "is_disabled",
]

DISABLE_ENV = "NEURO_CONSOLE_WIDTHS"

_oracle: ConsoleWidthOracle | None = None
_applied: bool = False
_lock = threading.Lock()

_pristine_rich_width: Callable[..., int] | None = None
_pristine_captured: bool = False

_undo: list[tuple[Any, str, Any]] = []


def is_disabled() -> bool:
    value = os.environ.get(DISABLE_ENV, "").strip().lower()
    return value in {"0", "false", "no", "off"}




def _capture_pristine_rich_width() -> None:
    global _pristine_rich_width, _pristine_captured
    if _pristine_captured:
        return
    with _lock:
        if _pristine_captured:
            return
        try:
            from rich import cells as rich_cells

            candidate = getattr(rich_cells, "get_character_cell_size", None)
            if candidate is not None and not getattr(
                candidate, "_neuro_wrapper", False
            ):
                _pristine_rich_width = candidate
        except ImportError:
            pass
        _pristine_captured = True


def _library_width(char: str) -> int:
    function = _pristine_rich_width
    if function is None:
        from rich.cells import cell_len

        return cell_len(char)
    return function(char)


def _defer_to_libraries(char: str) -> bool:
    if char.isascii():
        return True
    return unicodedata.category(char).startswith("M")


class ConsoleWidthOracle:

    def __init__(self, measurer: Callable[[str], int | None] | None = None) -> None:
        self._measurer = measurer
        self._cache: dict[str, int | None] = {}
        self._differs: set[str] = set()

    @property
    def measurer_available(self) -> bool:
        return self._measurer is not None

    @property
    def differs(self) -> frozenset[str]:
        return frozenset(self._differs)

    def width(self, char: str) -> int | None:
        if len(char) != 1 or _defer_to_libraries(char):
            return None
        try:
            return self._cache[char]
        except KeyError:
            pass
        measured = None
        if self._measurer is not None:
            measured = self._measurer(char)
            if measured is not None and measured != _library_width(char):
                self._differs.add(char)
        self._cache[char] = measured
        return measured

    def text_width(self, text: str) -> int:
        total = 0
        for char in text:
            measured = self.width(char)
            total += _library_width(char) if measured is None else measured
        return total

    def agrees(self, text: str) -> bool:
        for char in text:
            measured = self.width(char)
            if measured is not None and measured != _library_width(char):
                return False
        return True




def _windows_measurer_factory() -> Callable[[str], int | None] | None:
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", ctypes.c_short),
            ("Top", ctypes.c_short),
            ("Right", ctypes.c_short),
            ("Bottom", ctypes.c_short),
        ]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", COORD),
            ("dwCursorPosition", COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SMALL_RECT),
            ("dwMaximumWindowSize", COORD),
        ]

    k32.CreateConsoleScreenBuffer.restype = wintypes.HANDLE
    k32.CreateConsoleScreenBuffer.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    k32.WriteConsoleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    k32.GetConsoleScreenBufferInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO),
    ]
    k32.SetConsoleCursorPosition.argtypes = [wintypes.HANDLE, COORD]

    invalid = ctypes.c_void_p(-1).value
    try:
        handle = k32.CreateConsoleScreenBuffer(
            0x80000000 | 0x40000000, 0x03, None, 1, None
        )
    except OSError:
        return None
    if not handle or handle == invalid:
        return None

    atexit.register(k32.CloseHandle, handle)

    written = wintypes.DWORD(0)
    start_column = 5
    measure_lock = threading.Lock()

    def measure(char: str) -> int | None:
        with measure_lock:
            try:
                if not k32.SetConsoleCursorPosition(handle, COORD(start_column, 0)):
                    return None
                if not k32.WriteConsoleW(handle, char, 1, ctypes.byref(written), None):
                    return None
                info = CONSOLE_SCREEN_BUFFER_INFO()
                if not k32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
                    return None
            except OSError:
                return None
        return int(info.dwCursorPosition.X) - start_column

    if measure("A") != 1:
        return None
    return measure


def _stdout_is_console() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def calibrate_console_widths() -> bool:
    global _oracle
    if _applied:
        return True
    if is_disabled() or not _stdout_is_console():
        return False
    _capture_pristine_rich_width()
    if _pristine_rich_width is None:
        return False
    measurer = _windows_measurer_factory()
    if measurer is None:
        return False
    oracle = ConsoleWidthOracle(measurer)
    if oracle.width("\u00b7") is None:
        return False
    _oracle = oracle
    return apply_width_overrides(oracle)




def _install(owner: Any, attribute: str, replacement: Any) -> None:
    _undo.append((owner, attribute, getattr(owner, attribute)))
    setattr(owner, attribute, replacement)


def apply_width_overrides(oracle: ConsoleWidthOracle) -> bool:
    global _applied, _oracle
    if _applied:
        return True
    if oracle is None or not oracle.measurer_available:
        return False
    patched_rich = _patch_rich(oracle)
    patched_prompt_toolkit = _patch_prompt_toolkit(oracle)
    if not patched_rich and not patched_prompt_toolkit:
        return False
    _oracle = oracle
    _applied = True
    return True


def _patch_rich(oracle: ConsoleWidthOracle) -> bool:
    _capture_pristine_rich_width()
    pristine = _pristine_rich_width
    if pristine is None:
        return False
    try:
        from rich import cells as rich_cells
    except ImportError:
        return False
    if getattr(rich_cells, "_neuro_patched", False):
        return True

    @lru_cache(maxsize=4096)
    def measured_cell_size(character: str, unicode_version: str = "auto") -> int:
        measured = oracle.width(character)
        if measured is not None:
            return measured
        return pristine(character, unicode_version)

    measured_cell_size._neuro_wrapper = True

    _install(rich_cells, "get_character_cell_size", measured_cell_size)

    segment = None
    try:
        from rich import segment as segment_module

        segment = segment_module
    except ImportError:
        pass
    if (
        segment is not None
        and getattr(segment, "get_character_cell_size", None) is pristine
    ):
        _install(segment, "get_character_cell_size", measured_cell_size)

    original_single_cell = getattr(rich_cells, "_is_single_cell_widths", None)
    if original_single_cell is not None:

        def _is_single_cell_widths(text: str) -> bool:
            return original_single_cell(text) and oracle.agrees(text)

        _install(rich_cells, "_is_single_cell_widths", _is_single_cell_widths)
        if (
            segment is not None
            and getattr(segment, "_is_single_cell_widths", None) is original_single_cell
        ):
            _install(segment, "_is_single_cell_widths", _is_single_cell_widths)

    rich_cells._neuro_patched = True
    _clear_rich_caches(measured_cell_size, pristine)
    return True


def _clear_rich_caches(measured_cell_size: Any, pristine: Any) -> None:
    for function in (
        measured_cell_size,
        pristine,
        getattr(sys.modules.get("rich.cells"), "cached_cell_len", None),
    ):
        cache_clear = getattr(function, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()


def _patch_prompt_toolkit(oracle: ConsoleWidthOracle) -> bool:
    try:
        from prompt_toolkit import utils as ptk_utils
    except ImportError:
        return False

    cache_class = getattr(ptk_utils, "_CharSizesCache", None)
    cache = getattr(ptk_utils, "_CHAR_SIZES_CACHE", None)
    if cache_class is None or cache is None:
        return False
    if getattr(cache_class, "_neuro_patched", False):
        return True

    original_missing = cache_class.__missing__

    def __missing__(self, string: str):
        measured = oracle.width(string)
        if measured is not None:
            self[string] = measured
            return measured
        return original_missing(self, string)

    _install(cache_class, "__missing__", __missing__)
    cache_class._neuro_patched = True
    cache.clear()
    return True


def clear_width_overrides() -> None:
    global _applied, _oracle
    while _undo:
        owner, attribute, original = _undo.pop()
        setattr(owner, attribute, original)
    try:
        from rich import cells as rich_cells

        rich_cells._neuro_patched = False
        for name in ("cached_cell_len", "get_character_cell_size"):
            cache_clear = getattr(getattr(rich_cells, name, None), "cache_clear", None)
            if cache_clear is not None:
                cache_clear()
    except ImportError:
        pass
    try:
        from prompt_toolkit import utils as ptk_utils

        cache_class = getattr(ptk_utils, "_CharSizesCache", None)
        if cache_class is not None:
            cache_class._neuro_patched = False
        cache = getattr(ptk_utils, "_CHAR_SIZES_CACHE", None)
        if cache is not None:
            cache.clear()
    except ImportError:
        pass
    _applied = False
    _oracle = None


def display_width(text: str) -> int:
    oracle = _oracle
    if oracle is None:
        return sum(_library_width(char) for char in text)
    return oracle.text_width(text)
