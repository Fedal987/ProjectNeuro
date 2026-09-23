from __future__ import annotations

import codecs
import locale
from pathlib import Path
import sys


def decode_text(data: bytes) -> tuple[str, str]:
    for marker, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ):
        if data.startswith(marker):
            payload = data if encoding == "utf-8-sig" else data[len(marker):]
            return payload.decode(encoding), encoding
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return data.decode("gbk"), "gbk"
        except UnicodeDecodeError:
            return data.decode("gb18030"), "gb18030"


def read_text_file(path: Path) -> tuple[str, str]:
    return decode_text(path.read_bytes())


def encode_text(text: str, encoding: str, original: str = "") -> bytes:
    if "\r\n" in original and "\n" not in original.replace("\r\n", ""):
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    marker = {
        "utf-16-le": codecs.BOM_UTF16_LE,
        "utf-16-be": codecs.BOM_UTF16_BE,
        "utf-32-le": codecs.BOM_UTF32_LE,
        "utf-32-be": codecs.BOM_UTF32_BE,
    }.get(encoding, b"")
    return marker + text.encode(encoding)


def decode_output(data: bytes | str | None) -> str:
    if isinstance(data, str):
        return data
    if not data:
        return ""
    try:
        text, _ = decode_text(data)
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False)
        text = data.decode(encoding, errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def configure_terminal_encoding() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None or getattr(stream, "closed", False):
            continue
        if stream.isatty():
            reconfigure(errors="backslashreplace")
        else:
            reconfigure(encoding="utf-8", errors="backslashreplace")
