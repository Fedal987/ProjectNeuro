"""Console-accurate character widths for the full-screen UI.

``wcwidth`` (used by prompt_toolkit) and rich's cell table follow the POSIX
convention that East Asian "Ambiguous" characters occupy a single cell. A
Windows console running a CJK code page with a CJK font draws many of them two
cells wide. Measured on a CP936 console with 新宋体 (NSimSun):

    '·'  '—'  '…'  '’'  '→'  '°'  '±'  '×'  and the Cyrillic letters
        console: 2 cells      wcwidth/rich: 1 cell

The two models disagree, so rich pads a line by one column while the console has
already advanced by two. Every following column on that line, including a
panel's right border, is displaced -- and the error accumulates, which is why
switching to Russian (or any language with accents) visibly shears the layout.

This module asks the real console how many cells it gives a character and, when
that differs from what the libraries assume, teaches both libraries the
console's answer. Everything is best effort: with no console attached
(redirected output, tests, non-Windows) nothing is measured, nothing is patched,
and the libraries keep their own tables.

Set ``NEURO_CONSOLE_WIDTHS=0`` to disable detection.

Which characters get measured
-----------------------------
Every non-ASCII character that is not a combining mark. Two narrower rules were
considered and rejected against a 2,591-character probe of this console:

* "only East Asian Ambiguous characters" is unsound: emoji such as U+1F300 are
  classed Wide and given 2 cells by rich, but this console draws them 1 cell.
* "only characters the libraries call 1 cell wide" is unsound in the same way,
  in reverse.

Combining marks are deliberately left to the libraries. A mark measured alone
occupies a cell of its own, but it is drawn joined to the preceding base
character, so its isolated width is not its width in context. The libraries
correctly give marks zero width, and no mark appears in any catalog or in the
logo, so deferring them costs nothing here.
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

# The oracle and the patches it installed, kept for the process lifetime so
# repeated calls stay cheap.
_oracle: ConsoleWidthOracle | None = None
_applied: bool = False
_lock = threading.Lock()

# rich's own width function, captured before any patch. It is used both as the
# fallback and to decide whether the console disagrees, so it must never be the
# patched version.
_pristine_rich_width: Callable[..., int] | None = None
_pristine_captured: bool = False

# (owner, attribute, original) for every monkey patch, replayed in reverse by
# clear_width_overrides.
_undo: list[tuple[Any, str, Any]] = []


def is_disabled() -> bool:
    """Return True when calibration was switched off through the environment."""
    value = os.environ.get(DISABLE_ENV, "").strip().lower()
    return value in {"0", "false", "no", "off"}


# --------------------------------------------------------------------------
# The oracle
# --------------------------------------------------------------------------


def _capture_pristine_rich_width() -> None:
    """Remember rich's unpatched width function exactly once."""
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
    """Width the layout libraries assume, ignoring overrides we installed."""
    function = _pristine_rich_width
    if function is None:
        # Only reachable when an oracle was built without calibrating (tests).
        # rich is unpatched in that case, so its public function is safe.
        from rich.cells import cell_len

        return cell_len(char)
    return function(char)


def _defer_to_libraries(char: str) -> bool:
    """True when the console must not be asked about this character.

    ASCII never differs. Combining marks are excluded because they render joined
    to a base character, so measuring one on its own is meaningless.
    """
    if char.isascii():
        return True
    return unicodedata.category(char).startswith("M")


class ConsoleWidthOracle:
    """Per-character widths measured from a real console, memoized.

    ``measurer`` returns the number of cells the console advances for a single
    character, or None when that cannot be determined. It is injected so the
    oracle can be unit tested without a console.
    """

    def __init__(self, measurer: Callable[[str], int | None] | None = None) -> None:
        self._measurer = measurer
        self._cache: dict[str, int | None] = {}
        # Characters measured to disagree with the libraries. Used for reporting
        # and tests; correctness relies on ``agrees`` measuring, not on this set.
        self._differs: set[str] = set()

    @property
    def measurer_available(self) -> bool:
        return self._measurer is not None

    @property
    def differs(self) -> frozenset[str]:
        return frozenset(self._differs)

    def width(self, char: str) -> int | None:
        """Console width of one character, or None to defer to the libraries."""
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
        """Console width of a string, using measured widths where available."""
        total = 0
        for char in text:
            measured = self.width(char)
            total += _library_width(char) if measured is None else measured
        return total

    def agrees(self, text: str) -> bool:
        """True when the console gives every character its library width.

        This measures on demand rather than consulting ``_differs``: a caller
        may ask about a character that has not been measured yet, and reporting
        agreement then would let the libraries' fast paths return a wrong width.
        """
        for char in text:
            measured = self.width(char)
            if measured is not None and measured != _library_width(char):
                return False
        return True


# --------------------------------------------------------------------------
# Measuring the Windows console
# --------------------------------------------------------------------------


