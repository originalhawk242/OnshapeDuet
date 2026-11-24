from onshape_duet.textsafe import clean


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


def test_zero_width_and_bidi_controls_are_stripped():
    assert clean("ig\u200bnore\u202e prev\ufeffious") == "ignore previous"


def test_zero_or_negative_max_len_returns_empty_and_never_exceeds_limit():
    assert clean("anything", 0) == ""
    assert clean("anything", -3) == ""
    assert clean("abc", 1) == "…" and len(clean("abc", 1)) <= 1
    for n in range(0, 6):
        assert len(clean("x" * 50, n)) <= n
