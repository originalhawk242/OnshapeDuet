"""Find a feature by id or by name."""
from __future__ import annotations

from ..features.refs import resolve
from ..model import Feature, FeatureList


def find_feature(fl: FeatureList, key: str) -> Feature:
    """The feature whose id is exactly `key`, else the one feature with that name.
    Raises RefError (a ValueError) for an unknown or ambiguous name."""
    for f in fl.features:
        if f.id == key:
            return f
    return resolve(fl, key, "feature")
