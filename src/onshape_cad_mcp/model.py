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
    feature: Feature
    microversion: str
