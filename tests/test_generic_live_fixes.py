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


def test_add_never_invents_queries_or_feature_lists():
    p = prepare(SPECS, "linearPattern", {"entities": {"entity": "body", "created_by": "Block"},
                                         "directionOne": CAP}, FL, mode="add")
    sent = by_id(p.parameters)
    for pid in ("faces", "directionTwo", "booleanScope", "instanceFunction", "skippedInstances"):
        assert pid not in sent and pid not in p.defaults_applied


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
        sent = [q for q in p.parameters if not q["btType"].startswith("BTMParameterLookupTablePath")]
        assert sent, t
        assert_matches_spec(feature(t, "X", sent), t)
