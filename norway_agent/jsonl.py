"""JSON Lines that survive any text a website can contain.

``json.dumps(..., ensure_ascii=False)`` leaves U+2028, U+2029 and U+0085 unescaped. They are legal
inside JSON strings, but ``str.splitlines()`` and some JSONL readers treat them as line breaks and
cut a record in two. Writers here escape them, so every record is exactly one "\\n"-terminated line
for any reader; readers split on "\\n" only, so files written by older versions still load.
"""
from __future__ import annotations

import json
from pathlib import Path

_LINE_BREAKS = ((" ", "\\u2028"), (" ", "\\u2029"), ("\x85", "\\u0085"))


def dumps(obj, **kw) -> str:
    """json.dumps for one JSONL record (or an inline <script> payload): no raw line separators."""
    kw.setdefault("ensure_ascii", False)
    text = json.dumps(obj, **kw)
    for raw, escaped in _LINE_BREAKS:
        if raw in text:  # only ever inside a string, where the escape means the same character
            text = text.replace(raw, escaped)
    return text


def lines(text: str) -> list[str]:
    """Non-blank records of a JSONL text, split on "\\n" only (a trailing "\\r" is dropped)."""
    return [line.rstrip("\r") for line in text.split("\n") if line.strip()]


def read(path) -> list:
    return [json.loads(line) for line in lines(Path(path).read_text(encoding="utf-8"))]
