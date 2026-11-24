from pathlib import Path

import pytest

from onshape_cad_mcp.budget import BudgetExceeded, Ledger


def test_charge_counts(tmp_path):
    led = Ledger(tmp_path / "ledger.json", limit=5)
    assert led.used == 0 and led.remaining == 5
    led.charge()
    led.charge(2, reason="read")
    assert led.used == 3 and led.remaining == 2


def test_limit_zero_refuses_first_charge(tmp_path):
    p = tmp_path / "ledger.json"
    led = Ledger(p, limit=0)
    with pytest.raises(BudgetExceeded) as e:
        led.charge()
    assert "budget" in str(e.value)
    assert "currently 0" in str(e.value)
    assert led.used == 0


def test_exceeding_leaves_file_unchanged(tmp_path):
    p = tmp_path / "ledger.json"
    led = Ledger(p, limit=2)
    led.charge(2)
    before = p.read_text()
    with pytest.raises(BudgetExceeded):
        led.charge()
    assert p.read_text() == before


def test_persists_across_instances(tmp_path):
    p = tmp_path / "ledger.json"
    Ledger(p, limit=5).charge(2)
    assert Ledger(p, limit=5).used == 2


def test_corrupt_file_fails_closed(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text("garbage{{")
    led = Ledger(p, limit=5)
    with pytest.raises(BudgetExceeded):
        led.charge()
    with pytest.raises(BudgetExceeded):
        led.used
    assert p.read_text() == "garbage{{"


def test_unwritable_path_fails_closed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    led = Ledger(blocker / "sub" / "ledger.json", limit=5)
    with pytest.raises(BudgetExceeded) as e:
        led.charge()
    assert "ledger unusable" in str(e.value)


def test_non_utf8_file_fails_closed(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_bytes(b"\xff\xfe\x80garbage")
    led = Ledger(p, limit=5)
    with pytest.raises(BudgetExceeded) as e:
        led.charge()
    assert "ledger unusable" in str(e.value)
    assert "fix or delete" in str(e.value)
    with pytest.raises(BudgetExceeded):
        led.used


@pytest.mark.parametrize("n", [0, -1, 1.5, True, "1"])
def test_invalid_n_rejected(tmp_path, n):
    led = Ledger(tmp_path / "ledger.json", limit=5)
    with pytest.raises(ValueError):
        led.charge(n)
    assert led.used == 0


def _worker(args):
    path, limit, attempts = args
    ok = 0
    for _ in range(attempts):
        try:
            Ledger(Path(path), limit).charge()
            ok += 1
        except BudgetExceeded:
            pass
    return ok


def test_concurrent_charges_are_serialized(tmp_path):
    import multiprocessing

    p = tmp_path / "ledger.json"
    limit = 40
    with multiprocessing.Pool(6) as pool:
        oks = pool.map(_worker, [(str(p), limit, 15)] * 6)
    assert sum(oks) == limit
    assert Ledger(p, limit).used == limit


def test_lock_failure_fails_closed(tmp_path, monkeypatch):
    import fcntl

    def boom(*a, **k):
        raise OSError("no locks")

    monkeypatch.setattr(fcntl, "flock", boom)
    with pytest.raises(BudgetExceeded) as e:
        Ledger(tmp_path / "ledger.json", limit=5).charge()
    assert "ledger unusable" in str(e.value)


# --- wave 2 (T2) ---

def test_failed_write_with_existing_ledger_keeps_old_file_and_removes_temp(tmp_path, monkeypatch):
    import os
    p = tmp_path / "ledger.json"
    led = Ledger(p, limit=10)
    led.charge(2)
    before = p.read_text()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(BudgetExceeded):
        led.charge()
    monkeypatch.undo()
    assert p.read_text() == before and led.used == 2
    assert [f.name for f in tmp_path.iterdir() if f.name.startswith(".ledger-")] == []


def test_fdopen_failure_closes_the_descriptor(tmp_path, monkeypatch):
    import os
    closed = []
    real_close = os.close

    def bad_fdopen(fd, *a, **k):
        raise OSError("no fdopen")
    monkeypatch.setattr(os, "fdopen", bad_fdopen)
    monkeypatch.setattr(os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    led = Ledger(tmp_path / "l.json", limit=10)
    with pytest.raises(BudgetExceeded):
        led.charge()
    assert len(closed) == 1
    assert [f.name for f in tmp_path.iterdir() if f.name.startswith(".ledger-")] == []


def test_successful_write_fsyncs_the_directory(tmp_path, monkeypatch):
    import os
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(os.fstat(fd).st_mode), real(fd))[1])
    Ledger(tmp_path / "l.json", limit=3).charge()
    import stat
    assert any(stat.S_ISDIR(m) for m in synced)


def test_missing_fcntl_fails_closed_at_use_not_at_import(tmp_path, monkeypatch):
    import onshape_cad_mcp.budget as budget
    monkeypatch.setattr(budget, "fcntl", None)
    led = Ledger(tmp_path / "l.json", limit=3)
    with pytest.raises(BudgetExceeded, match="POSIX"):
        led.charge()
    assert led.used == 0  # reading still works; nothing was charged
