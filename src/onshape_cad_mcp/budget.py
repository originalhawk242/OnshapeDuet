"""Persistent request budget ledger. Fails closed on any file problem."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path


def _unusable(path, why) -> str:
    return f"ledger unusable: {why} (fix or delete {path})"


class BudgetExceeded(Exception):
    pass


class Ledger:
    def __init__(self, path: Path, limit: int) -> None:
        self.path = Path(path)
        self.limit = limit

    def _read(self) -> int:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0
        except (OSError, UnicodeDecodeError) as e:
            raise BudgetExceeded(_unusable(self.path, e)) from e
        try:
            data = json.loads(raw)
            used = data["used"]
            if type(used) is not int or used < 0:
                raise ValueError(f"bad used value {used!r}")
        except (ValueError, KeyError, TypeError) as e:
            raise BudgetExceeded(_unusable(self.path, f"corrupt: {e}")) from e
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
            raise BudgetExceeded(_unusable(self.path, e)) from e

    @property
    def used(self) -> int:
        return self._read()

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self._read())

    def charge(self, n: int = 1, reason: str = "") -> None:
        if type(n) is not int or n < 1:
            raise ValueError(f"charge n must be a positive int, got {n!r}")
        lock_path = self.path.with_name(self.path.name + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock = open(lock_path, "a")
        except OSError as e:
            raise BudgetExceeded(_unusable(lock_path, e)) from e
        with lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
            except OSError as e:
                raise BudgetExceeded(_unusable(lock_path, e)) from e
            used = self._read()
            if used + n > self.limit:
                why = f" for {reason}" if reason else ""
                raise BudgetExceeded(
                    f"request budget exhausted{why}: set `budget` in the config file "
                    f"(currently {self.limit}, used {used})"
                )
            self._write(used + n)
