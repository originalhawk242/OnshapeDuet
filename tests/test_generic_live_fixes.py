"""Fixes from the generic live run (docs/live-checklist.md, G3 and G6, 2026-11-24).

G3: Onshape does not fill omitted parameters. A cPlane without cplaneType/oppositeDirection
regenerated with ERROR; an array item without index2 was rejected with HTTP 400
("does not match its feature spec"). A generic add therefore sends every omitted scalar
parameter at its spec default, and a full array item carries every item parameter.
G6: feature lists render as raw ids, Onshape's qCompressed selections render as noise, and a
one-item array renders on the label line."""
import gzip
import json
from pathlib import Path

from onshape_duet.generic.render import param_value, render_feature
from onshape_duet.generic.specs import parse_feature_specs
from onshape_duet.generic.validate import prepare
from onshape_duet.model import Feature, FeatureList

LIVE = json.load(gzip.open(Path(__file__).parent / "fixtures" / "feature_specs.json.gz"))["kept"]
SPECS = {s.type: s for s in parse_feature_specs({"featureSpecs": LIVE})}
FL = FeatureList("mv", [Feature("F0", "newSketch", "Base", "OK"),
                        Feature("F1", "extrude", "Block", "OK")])
CAP = {"entity": "face", "created_by": "Block", "cap": "end"}


def by_id(params):
    return {p["parameterId"]: p for p in params}


def test_add_sends_every_omitted_scalar_default():
    p = prepare(SPECS, "cPlane", {"entities": CAP, "offset": "5 mm"}, FL, mode="add")
    assert not [f for f in p.findings if f.level == "refuse"]
    sent = by_id(p.parameters)
    assert sent["cplaneType"]["value"] == "OFFSET"
    assert sent["oppositeDirection"]["value"] is False  # conditional, still sent
    assert sent["width"]["expression"] == "150 mm"
    assert sent["offset"]["expression"] == "5 mm"  # caller's value, not the default
    assert set(sent) == {"entities", "cplaneType", "offset", "angle", "oppositeDirection",
                         "flipAlignment", "flipNormal", "width", "height"}
    # defaults_applied names exactly what was sent at a default
    assert set(p.defaults_applied) == set(sent) - {"entities", "offset"}
    assert p.defaults_applied["cplaneType"] == "OFFSET"


def test_add_never_invents_selections_or_feature_references():
    p = prepare(SPECS, "linearPattern", {"entities": {"entity": "body", "created_by": "Block"},
                                         "directionOne": CAP}, FL, mode="add")
    sent = by_id(p.parameters)
    for pid in ("faces", "directionTwo", "booleanScope"):
        assert sent[pid]["queries"] == []  # sent, but empty: nothing is picked for the caller
    assert sent["instanceFunction"]["featureIds"] == [] and sent["skippedInstances"]["items"] == []


def test_full_array_item_carries_every_item_parameter():
    p = prepare(SPECS, "linearPattern",
                {"entities": {"entity": "body", "created_by": "Block"}, "directionOne": CAP,
                 "skipInstances": True, "skippedInstances": [{"index1": 1}]}, FL, mode="add")
    assert not [f for f in p.findings if f.level == "refuse"]
    item = by_id(p.parameters)["skippedInstances"]["items"][0]["parameters"]
    assert {q["parameterId"]: q["expression"] for q in item} == {"index1": "1", "index2": "0"}


def test_update_sends_only_named_parameters():
    cur = {"parameters": [{"btType": "BTMParameterQuantity-147", "parameterId": "offset",
                           "expression": "5 mm"}]}
    p = prepare(SPECS, "cPlane", {"offset": "8 mm"}, FL, mode="update", self_index=1,
                current_raw=cur)
    assert [q["parameterId"] for q in p.parameters] == ["offset"] and p.defaults_applied == {}


