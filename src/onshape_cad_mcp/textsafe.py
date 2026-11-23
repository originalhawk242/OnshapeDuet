"""Sanitising of untrusted document text before it reaches an LLM."""
from __future__ import annotations

import re

_RUN = re.compile(r"[\s\x00-\x1f\x7f-\x9f  ]+")


def clean(s: object, max_len: int = 80) -> str:
    out = _RUN.sub(" ", str(s)).strip()
    if len(out) > max_len:
        out = out[: max(max_len - 1, 0)] + "…"
    return out
