"""In-memory backend for tests."""
from __future__ import annotations

from collections import Counter

from ..budget import Ledger
from ..docref import DocRef
from ..model import Feature, FeatureList, Variable, WriteResult
from .base import StaleVersion


class _Doc:
    def __init__(self) -> None:
        self.version = 0
        self.features: list[Feature] = []
        self.variables: dict[str, str] = {}


class FakeBackend:
    def __init__(self, ledger: Ledger | None = None) -> None:
        self.ledger = ledger
        self.calls: Counter[str] = Counter()
        self._docs: dict[DocRef, _Doc] = {}

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
        return FeatureList(self._mv(d), list(d.features))

    def get_variables(self, ref: DocRef) -> list[Variable]:
        self._enter("get_variables")
        return [Variable(n, e) for n, e in self._doc(ref).variables.items()]

    def add_feature(self, ref: DocRef, feature_json: dict, expected_microversion: str) -> WriteResult:
        self._enter("add_feature")
        d = self._doc(ref)
        self._check(d, expected_microversion)
        feat = Feature(
            id=f"F{len(d.features) + 1}",
            type=feature_json.get("type", ""),
            name=feature_json.get("name", ""),
            status=feature_json.get("status", "OK"),
            params=dict(feature_json.get("params", {})),
        )
        d.features.append(feat)
        d.version += 1
        return WriteResult(feat, self._mv(d))

    def set_variable(self, ref: DocRef, name: str, expression: str, expected_microversion: str) -> str:
        self._enter("set_variable")
        d = self._doc(ref)
        self._check(d, expected_microversion)
        d.variables[name] = expression
        d.version += 1
        return self._mv(d)