def test_update_list_form_fills_item_defaults_patch_form_does_not():
    cur_arr = {"btType": "BTMParameterArray-2025", "parameterId": "skippedInstances", "items": [
        {"btType": "BTMArrayParameterItem-1843", "parameters": [
            {"btType": "BTMParameterQuantity-147", "parameterId": "index1", "expression": "1"}]}]}
    cur = {"parameters": [cur_arr]}
    listed = prepare(SPECS, "linearPattern", {"skippedInstances": [{"index1": 2}]}, FL,
                     mode="update", self_index=1, current_raw=cur)
    ids = [q["parameterId"] for q in listed.parameters[0]["items"][0]["parameters"]]
    assert sorted(ids) == ["index1", "index2"]
    patched = prepare(SPECS, "linearPattern", {"skippedInstances": {"items": {"0": {"index1": 2}}}},
                      FL, mode="update", self_index=1, current_raw=cur)
    ids = [q["parameterId"] for q in patched.parameters[0]["items"][0]["parameters"]]
    assert ids == ["index1"]  # a patch changes what it names and keeps the rest as read


def test_feature_list_renders_names():
    f = Feature("F9", "mirror", "Mirror", "OK", raw={"parameters": [
        {"btType": "BTMParameterFeatureList-1749", "parameterId": "instanceFunction",
         "featureIds": ["F1", "Fgone"]}]})
    out = render_feature(f, None, FL)
    assert "instanceFunction: Block, Fgone (not in the feature list)" in out


def test_compressed_selection_is_summarised():
    blob = 'query=qCompressed(1.0,"&1b9$eJx9UE1zgjAQ' + "x" * 400 + '");'
    v = param_value({"btType": "BTMParameterQueryList-148",
                     "queries": [{"btType": "BTMIndividualQuery-138", "queryString": blob,
                                  "deterministicIds": ["JHD"]}]})
    assert v == "selection compressed by Onshape (not readable here; change it in Onshape)"


def test_one_item_array_renders_on_its_own_line():
    f = Feature("F4", "linearPattern", "Copies", "OK", raw={"parameters": [
        {"btType": "BTMParameterArray-2025", "parameterId": "skippedInstances", "items": [
            {"btType": "BTMArrayParameterItem-1843", "parameters": [
                {"btType": "BTMParameterQuantity-147", "parameterId": "index1", "expression": "2"}]}]},
        {"btType": "BTMParameterArray-2025", "parameterId": "empty", "items": []}]})
    lines = render_feature(f, None).splitlines()
    assert lines[1:] == ["  skippedInstances:", "    [0] index1=2", "  empty: (no items)"]


def test_add_sends_lookup_table_defaults():
    # Peer finding (Task 15): hole.fs requires isoHoleTableEx is LookupTablePath for a METRIC
    # SIMPLE V2+ hole; an omitted table fails a precondition (REGEN_ERROR).
    hole = SPECS["hole"]
    assert hole.param("isoHoleTableEx").kind == "unsupported"  # callers still cannot set it
    assert dict(hole.param("isoHoleTableEx").lookup_default) == {"size": "5", "holeType": "Drilled"}
    p = prepare(SPECS, "hole", {}, FL, mode="add")
    sent = by_id(p.parameters)["isoHoleTableEx"]
    assert sent == {"btType": "BTMParameterLookupTablePath-1419", "parameterId": "isoHoleTableEx",
                    "namespace": "", "value": {"size": "5", "holeType": "Drilled"}}
    assert p.defaults_applied["isoHoleTableEx"] == {"size": "5", "holeType": "Drilled"}


def test_lookup_default_only_from_plain_string_mappings():
    from onshape_duet.generic.specs import parse_feature_specs as parse
    spec = {"featureSpecs": [{"btType": "BTFeatureSpec-129", "featureType": "t", "featureTypeName": "T",
                              "parameters": [
        {"btType": "BTParameterSpecLookupTablePath-761", "parameterId": "ok",
         "defaultValue": {"btType": "BTMParameterLookupTablePath-1419", "value": {"a": "1"}}},
        {"btType": "BTParameterSpecLookupTablePath-761", "parameterId": "bad",
         "defaultValue": {"btType": "BTMParameterLookupTablePath-1419", "value": {"a": {"x": 1}}}},
        {"btType": "BTParameterSpecLookupTablePath-761", "parameterId": "none"}]}]}
    t = parse(spec)[0]
    assert dict(t.param("ok").lookup_default) == {"a": "1"}
    assert t.param("bad").lookup_default == () and t.param("none").lookup_default == ()


