from onshape_duet.backends.fake import FakeBackend
from onshape_duet.backends.onshape import _parse_feature
from onshape_duet.docref import DocRef
from onshape_duet.model import Feature

REF = DocRef("a"*24, "b"*24, "c"*24)


def test_parse_feature_keeps_raw():
    f = {"featureId": "F1", "featureType": "extrude", "name": "Base",
         "parameters": [{"btType": "BTMParameterQuantity-147", "parameterId": "depth", "expression": "10 mm"}]}
    feat = _parse_feature(f, {})
    assert feat.raw == f and feat.raw is not f      # a copy, not the response object
    assert feat.params == {"depth": "10 mm"}


def test_feature_default_raw_is_empty():
    assert Feature("F1", "extrude", "x", "OK").raw == {}


def test_fake_add_feature_keeps_raw():
    fb = FakeBackend()
    fj = {"featureType": "extrude", "name": "B", "parameters": []}
    r = fb.add_feature(REF, fj, fb.get_microversion(REF))
    assert r.feature.raw["featureType"] == "extrude" and r.feature.raw["featureId"] == r.feature.id
