"""Onshape document references parsed from URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_ID = re.compile(r"[0-9a-f]{24}")


def is_valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


DEFAULT_BASE_URL = "https://cad.onshape.com"
# Any host is captured, then compared (case-insensitively, exactly) to the host in
# `base_url`: that host is the ONLY one requests are ever sent to, so a URL that
# names another stack must be refused, not silently read from the configured one.
_RE = re.compile(r"^https://([A-Za-z0-9.-]+)(?::443)?/documents/([^/]+)/(?:w|v)/([^/]+)/e/([^/?#]+)",
                 re.IGNORECASE)
_VERSION_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::443)?/documents/[^/]+/v/", re.IGNORECASE)


@dataclass(frozen=True)
class DocRef:
    document: str
    workspace: str  # workspace id, or version id for /v/ URLs (read-only)
    element: str

    def __post_init__(self) -> None:
        for name in ("document", "workspace", "element"):
            if not is_valid_id(getattr(self, name)):
                raise ValueError(f"invalid Onshape id for {name}: {getattr(self, name)!r}")


def parse_url(url: str, base_url: str = DEFAULT_BASE_URL) -> DocRef:
    url = url.strip()
    host = (urlsplit(base_url).hostname or "").lower()
    if _VERSION_RE.match(url):
        raise ValueError("version URLs (/v/) are not supported yet: open the workspace "
                         "(/w/) URL of the Part Studio instead")
    m = _RE.match(url)
    if not m:
        raise ValueError(f"not an Onshape document URL (expected "
                         f"https://{host}/documents/<id>/w/<id>/e/<id>): {url!r}")
    url_host = m.group(1).lower()
    if url_host != host:
        raise ValueError(
            f"the URL host {url_host!r} is not the configured Onshape host {host!r}. "
            "Requests only ever go to the host in `base_url`; for another Onshape stack "
            "(enterprise) set `base_url` in the config file, e.g. "
            f"base_url = \"https://{url_host}\"")
    return DocRef(*m.groups()[1:])
