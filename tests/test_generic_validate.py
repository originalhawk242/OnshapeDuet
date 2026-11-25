import json
from pathlib import Path

from onshape_duet.generic.lint import lint_generic
from onshape_duet.generic.specs import parse_feature_specs
from onshape_duet.generic.validate import prepare
from onshape_duet.model import Feature, FeatureList
from onshape_duet.variables import make_variable_feature

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "featurespecs_UNVERIFIED.json").read_text())
SPECS = {s.type: s for s in parse_feature_specs(FIXTURE)}
FL = FeatureList("mv", [
    Feature("F0", "newSketch", "Base", "OK"),
    Feature("F1", "extrude", "Pad", "OK"),
    Feature("F2", "fillet", "Round", "OK"),
    Feature("F3", "fillet", "Round2", "OK"),
])


def codes(p, level="refuse"):
    return {f.code for f in p.findings if f.level == level}


def test_collects_all_refusals():
    p = prepare(SPECS, "extrude", {"depht": "1 mm", "endBound": "blind"}, FL, mode="add")
    assert codes(p) == {"UNKNOWN_PARAM", "BAD_VALUE"}
    assert "depth" in next(f.message for f in p.findings if f.code == "UNKNOWN_PARAM")


def test_typed_tool_required_for_sketch_and_variable():
    for t, tool in (("newSketch", "cad_sketch"), ("assignVariable", "cad_variable")):
        p = prepare(SPECS, t, {}, FL, mode="add")
        assert codes(p) == {"TYPED_TOOL_REQUIRED"} and tool in p.findings[0].message
        assert p.parameters == []


def test_custom_and_unknown_type():
    p = prepare(SPECS, "e123::m456", {}, FL, mode="add")
    assert codes(p) == {"CUSTOM_FEATURE"} and p.parameters == []
    p = prepare(SPECS, "extrud", {}, FL, mode="add")
    assert codes(p) == {"UNKNOWN_FEATURE_TYPE"} and "extrude" in p.findings[0].message


def test_defaults_applied_excludes_conditional():
    p = prepare(SPECS, "extrude", {"endBound": "BLIND"}, FL, mode="add")
    assert not p.findings
    assert "depth" not in p.defaults_applied and p.defaults_applied["oppositeDirection"] is False
    assert "endBound" not in p.defaults_applied and "entities" not in p.defaults_applied
    u = prepare(SPECS, "extrude", {"endBound": "BLIND"}, FL, mode="update", current_raw={})
    assert u.defaults_applied == {}


def test_update_uses_current_raw_parameter():
    cur = {"parameters": ["junk", {"parameterId": "other"}, {"parameterId": "depth"}]}
    p = prepare(SPECS, "extrude", {"depth": "2 mm"}, FL, mode="update", self_index=1, current_raw=cur)
    assert not p.findings and p.parameters[0]["expression"] == "2 mm"


def test_self_reference_on_update():
    def run(name, idx):
        return prepare(SPECS, "linearPattern", {"instanceFunction": [name]}, FL,
                       mode="update", self_index=idx)
    assert codes(run("Round", 2)) == {"SELF_REFERENCE"}   # itself
    assert codes(run("Round2", 2)) == {"SELF_REFERENCE"}  # later
    assert not run("Pad", 2).findings                     # earlier


def test_lint_default_name_magic_unknown_var_typed_available():
    fl = FeatureList("mv", [make_variable_feature("V1", "w", "5 mm")])
    ws = {f.code for f in lint_generic("extrude", "Extrude 3", {"depth": "10 mm"}, SPECS["extrude"], fl, mode="add")}
    assert ws == {"DEFAULT_NAME", "TYPED_TOOL_AVAILABLE"}
    f = lint_generic("cPlane", "Plane", {"offset": "#nope + #w"}, SPECS["cPlane"], fl, mode="update")
    assert [x.code for x in f] == ["UNKNOWN_VARIABLE"] and "#nope" in f[0].message
    spec = SPECS["holeLike"]
    f = lint_generic("holeLike", "H", {"holes": [{"diameter": "10 mm"}, {"diameter": "10mm"}]},
                     spec, fl, mode="add")
    assert [x.code for x in f] == ["MAGIC_NUMBER"]
    f = lint_generic("holeLike", "H", {"holes": {"items": {"0": {"diameter": "10 mm"},
                                                           "1": {"diameter": "10 MM"}}}},
                     spec, fl, mode="update")
    assert [x.code for x in f] == ["MAGIC_NUMBER"]
    assert [x.code for x in lint_generic("fillet", "F", {"radius": "#w"}, SPECS["fillet"], fl, mode="update")] == []


def test_hidden_param_warn():
    f = lint_generic("extrude", "Pad", {"endBound": "THROUGH_ALL", "depth": "5 mm"},
                     SPECS["extrude"], FL, mode="update")
    assert [x.code for x in f] == ["HIDDEN_PARAM"] and "not evaluated exactly" in f[0].message
    assert not lint_generic("extrude", "Pad", {"endBound": "BLIND", "depth": "5 mm"},
                            SPECS["extrude"], FL, mode="update")
    assert not lint_generic("extrude", "Pad", {"depth": "5 mm"}, SPECS["extrude"], FL, mode="update")


def test_params_not_a_dict():
    for bad in ([("depth", "1 mm")], {1: "x"}):
        p = prepare(SPECS, "extrude", bad, FL, mode="add")
        assert [f.code for f in p.findings] == ["BAD_VALUE"]


def test_typed_tool_available_names_only_registered_tools(tmp_path):
    import asyncio
    from onshape_duet import server
    from onshape_duet.generic.validate import TYPED_TOOL_AVAILABLE
    from onshape_duet.backends.fake import FakeBackend
    from onshape_duet.budget import Ledger
    from onshape_duet.cache import DocCache
    from onshape_duet.config import Config
    from onshape_duet.tools import Context
    fb = FakeBackend()
    ctx = Context(fb, DocCache(fb), Ledger(tmp_path / "l.json", 10), Config(budget=10))
    names = {t.name for t in asyncio.run(server.build_server(ctx).list_tools())}
    assert set(TYPED_TOOL_AVAILABLE.values()) <= names
