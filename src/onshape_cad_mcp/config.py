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


def load_config(path: Path | None) -> Config:
    if path is None or not Path(path).exists():
        return Config()
    data = tomllib.loads(Path(path).read_text())
    kwargs = {k: data[k] for k in ("budget", "key_env", "secret_env", "base_url") if k in data}
    if "ledger_path" in data:
        kwargs["ledger_path"] = Path(data["ledger_path"]).expanduser()
    return Config(**kwargs)
