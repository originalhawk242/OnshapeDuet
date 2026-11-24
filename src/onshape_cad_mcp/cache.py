"""Document cache keyed by microversion.

A read costs one get_microversion call; list_features is only fetched when the
microversion differs from the cached one. Read/write staleness semantics live
in Context, not here.
"""
from __future__ import annotations

import copy

from .backends.base import Backend
from .docref import DocRef
from .model import Feature, FeatureList


class DocCache:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self._entries: dict[DocRef, FeatureList] = {}

    def read(self, ref: DocRef) -> FeatureList:
        mv = self._backend.get_microversion(ref)
        entry = self._entries.get(ref)
        if entry is None or entry.microversion != mv:
            entry = copy.deepcopy(self._backend.list_features(ref))
            self._entries[ref] = entry
        return copy.deepcopy(entry)

    def peek(self, ref: DocRef) -> FeatureList | None:
        """The cached entry (a copy) without any request, or None."""
        entry = self._entries.get(ref)
        return copy.deepcopy(entry) if entry is not None else None

    def invalidate(self, ref: DocRef) -> None:
        self._entries.pop(ref, None)

    def note_write(self, ref: DocRef, new_microversion: str, feature: Feature | None = None,
                   *, expected_microversion: str | None = None) -> None:
        """Record the state after our own write without refetching.

        With a feature, it replaces the same-id feature or is appended, and the
        entry moves to new_microversion. Without one the resulting feature list
        is unknown, so the entry is dropped. No entry: nothing to do.

        The splice is only sound if the cached entry is the version the write
        was pinned to: when expected_microversion is missing or differs from the
        entry's version, the entry is dropped instead.
        """
        entry = self._entries.get(ref)
        if entry is None:
            return
        if feature is None or entry.microversion != expected_microversion:
            del self._entries[ref]
            return
        feats = [f for f in entry.features]
        new = copy.deepcopy(feature)
        for i, f in enumerate(feats):
            if f.id == new.id:
                feats[i] = new
                break
        else:
            feats.append(new)
        self._entries[ref] = FeatureList(new_microversion, feats)
