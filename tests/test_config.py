from onshape_cad_mcp.config import load_config


def test_default_budget_is_zero():
    assert load_config(None).budget == 0


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml").budget == 0


def test_toml_sets_budget(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("budget = 40\n")
    assert load_config(p).budget == 40


import pytest


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
