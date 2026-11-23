from onshape_cad_mcp.textsafe import clean


def test_collapses_whitespace_and_controls():
    assert clean("a\nb\r\nc\td\x00e\x7ff g h  i") == "a b c d e f g h i"


def test_strips():
    assert clean("  \n x \n ") == "x"


def test_str_of_non_string():
    assert clean(12) == "12"
    assert clean(None) == "None"


def test_truncates_with_ellipsis():
    out = clean("x" * 200, max_len=10)
    assert out == "x" * 9 + "…"
    assert clean("x" * 10, max_len=10) == "x" * 10
    assert len(clean("y" * 500)) == 80