def test_every_sent_default_matches_onshapes_featurespec():
    from onshape_duet.features.params import feature
    from ui_fixture import assert_matches_spec
    for t in SPECS:
        if t in ("newSketch", "assignVariable"):
            continue
        p = prepare(SPECS, t, {}, FL, mode="add")
        assert p.parameters, t
        assert_matches_spec(feature(t, "X", p.parameters), t)


# ---- review round 1 + peer notes -------------------------------------------------------------

def test_omitted_queries_feature_lists_arrays_and_empty_strings_are_sent_empty():
    # hole.fs preconditions need e.g. initEntities is Query and featureName is string
    p = prepare(SPECS, "linearPattern", {"entities": {"entity": "body", "created_by": "Block"},
                                         "directionOne": CAP}, FL, mode="add")
    sent = by_id(p.parameters)
    assert sent["faces"] == {"btType": "BTMParameterQueryList-148", "parameterId": "faces", "queries": []}
    assert sent["instanceFunction"]["featureIds"] == [] and sent["skippedInstances"]["items"] == []
    assert p.defaults_applied["faces"] == [] and p.defaults_applied["skippedInstances"] == []
    h = by_id(prepare(SPECS, "hole", {}, FL, mode="add").parameters)
    assert h["featureName"] == {"btType": "BTMParameterString-149", "parameterId": "featureName", "value": ""}
    assert h["initEntities"]["queries"] == []


def test_every_spec_parameter_is_sent_on_add():
    for t, spec in SPECS.items():
        if t in ("newSketch", "assignVariable"):
            continue
        sent = by_id(prepare(SPECS, t, {}, FL, mode="add").parameters)
        assert set(sent) == {p.id for p in spec.params}, t


def test_odd_onshape_strings_are_not_sent_and_shown_keys_are_cleaned():
    from onshape_duet.generic.specs import parse_feature_specs as parse

    def lookup(pid, val):
        return {"btType": "BTParameterSpecLookupTablePath-761", "parameterId": pid,
                "defaultValue": {"btType": "BTMParameterLookupTablePath-1419", "value": val}}
    t = parse({"featureSpecs": [{"btType": "BTFeatureSpec-129", "featureType": "t", "featureTypeName": "T",
                                  "parameters": [
        lookup("evil", {"size": "5\u202e\x1b[31m"}), lookup("long", {"k": "x" * 500}),
        {"btType": "BTParameterSpecBoolean-170", "parameterId": "b\x1b[31mad",
         "defaultValue": {"btType": "BTMParameterBoolean-144", "value": True}}]}]})[0]
    assert t.param("evil").lookup_default == () and t.param("long").lookup_default == ()
    p = prepare({"t": t}, "t", {}, FL, mode="add")
    assert all("\x1b" not in k for k in p.defaults_applied) and list(p.defaults_applied.values()) == [True]


def test_hidden_param_warns_when_the_controller_comes_from_a_default():
    from onshape_duet.generic.lint import lint_generic
    params = {"entities": CAP, "angle": "30 deg"}
    p = prepare(SPECS, "cPlane", params, FL, mode="add")
    f = lint_generic("cPlane", "Tilt", params, SPECS["cPlane"], FL, mode="add",
                     defaults=p.defaults_applied)
    hidden = [x for x in f if x.code == "HIDDEN_PARAM"]
    assert len(hidden) == 1 and "angle" in hidden[0].message and "default" in hidden[0].message
    assert "cplaneType" in hidden[0].message


def test_refused_parameter_gets_no_default_substituted():
    p = prepare(SPECS, "cPlane", {"entities": CAP, "cplaneType": "offset"}, FL, mode="add")
    assert "BAD_VALUE" in {x.code for x in p.findings}
    assert "cplaneType" not in by_id(p.parameters) and "cplaneType" not in p.defaults_applied


