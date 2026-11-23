import pytest

from onshape_cad_mcp.docref import DocRef, parse_url


def test_parse_url():
    assert parse_url("https://cad.onshape.com/documents/d1/w/w2/e/e3") == DocRef("d1", "w2", "e3")


def test_parse_version_url():
    assert parse_url("https://cad.onshape.com/documents/d1/v/v2/e/e3") == DocRef("d1", "v2", "e3")


def test_parse_bad_url_raises():
    with pytest.raises(ValueError):
        parse_url("https://example.com")
