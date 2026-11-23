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