def test_unbuildable_default_is_reported():
    from onshape_duet.generic.specs import FeatureSpec, ParamSpec
    spec = FeatureSpec("t", "T", (ParamSpec("mode", "Mode", "enum", default="GONE", options=("A",),
                                            enum_name="M"),))
    p = prepare({"t": spec}, "t", {}, FL, mode="add")
    assert [(x.level, x.code) for x in p.findings] == [("warn", "DEFAULT_NOT_SENT")]
    assert p.parameters == [] and p.defaults_applied == {}


def test_item_defaults_are_listed():
    p = prepare(SPECS, "linearPattern",
                {"entities": {"entity": "body", "created_by": "Block"}, "directionOne": CAP,
                 "skippedInstances": [{"index1": 1}, {"index2": 1}]}, FL, mode="add")
    assert p.defaults_applied["skippedInstances[0].index2"] == "0"
    assert p.defaults_applied["skippedInstances[1].index1"] == "1"
    assert "skippedInstances" not in p.defaults_applied


def test_feature_list_names_inside_arrays_and_compressed_collapsed():
    f = Feature("F9", "x", "X", "OK", raw={"parameters": [
        {"btType": "BTMParameterArray-2025", "parameterId": "arr", "items": [
            {"btType": "BTMArrayParameterItem-1843", "parameters": [
                {"btType": "BTMParameterFeatureList-1749", "parameterId": "feats", "featureIds": ["F1"]}]}]},
        {"btType": "BTMParameterQueryList-148", "parameterId": "q", "queries": [
            {"queryString": 'query=qCompressed(1.0,"a");'}, {"queryString": 'query=qCompressed(1.0,"b");'},
            {"queryString": "query=qEverything(EntityType.FACE);"}]}]})
    out = render_feature(f, None, FL)
    assert "[0] feats=Block" in out
    assert "q: query=qEverything(EntityType.FACE);; 2 selections compressed by Onshape" in out


def _tool_ctx(tmp_path):
    from onshape_duet.backends.fake import FakeBackend
    from onshape_duet.budget import Ledger
    from onshape_duet.cache import DocCache
    from onshape_duet.config import Config
    from onshape_duet.tools import Context
    cfg = Config(budget=50, ledger_path=tmp_path / "l.json")
    ledger = Ledger(cfg.ledger_path, 50)
    backend = FakeBackend(ledger)
    backend.specs_json = {"featureSpecs": LIVE}
    return Context(backend, DocCache(backend), ledger, cfg), backend


def test_apply_posts_exactly_what_the_dry_run_listed(tmp_path):
    from onshape_duet.docref import DocRef
    from onshape_duet.feature_tools import cad_extrude
    from onshape_duet.tools import cad_feature_add, cad_read, cad_sketch
    ref = DocRef("a" * 24, "b" * 24, "c" * 24)
    ctx, fake = _tool_ctx(tmp_path)
    cad_read(ctx, ref)
    sk = {"plane": "Top", "entities": [{"id": "r", "type": "rectangle", "corner1": [0, 0], "corner2": [60, 40]}],
          "constraints": [{"type": "ground", "refs": ["r.bottom.start"]}],
          "dimensions": [{"type": "length", "ref": "r.bottom", "value": 60},
                         {"type": "length", "ref": "r.left", "value": 40}]}
    assert cad_sketch(ctx, ref, "Base", sk, apply=True)["applied"]
    assert cad_extrude(ctx, ref, "Block", "Base", "10 mm", apply=True)["applied"]
    params = {"entities": CAP, "offset": "5 mm"}
    dry = cad_feature_add(ctx, ref, "cPlane", "OffPlane", params)
    out = cad_feature_add(ctx, ref, "cPlane", "OffPlane", params, apply=True)
    assert out["applied"] is True
    posted = {p["parameterId"] for p in fake.last_feature_json["parameters"]}
    assert posted == set(params) | set(dry["defaults_applied"])
    assert dry["defaults_applied"]["cplaneType"] == "OFFSET"


