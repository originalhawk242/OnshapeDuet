import json

from onshape_duet.generic.update import merge

Q = "BTMParameterQuantity-147"
RAW_FILLET = {
    "btType": "BTMFeature-134", "featureType": "fillet", "featureId": "F9", "name": "Round",
    "suppressed": False, "returnAfterSubfeatures": True,
    "parameters": [
        {"btType": "BTMParameterQueryList-148", "parameterId": "entities", "queries": [
            {"btType": "BTMIndividualQuery-138", "deterministicIds": ["JHD", "JHF"]}]},
        {"btType": Q, "parameterId": "radius", "expression": "2 mm", "value": 2.0},
        {"btType": "BTMParameterEnum-145", "parameterId": "style", "value": "A",
         "enumName": "S"},
    ],
}


def _radius(expr):
    return {"btType": Q, "parameterId": "radius", "expression": expr}


def test_merge_preserves_untouched_bytes():
    new, changes = merge(RAW_FILLET, [_radius("3 mm")])
    assert json.dumps({k: v for k, v in new.items() if k != "parameters"}, sort_keys=True) == \
           json.dumps({k: v for k, v in RAW_FILLET.items() if k != "parameters"}, sort_keys=True)
    others = [p for p in new["parameters"] if p["parameterId"] != "radius"]
    assert others == [p for p in RAW_FILLET["parameters"] if p["parameterId"] != "radius"]
    assert [p["parameterId"] for p in new["parameters"]] == ["entities", "radius", "style"]
    assert changes == [{"parameterId": "radius", "before": "2 mm", "after": "3 mm"}]


def test_merge_does_not_mutate_input_and_appends_new_param():
    snapshot = json.dumps(RAW_FILLET, sort_keys=True)
    new, changes = merge(RAW_FILLET, [{"btType": Q, "parameterId": "extra", "expression": "1 mm"}])
    assert json.dumps(RAW_FILLET, sort_keys=True) == snapshot
    assert new["parameters"][-1]["parameterId"] == "extra"
    assert changes == [{"parameterId": "extra", "before": None, "after": "1 mm"}]


def _arr(*depths):
    return {"btType": "BTMParameterArray-2025", "parameterId": "items", "items": [
        {"btType": "BTMArrayParameterItem-1843", "parameters": [
            {"btType": Q, "parameterId": "depth", "expression": d},
            {"btType": Q, "parameterId": "gap", "expression": "1 mm"}]} for d in depths]}


def test_merge_array_changes_listed_per_item():
    raw = {"featureId": "F1", "parameters": [_arr("1 mm", "2 mm")]}
    new, changes = merge(raw, [_arr("1 mm", "9 mm")])
    assert changes == [{"parameterId": "items[1].depth", "before": "2 mm", "after": "9 mm"}]
    assert new["parameters"][0] == _arr("1 mm", "9 mm")


def test_merge_array_grown_lists_new_item_params_with_none_before():
    raw = {"featureId": "F1", "parameters": [_arr("1 mm")]}
    _, changes = merge(raw, [_arr("1 mm", "5 mm")])
    assert changes == [{"parameterId": "items[1].depth", "before": None, "after": "5 mm"},
                       {"parameterId": "items[1].gap", "before": None, "after": "1 mm"}]
