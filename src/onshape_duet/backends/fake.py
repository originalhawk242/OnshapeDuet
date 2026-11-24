"""In-memory backend for tests."""
from __future__ import annotations

import copy
from collections import Counter
from collections.abc import MutableMapping

from ..budget import Ledger
from ..docref import DocRef
from ..model import Feature, FeatureList, Variable, WriteResult, flatten_params
from ..variables import (VARIABLE_FEATURE_TYPE, find_variable, make_variable_feature,
                         variables_from_features)
from .base import StaleVersion


class _Variables(MutableMapping):
    """name -> expression view over the document's variable (assign) features, so
    variables live in the feature list exactly as they do in Onshape."""

    def __init__(self, doc: "_Doc") -> None:
        self._doc = doc

    def _fl(self) -> FeatureList:
        return FeatureList("", self._doc.features)

    def __getitem__(self, name: str) -> str:
        for v in variables_from_features(self._fl()):
            if v.name == name:
                return v.expression
        raise KeyError(name)

    def __setitem__(self, name: str, expression: str) -> None:
        feats = self._doc.features
        existing = find_variable(self._fl(), name)
        new = make_variable_feature(existing.id if existing else self._doc.new_id(), name, expression)
        if existing is None:
            feats.append(new)
        else:
            feats[feats.index(existing)] = new

    def __delitem__(self, name: str) -> None:
        existing = find_variable(self._fl(), name)
        if existing is None:
            raise KeyError(name)
        self._doc.features.remove(existing)

    def __iter__(self):
        return iter([v.name for v in variables_from_features(self._fl())])

    def __len__(self) -> int:
        return sum(1 for f in self._doc.features if f.type == VARIABLE_FEATURE_TYPE)


class _Doc:
    def __init__(self) -> None:
        self.version = 0
        self.features: list[Feature] = []
        self.variables = _Variables(self)
        self._counter = 0

    def new_id(self) -> str:
        """A feature id that never repeats, even after features are deleted."""
        self._counter += 1
        return f"F{self._counter}"


class FakeBackend:
    def __init__(self, ledger: Ledger | None = None) -> None:
        self.ledger = ledger
        self.calls: Counter[str] = Counter()
        self._docs: dict[DocRef, _Doc] = {}
        self.next_status: str | None = None

    def _doc(self, ref: DocRef) -> _Doc:
        return self._docs.setdefault(ref, _Doc())

    def _enter(self, method: str) -> None:
        if self.ledger is not None:
            self.ledger.charge(1, method)
        self.calls[method] += 1

    def _mv(self, doc: _Doc) -> str:
        return f"mv{doc.version}"

    def _check(self, doc: _Doc, expected: str) -> None:
        if expected != self._mv(doc):
            raise StaleVersion(f"expected {expected}, current {self._mv(doc)}")

    def simulate_human_edit(self, ref: DocRef) -> None:
        self._doc(ref).version += 1

    def get_microversion(self, ref: DocRef) -> str:
        self._enter("get_microversion")
        return self._mv(self._doc(ref))

    def list_features(self, ref: DocRef) -> FeatureList:
        self._enter("list_features")
        d = self._doc(ref)
        return FeatureList(self._mv(d), copy.deepcopy(d.features))

    def get_variables(self, ref: DocRef) -> list[Variable]:
        self._enter("get_variables")
        return variables_from_features(FeatureList("", self._doc(ref).features))

    def add_feature(self, ref: DocRef, feature_json: dict, expected_microversion: str) -> WriteResult:
        self._enter("add_feature")
        d = self._doc(ref)
        self._check(d, expected_microversion)
        parameters = feature_json.get("parameters")
        status = self.next_status or feature_json.get("status", "OK")
        self.next_status = None
        new_id = d.new_id()
        feat = Feature(
            id=new_id,
            type=feature_json.get("featureType") or feature_json.get("type") or feature_json.get("btType") or "",
            name=feature_json.get("name", ""),
            status=status,
            params=(flatten_params(parameters) if isinstance(parameters, list)
                    else copy.deepcopy(feature_json.get("params", {}))),
            raw=({**copy.deepcopy(feature_json), "featureId": new_id}
                 if isinstance(parameters, list) else {}),
        )
        d.features.append(feat)
        d.version += 1
        return WriteResult(copy.deepcopy(feat), self._mv(d))

    def set_variable(self, ref: DocRef, name: str, expression: str, expected_microversion: str,
                     feature_id: str | None = None) -> WriteResult:
        self._enter("set_variable")
        d = self._doc(ref)
        self._check(d, expected_microversion)
        d.variables[name] = expression
        d.version += 1
        feat = copy.deepcopy(find_variable(FeatureList("", d.features), name))
        return WriteResult(feat, self._mv(d))
