import pytest

from onshape_duet.docref import DocRef, parse_url

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


@pytest.mark.parametrize("url", [
    f"http://cad.onshape.com/documents/{D}/w/{W}/e/{E}",            # not https
    f"https://evil-onshape.com/documents/{D}/w/{W}/e/{E}",           # suffix trick
    f"https://cad.onshape.com.evil.com/documents/{D}/w/{W}/e/{E}",
    f"https://user@cad.onshape.com@evil.com/documents/{D}/w/{W}/e/{E}",
    f"https://notcad.onshape.com/documents/{D}/w/{W}/e/{E}",        # other stack
])
def test_hosts_other_than_the_configured_one_are_refused(url):
    with pytest.raises(ValueError):
        parse_url(url)


def test_host_is_case_insensitive_and_scheme_must_be_https():
    assert parse_url(f"HTTPS://CAD.OnShape.com/documents/{D}/w/{W}/e/{E}") == DocRef(D, W, E)


def test_other_stack_error_points_at_base_url():
    with pytest.raises(ValueError, match="base_url"):
        parse_url(f"https://acme.onshape.com/documents/{D}/w/{W}/e/{E}")


def test_configured_enterprise_host_is_accepted():
    url = f"https://acme.onshape.com/documents/{D}/w/{W}/e/{E}"
    assert parse_url(url, "https://acme.onshape.com") == DocRef(D, W, E)
    with pytest.raises(ValueError):
        parse_url(f"https://cad.onshape.com/documents/{D}/w/{W}/e/{E}", "https://acme.onshape.com")


def test_explicit_port_matching_base_url_is_accepted():
    base = "https://onshape.corp:8443"
    assert parse_url(f"https://onshape.corp:8443/documents/{D}/w/{W}/e/{E}", base) == DocRef(D, W, E)
    with pytest.raises(ValueError, match="port"):
        parse_url(f"https://onshape.corp/documents/{D}/w/{W}/e/{E}", base)
    with pytest.raises(ValueError, match="port"):
        parse_url(f"https://onshape.corp:9443/documents/{D}/w/{W}/e/{E}", base)


def test_default_port_443_may_be_written_explicitly():
    assert parse_url(f"https://cad.onshape.com:443/documents/{D}/w/{W}/e/{E}") == DocRef(D, W, E)
    with pytest.raises(ValueError):
        parse_url(f"https://cad.onshape.com:444/documents/{D}/w/{W}/e/{E}")


def test_http_url_gets_a_specific_message():
    with pytest.raises(ValueError, match="only https URLs are supported"):
        parse_url(f"http://cad.onshape.com/documents/{D}/w/{W}/e/{E}")


@pytest.mark.parametrize("url", [
    f"https://cad.onshape.com./documents/{D}/w/{W}/e/{E}",              # trailing dot
    f"https://cad.onshape.com@evil.com/documents/{D}/w/{W}/e/{E}",
    f"https://cad.onshape.com:443@evil.com/documents/{D}/w/{W}/e/{E}",
    f"https://evilcad.onshape.com/documents/{D}/w/{W}/e/{E}",
])
def test_lookalikes_still_refused(url):
    with pytest.raises(ValueError):
        parse_url(url)
