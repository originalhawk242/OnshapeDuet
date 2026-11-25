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
    raw: dict = field(default_factory=dict)  # feature JSON exactly as Onshape returned it ({} when unknown)


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


def _flatten_param(p: dict) -> object:
    """Editable value of a parameter: its expression (quantities), else its scalar
    value (strings, enums, booleans, numbers); anything else (queries...) stays raw."""
    if isinstance(p.get("expression"), str):
        return p["expression"]
    val = p.get("value")
    if isinstance(val, (str, int, float, bool)):
        return val
    return p


def flatten_params(parameters: list) -> dict[str, object]:
    """Flatten an Onshape `parameters` list into {parameterId: editable value}."""
    return {(p.get("parameterId") if isinstance(p.get("parameterId"), str) else ""): _flatten_param(p)
            for p in parameters if isinstance(p, dict)}
