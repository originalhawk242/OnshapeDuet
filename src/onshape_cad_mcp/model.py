"""Domain model shared by all backends."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Feature:
    id: str
    type: str
    name: str
    status: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Variable:
    name: str
    expression: str


@dataclass(frozen=True)
class FeatureList:
    microversion: str
    features: list[Feature]


@dataclass(frozen=True)
class WriteResult:
    """Outcome of a landed write.

    exact is False when `microversion` came from a separate request after the
    write (the write reply did not name a new version): a human edit between the
    POST and that GET would be included unseen, so Claude has NOT read it.
    For set_variable, `feature` is the variable feature (id "" if unknown; status
    "" if the reply did not report one)."""
    feature: Feature
    microversion: str
    exact: bool = True
