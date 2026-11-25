"""Backend protocol: the only surface the rest of the server talks to."""
from __future__ import annotations

from typing import Protocol

from ..docref import DocRef
from ..generic.specs import FeatureSpec
from ..model import FeatureList, Variable, WriteResult


class StaleVersion(Exception):
    """The document changed since the caller last read it.

    may_have_landed is True when Onshape reported microversionSkew. Onshape does
    not actually reject such writes (verified live), so the write most likely
    landed: the caller must re-read before doing anything else.
    """

    def __init__(self, message: str = "", may_have_landed: bool = False) -> None:
        super().__init__(message)
        self.may_have_landed = may_have_landed


class PostWriteError(Exception):
    """A write was (or may have been) sent, but its outcome is unknown or its
    follow-up failed. The write may have landed: do not blindly retry."""


class Backend(Protocol):
    def get_microversion(self, ref: DocRef) -> str: ...

    def list_features(self, ref: DocRef) -> FeatureList: ...

    def get_variables(self, ref: DocRef) -> list[Variable]: ...

    def get_feature_specs(self, ref: DocRef) -> list[FeatureSpec]:
        """Native feature specs of the part studio's feature library, sorted by type."""
        ...

    def add_feature(self, ref: DocRef, feature_json: dict, expected_microversion: str) -> WriteResult:
        """Raises StaleVersion(may_have_landed=True) if Onshape reports skew (the
        write was most likely applied anyway). The stale guard itself is the
        caller's pre-POST version check, not the server."""
        ...

    def update_feature(self, ref: DocRef, feature_id: str, feature_json: dict,
                       expected_microversion: str) -> WriteResult:
        """Replace the existing feature `feature_id` with feature_json, in place.
        Same failure contract as add_feature."""
        ...

    def set_variable(self, ref: DocRef, name: str, expression: str, expected_microversion: str,
                     feature_id: str | None = None) -> WriteResult:
        """Returns a WriteResult: the variable feature (id, regeneration status;
        "" when the backend did not report them) and the new microversion.
        Raises StaleVersion if stale.

        feature_id is the existing variable's feature (from the last-read feature
        list) to update in place; None creates a new variable."""
        ...

    def eval_featurescript(self, ref: DocRef, script: str) -> dict:
        """Run a read-only FeatureScript evaluation and return the reply's `result`
        value. Not a write: no version pin, no cache change. Costs one request."""
        ...
