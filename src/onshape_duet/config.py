"""Server configuration. The request budget defaults to 0 (no API calls)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    budget: int = 0
    ledger_path: Path = field(default_factory=lambda: Path.home() / ".onshape-duet" / "ledger.json")
    key_env: str = "ONSHAPE_ACCESS_KEY"
    secret_env: str = "ONSHAPE_SECRET_KEY"
    base_url: str = "https://cad.onshape.com"


_KEYS = {"budget", "ledger_path", "key_env", "secret_env", "base_url"}


def load_config(path: Path | None) -> Config:
    if path is None or not Path(path).exists():
        return Config()
    try:
        data = tomllib.loads(Path(path).read_text())
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read config file {path}: {e}") from e
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        raise ValueError(f"unknown config key(s) in {path}: {', '.join(map(str, unknown))}; "
                         f"valid keys: {', '.join(sorted(_KEYS))}")
    b = data.get("budget", 0)
    if type(b) is not int or b < 0:
        raise ValueError(f"config key `budget` in {path} must be an integer >= 0, got {b!r}")
    for key in ("key_env", "secret_env", "ledger_path", "base_url"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ValueError(f"config key `{key}` in {path} must be a non-empty string, got {data[key]!r}")
    if "base_url" in data:
        u = urlsplit(data["base_url"])
        if u.scheme != "https" or not u.netloc or u.path not in ("", "/") or u.query or u.fragment:
            raise ValueError(f"config key `base_url` in {path} must be an https URL with no path "
                             f"(e.g. https://cad.onshape.com), got {data['base_url']!r}")
    kwargs = {k: data[k] for k in ("budget", "key_env", "secret_env") if k in data}
    if "base_url" in data:
        kwargs["base_url"] = data["base_url"].rstrip("/")
    if "ledger_path" in data:
        kwargs["ledger_path"] = Path(data["ledger_path"]).expanduser()
    return Config(**kwargs)
