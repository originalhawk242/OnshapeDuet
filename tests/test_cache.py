from onshape_cad_mcp.backends.fake import FakeBackend
from onshape_cad_mcp.budget import Ledger
from onshape_cad_mcp.cache import DocCache
from onshape_cad_mcp.docref import DocRef

REF = DocRef("a"*24, "b"*24, "c"*24)
FEAT = {"type": "sketch", "name": "S1", "status": "OK", "params": {"a": 1}}


def test_second_read_skips_feature_fetch():
    b = FakeBackend()
    c = DocCache(b)
    c.read(REF)
    c.read(REF)
    assert b.calls["get_microversion"] == 2
    assert b.calls["list_features"] == 1


def test_human_edit_forces_refetch():
    b = FakeBackend()
    c = DocCache(b)
    c.read(REF)
    b.simulate_human_edit(REF)
    fl = c.read(REF)
    assert b.calls["list_features"] == 2
    assert fl.microversion == b.get_microversion(REF)


def test_own_write_updates_cache_without_refetch():
    b = FakeBackend()
    c = DocCache(b)
    mv = c.read(REF).microversion
    res = b.add_feature(REF, FEAT, mv)
    c.note_write(REF, res.microversion, res.feature, expected_microversion=mv)
    b.calls.clear()
    fl = c.read(REF)
    assert b.calls["get_microversion"] == 1
    assert b.calls["list_features"] == 0
    assert fl.microversion == res.microversion
    assert fl.features == [res.feature]


def test_note_write_without_entry_is_noop():
    b = FakeBackend()
    c = DocCache(b)
    c.note_write(REF, "mv9", None)
    c.read(REF)
    assert b.calls["list_features"] == 1


def test_note_write_without_feature_drops_entry():
    b = FakeBackend()
    c = DocCache(b)
    c.read(REF)
    c.note_write(REF, "mv5")
    c.read(REF)
    assert b.calls["list_features"] == 2


def test_invalidate_forces_refetch():
    b = FakeBackend()
    c = DocCache(b)
    c.read(REF)
    c.invalidate(REF)
    c.read(REF)
    assert b.calls["list_features"] == 2


def test_returned_value_cannot_corrupt_cache():
    b = FakeBackend()
    c = DocCache(b)
    mv = c.read(REF).microversion
    res = b.add_feature(REF, FEAT, mv)
    c.invalidate(REF)
    fl = c.read(REF)
    fl.features.clear()
    again = c.read(REF)
    assert len(again.features) == 1
    again.features[0].params["a"] = 99
    assert c.read(REF).features[0].params["a"] == 1


def test_ledger_charges_two_then_one(tmp_path):
    led = Ledger(tmp_path / "l.json", 100)
    b = FakeBackend(ledger=led)
    c = DocCache(b)
    c.read(REF)
    assert led._read() == 2
    c.read(REF)
    assert led._read() == 3


def test_note_write_with_mismatched_expected_version_drops_entry():
    b = FakeBackend()
    c = DocCache(b)
    mv = c.read(REF).microversion
    res = b.add_feature(REF, FEAT, mv)
    c.note_write(REF, res.microversion, res.feature, expected_microversion="not-the-cached-one")
    assert c.peek(REF) is None


def test_note_write_without_expected_version_drops_entry():
    b = FakeBackend()
    c = DocCache(b)
    mv = c.read(REF).microversion
    res = b.add_feature(REF, FEAT, mv)
    c.note_write(REF, res.microversion, res.feature)
    assert c.peek(REF) is None
