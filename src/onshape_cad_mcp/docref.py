"""Onshape document references parsed from URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass

_RE = re.compile(r"^https?://[^/]*onshape\.com/documents/([^/]+)/(?:w|v)/([^/]+)/e/([^/?#]+)")


@dataclass(frozen=True)
class DocRef:
    document: str
    workspace: str  # workspace id, or version id for /v/ URLs (read-only)
    element: str


def parse_url(url: str) -> DocRef:
    m = _RE.match(url.strip())
    if not m:
        raise ValueError(f"not an Onshape document URL: {url!r}")
    return DocRef(*m.groups())