def _windows_measurer_factory() -> Callable[[str], int | None] | None:
    """Build a measurer backed by an off-screen console screen buffer.

    A private buffer is used so calibration never disturbs what the user sees.
    Returns None when there is no usable console.
    """
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
    # Characters are drawn at a fixed column, so the cursor delta is the width.
    start_column = 5
    # The buffer is shared, so measurements must not interleave.
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
        # The buffer cannot report widths; do not trust anything it says.
        return None
    return measure


def _stdout_is_console() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def calibrate_console_widths() -> bool:
    """Measure the console and align rich and prompt_toolkit with it.

    Returns True when overrides are in effect. Safe to call more than once; the
    second call is a no-op. Call this before anything renders.
    """
    global _oracle
    if _applied:
        return True
    if is_disabled() or not _stdout_is_console():
        # No console means no full-screen UI and no widths to correct.
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


# --------------------------------------------------------------------------
# Teaching the libraries the console's widths
# --------------------------------------------------------------------------


def _install(owner: Any, attribute: str, replacement: Any) -> None:
    """Set ``owner.attribute`` and remember how to put it back."""
    _undo.append((owner, attribute, getattr(owner, attribute)))
    setattr(owner, attribute, replacement)


def apply_width_overrides(oracle: ConsoleWidthOracle) -> bool:
    """Patch rich and prompt_toolkit to use ``oracle``'s widths.

    Idempotent. Returns True when the overrides are active.
    """
    global _applied, _oracle
    if _applied:
        return True
    if oracle is None or not oracle.measurer_available:
        return False
    # rich has to agree first: it pads and wraps the text that prompt_toolkit
    # then lays out, so a line must leave rich already the right length.
    patched_rich = _patch_rich(oracle)
    patched_prompt_toolkit = _patch_prompt_toolkit(oracle)
    if not patched_rich and not patched_prompt_toolkit:
        return False
    _oracle = oracle
    _applied = True
    return True


def _patch_rich(oracle: ConsoleWidthOracle) -> bool:
    """Route rich's cell measurements through the oracle."""
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

    measured_cell_size._neuro_wrapper = True  # type: ignore[attr-defined]

    _install(rich_cells, "get_character_cell_size", measured_cell_size)

    # Modules that bound these names by value at import time need them too.
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
            # The original fast path returns ``len(text)``. That is only correct
            # when the console draws every character one cell wide, so ask the
            # console rather than trusting the hardcoded ranges -- they cover
            # Cyrillic, which this console draws two cells wide.
            return original_single_cell(text) and oracle.agrees(text)

        _install(rich_cells, "_is_single_cell_widths", _is_single_cell_widths)
        if (
            segment is not None
            and getattr(segment, "_is_single_cell_widths", None) is original_single_cell
        ):
            _install(segment, "_is_single_cell_widths", _is_single_cell_widths)

    rich_cells._neuro_patched = True  # type: ignore[attr-defined]
    _clear_rich_caches(measured_cell_size, pristine)
    return True


def _clear_rich_caches(measured_cell_size: Any, pristine: Any) -> None:
    """Drop widths rich memoized before the overrides existed."""
    for function in (
        measured_cell_size,
        pristine,
        getattr(sys.modules.get("rich.cells"), "cached_cell_len", None),
    ):
        cache_clear = getattr(function, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()


def _patch_prompt_toolkit(oracle: ConsoleWidthOracle) -> bool:
    """Route every prompt_toolkit width query through the oracle.

    All of prompt_toolkit's width lookups funnel through one shared dict
    subclass (``_CHAR_SIZES_CACHE``), so replacing ``__missing__`` covers the
    layout, the controls and ``Char.width`` in a single change.
    """
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
    cache_class._neuro_patched = True  # type: ignore[attr-defined]
    # Drop widths cached before the overrides existed.
    cache.clear()
    return True


def clear_width_overrides() -> None:
    """Restore the libraries' own width tables. Intended for tests."""
    global _applied, _oracle
    while _undo:
        owner, attribute, original = _undo.pop()
        setattr(owner, attribute, original)
    try:
        from rich import cells as rich_cells

        # Both flags must be cleared, or a later apply would see the flag, skip
        # the patch, and silently leave the library using its own table.
        rich_cells._neuro_patched = False  # type: ignore[attr-defined]
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
            cache_class._neuro_patched = False  # type: ignore[attr-defined]
        cache = getattr(ptk_utils, "_CHAR_SIZES_CACHE", None)
        if cache is not None:
            cache.clear()
    except ImportError:
        pass
    _applied = False
    _oracle = None


def display_width(text: str) -> int:
    """Width of ``text`` in console cells."""
    oracle = _oracle
    if oracle is None:
        return sum(_library_width(char) for char in text)
    return oracle.text_width(text)
