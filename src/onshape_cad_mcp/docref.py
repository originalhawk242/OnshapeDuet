"""Onshape document references parsed from URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ID = re.compile(r"[0-9a-f]{24}")


def is_valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


_RE = re.compile(r"^https?://[^/]*onshape\.com/documents/([^/]+)/(?:w|v)/([^/]+)/e/([^/?#]+)")
_VERSION_RE = re.compile(r"^https?://[^/]*onshape\.com/documents/[^/]+/v/")


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
    url = url.strip()
    if _VERSION_RE.match(url):
        raise ValueError("version URLs (/v/) are not supported yet: open the workspace "
                         "(/w/) URL of the Part Studio instead")
    m = _RE.match(url)
    if not m:
        raise ValueError(f"not an Onshape document URL (expected "
                         f"https://cad.onshape.com/documents/<id>/w/<id>/e/<id>): {url!r}")
    return DocRef(*m.groups())
