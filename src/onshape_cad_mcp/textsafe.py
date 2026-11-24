"""Sanitising of untrusted document text before it reaches an LLM."""
from __future__ import annotations

import re

# Whitespace/control runs collapse to one space (incl. NBSP and the Unicode line and
# paragraph separators); zero-width and bidi-control characters, which can hide or
# reorder text, are removed outright.
_RUN = re.compile("[\\s\\x00-\\x1f\\x7f-\\x9f\\u00a0\\u2028\\u2029]+")
_INVISIBLE = re.compile("[\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u2064\\u2066-\\u2069\\ufeff]")


def clean(s: object, max_len: int = 80) -> str:
    out = _RUN.sub(" ", _INVISIBLE.sub("", str(s))).strip()
    if len(out) > max_len:
        out = out[: max(max_len - 1, 0)] + "…"
    return out
