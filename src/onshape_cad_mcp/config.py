"""Server configuration. The request budget defaults to 0 (no API calls)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    budget: int = 0
    ledger_path: Path = field(default_factory=lambda: Path.home() / ".onshape-cad-mcp" / "ledger.json")
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
    kwargs = {k: data[k] for k in ("budget", "key_env", "secret_env", "base_url") if k in data}
    if "ledger_path" in data:
        kwargs["ledger_path"] = Path(data["ledger_path"]).expanduser()
    return Config(**kwargs)
