"""The ONE place that maps Onshape variable ("assign") features to Variables.

VERIFIED live: a Part Studio variable is an `assignVariable` feature whose feature
name is the default template "#name = #value"; the variable name is the string
parameter `name` and the expression is the quantity parameter `lengthValue`.
Only length variables are supported. The matching write body is in
backends/onshape.py.
"""
from __future__ import annotations

from .model import Feature, FeatureList, Variable

VARIABLE_FEATURE_TYPE = "assignVariable"
VARIABLE_FEATURE_NAME = "#name = #value"  # Onshape's default feature name template
VARIABLE_NAME_PARAM = "name"
VARIABLE_EXPR_PARAM = "lengthValue"


def variable_name(feature: Feature) -> str:
    val = feature.params.get(VARIABLE_NAME_PARAM, "")
    return val if isinstance(val, str) else ""


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
    return Feature(feature_id, VARIABLE_FEATURE_TYPE, VARIABLE_FEATURE_NAME, "OK",
                   {VARIABLE_NAME_PARAM: name, VARIABLE_EXPR_PARAM: expression})
