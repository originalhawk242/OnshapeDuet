"""Persistent request budget ledger. Fails closed on any file problem."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class BudgetExceeded(Exception):
    pass


class Ledger:
    def __init__(self, path: Path, limit: int) -> None:
        self.path = Path(path)
        self.limit = limit

    def _read(self) -> int:
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return 0
        except OSError as e:
            raise BudgetExceeded(f"ledger unusable: {e}") from e
        try:
            data = json.loads(raw)
            used = data["used"]
            if type(used) is not int or used < 0:
                raise ValueError(f"bad used value {used!r}")
        except (ValueError, KeyError, TypeError) as e:
            raise BudgetExceeded(f"ledger unusable: {self.path} is corrupt ({e})") from e
        return used

    def _write(self, used: int) -> None:
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".ledger-")
            with os.fdopen(fd, "w") as f:
                json.dump({"used": used}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise BudgetExceeded(f"ledger unusable: {e}") from e

    @property
    def used(self) -> int:
        return self._read()

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self._read())

    def charge(self, n: int = 1, reason: str = "") -> None:
        used = self._read()
        if used + n > self.limit:
            why = f" for {reason}" if reason else ""
            raise BudgetExceeded(
                f"request budget exhausted{why}: set `budget` in the config file "
                f"(currently {self.limit}, used {used})"
            )
        self._write(used + n)
