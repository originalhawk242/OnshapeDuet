import pytest

from onshape_cad_mcp.docref import DocRef, parse_url

D, W, E = "a" * 24, "b" * 24, "c" * 24


def test_parse_url():
    assert parse_url(f"https://cad.onshape.com/documents/{D}/w/{W}/e/{E}") == DocRef(D, W, E)


def test_version_url_rejected_clearly():
    with pytest.raises(ValueError, match="version URLs"):
        parse_url(f"https://cad.onshape.com/documents/{D}/v/{W}/e/{E}")


def test_parse_bad_url_raises():
    with pytest.raises(ValueError):
        parse_url("https://example.com")


@pytest.mark.parametrize("bad", ["d1", "../" + "a" * 21, "a" * 23 + "/", "a" * 23 + "?", "%2f" + "a" * 21, "A" * 24, ""])
def test_bad_ids_rejected(bad):
    with pytest.raises(ValueError):
        DocRef(bad, W, E)
    with pytest.raises(ValueError):
        DocRef(D, bad, E)
    with pytest.raises(ValueError):
        DocRef(D, W, bad)


def test_url_with_bad_id_rejected():
    with pytest.raises(ValueError):
        parse_url(f"https://cad.onshape.com/documents/d1/w/{W}/e/{E}")
