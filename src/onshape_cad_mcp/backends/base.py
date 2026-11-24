"""Backend protocol: the only surface the rest of the server talks to."""
from __future__ import annotations

from typing import Protocol

from ..docref import DocRef
from ..model import FeatureList, Variable, WriteResult


class StaleVersion(Exception):
    """The document changed since the caller last read it.

    may_have_landed is True when Onshape reported that the write was applied
    against a newer version (microversionSkew): the state is then unknown.
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

    def add_feature(self, ref: DocRef, feature_json: dict, expected_microversion: str) -> WriteResult:
        """Raises StaleVersion if expected_microversion is not current."""
        ...

    def set_variable(self, ref: DocRef, name: str, expression: str, expected_microversion: str,
                     feature_id: str | None = None) -> str:
        """Returns the new microversion. Raises StaleVersion if stale.

        feature_id is the existing variable's feature (from the last-read feature
        list) to update in place; None creates a new variable."""
        ...
