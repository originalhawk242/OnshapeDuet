"""The ONE place that maps Onshape variable ("assign") features to Variables.

UNVERIFIED against live Onshape until Task 12 (see docs/live-checklist.md): the
variable name is assumed to be the feature name "#<name>" and the expression the
flattened `value` parameter. If the real model differs, change only this module
(and the matching write body in backends/onshape.py).
"""
from __future__ import annotations

from .model import Feature, FeatureList, Variable

VARIABLE_FEATURE_TYPE = "assign"
VARIABLE_NAME_PREFIX = "#"  # assign features are named "#<var>"
VARIABLE_EXPR_PARAM = "value"


def variable_name(feature: Feature) -> str:
    return feature.name.removeprefix(VARIABLE_NAME_PREFIX)


def _expression(feature: Feature) -> str:
    val = feature.params.get(VARIABLE_EXPR_PARAM, "")
    if isinstance(val, bool) or not isinstance(val, (str, int, float)):
        return ""
    return str(val)


def variables_from_features(fl: FeatureList) -> list[Variable]:
    return [Variable(variable_name(f), _expression(f))
            for f in fl.features if f.type == VARIABLE_FEATURE_TYPE]


def find_variable(fl: FeatureList, name: str) -> Feature | None:
    """The assign feature that defines variable `name`, if any."""
    return next((f for f in fl.features
                 if f.type == VARIABLE_FEATURE_TYPE and variable_name(f) == name), None)


def make_variable_feature(feature_id: str, name: str, expression: str) -> Feature:
    """A variable feature as `variables_from_features` expects it (used by the fake backend)."""
    return Feature(feature_id, VARIABLE_FEATURE_TYPE, VARIABLE_NAME_PREFIX + name, "OK",
                   {VARIABLE_EXPR_PARAM: expression})
