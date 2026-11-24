import pytest

from onshape_cad_mcp.config import load_config


def test_default_budget_is_zero():
    assert load_config(None).budget == 0


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml").budget == 0


def test_toml_sets_budget(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("budget = 40\n")
    assert load_config(p).budget == 40


@pytest.mark.parametrize("text", ['budget = "5"', "budget = -1", "budget = true", "budjet = 5", "budget = ["])
def test_bad_config_raises_clear_error(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    with pytest.raises(ValueError) as e:
        load_config(p)
    assert str(p) in str(e.value)
    if "budjet" in text:
        assert "budjet" in str(e.value)
    elif "[" not in text:
        assert "budget" in str(e.value)


@pytest.mark.parametrize("line,key", [
    ('base_url = "http://cad.onshape.com"', "base_url"),
    ('base_url = "https://cad.onshape.com/api"', "base_url"),
    ('base_url = "https://cad.onshape.com?x=1"', "base_url"),
    ("base_url = 5", "base_url"),
    ("key_env = 5", "key_env"),
    ('secret_env = ""', "secret_env"),
    ("ledger_path = 3", "ledger_path"),
    ("ledger_path = [1]", "ledger_path"),
])
def test_bad_typed_keys_raise_valueerror_naming_the_key(tmp_path, line, key):
    p = tmp_path / "c.toml"
    p.write_text(line)
    with pytest.raises(ValueError) as e:
        load_config(p)
    assert key in str(e.value) and str(p) in str(e.value)


def test_good_base_url_variants(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('base_url = "https://onshape.example.com/"\nkey_env = "K"\n')
    cfg = load_config(p)
    assert cfg.base_url == "https://onshape.example.com" and cfg.key_env == "K"
