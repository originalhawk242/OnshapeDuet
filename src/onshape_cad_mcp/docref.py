"""Onshape document references parsed from URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ID = re.compile(r"[0-9a-f]{24}")


def is_valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


_RE = re.compile(r"^https?://[^/]*onshape\.com/documents/([^/]+)/(?:w|v)/([^/]+)/e/([^/?#]+)")


@dataclass(frozen=True)
class DocRef:
    document: str
    workspace: str  # workspace id, or version id for /v/ URLs (read-only)
    element: str

    def __post_init__(self) -> None:
        for name in ("document", "workspace", "element"):
            if not is_valid_id(getattr(self, name)):
                raise ValueError(f"invalid Onshape id for {name}: {getattr(self, name)!r}")


def parse_url(url: str) -> DocRef:
    m = _RE.match(url.strip())
    if not m:
        raise ValueError(f"not an Onshape document URL: {url!r}")
    return DocRef(*m.groups())