def test_cad_inspect_shows_feature_names(tmp_path):
    from onshape_duet.docref import DocRef
    from onshape_duet.tools import cad_inspect, cad_read
    ref = DocRef("a" * 24, "b" * 24, "c" * 24)
    ctx, fake = _tool_ctx(tmp_path)
    fake.add_feature(ref, {"btType": "BTMFeature-134", "featureType": "extrude", "name": "Boss",
                           "parameters": []}, fake.get_microversion(ref))
    fake.add_feature(ref, {"btType": "BTMFeature-134", "featureType": "mirror", "name": "Mirror",
                           "parameters": [{"btType": "BTMParameterFeatureList-1749",
                                           "parameterId": "instanceFunction", "featureIds": ["F1"]}]},
                     fake.get_microversion(ref))
    cad_read(ctx, ref)
    out = cad_inspect(ctx, ref, "Mirror")
    assert "instanceFunction: Boss" in out["feature"]


# ---- review round 2 ---------------------------------------------------------------------------

def test_omitted_selection_that_the_feature_shows_is_flagged():
    p = prepare(SPECS, "extrude", {"depth": "10 mm"}, FL, mode="add")
    warn = [f for f in p.findings if f.code == "EMPTY_SELECTION"]
    assert len(warn) == 1 and warn[0].level == "warn" and "entities" in warn[0].message


def test_empty_selection_not_flagged_when_its_condition_is_off_or_it_was_given():
    p = prepare(SPECS, "linearPattern", {"entities": {"entity": "body", "created_by": "Block"},
                                         "directionOne": CAP}, FL, mode="add")
    msg = " ".join(f.message for f in p.findings if f.code == "EMPTY_SELECTION")
    assert "directionTwo" not in msg  # only when hasSecondDir == true; default false
    assert "entities" not in msg and "directionOne" not in msg  # given


def test_empty_selection_renders_as_none():
    assert param_value({"btType": "BTMParameterQueryList-148", "queries": []}) == "(none)"
    assert param_value({"btType": "BTMParameterFeatureList-1749", "featureIds": []}, FL) == "(none)"


def test_parameter_without_any_default_is_reported():
    from onshape_duet.generic.specs import FeatureSpec, ParamSpec
    spec = FeatureSpec("t", "T", (ParamSpec("n", "N", "quantity", quantity_type="REAL"),
                                  ParamSpec("w", "W", "unsupported")))
    p = prepare({"t": spec}, "t", {}, FL, mode="add")
    notes = [f for f in p.findings if f.code == "DEFAULT_NOT_SENT"]
    assert {m for f in notes for m in ("n", "w") if f.message.startswith(m + " ")} == {"n", "w"}
    assert p.parameters == []


def test_empty_selection_splits_known_from_unchecked():
    p = prepare(SPECS, "cPlane", {"offset": "5 mm"}, FL, mode="add")  # entities: always shown
    msg = next(f.message for f in p.findings if f.code == "EMPTY_SELECTION")
    assert "shown in this feature: entities" in msg and "not checked" not in msg
    # extrude's entities has a compound (Logical) condition: listed as not checked, still named
    p = prepare(SPECS, "extrude", {"depth": "10 mm"}, FL, mode="add")
    msg = next(f.message for f in p.findings if f.code == "EMPTY_SELECTION")
    assert "not checked here: entities," in msg
    assert param_value({"btType": "BTMParameterFeatureList-1749", "featureIds": []}) == "(none)"


# --- follow-up review M2: lookup tables render as their value, not as a btType ---

def test_lookup_table_renders_its_value():
    p = {"btType": "BTMParameterLookupTablePath-1419", "parameterId": "isoHoleTableEx",
         "namespace": "", "value": {"size": "5", "holeType": "Drilled"}}
    assert param_value(p) == "{size: 5, holeType: Drilled}"


def test_lookup_table_value_is_cleaned_and_truncated():
    p = {"btType": "BTMParameterLookupTablePath-1419", "parameterId": "t",
         "value": {"size\n": "a\x1bb", "k": "x" * 500, "n": 3}}
    out = param_value(p)
    assert "\n" not in out and "\x1b" not in out and len(out) <= 200
    assert out.startswith("{size")
    assert param_value({"btType": "BTMParameterLookupTablePath-1419", "value": None}) == "{}"
