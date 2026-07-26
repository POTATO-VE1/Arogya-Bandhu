from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, TypedDict


class NodeOutcome(TypedDict, total=False):
    node_id: str
    digit: str
    score: int
    reason: str | None
    forced_red: bool


@dataclass
class RiskResult:
    level: str  # 'green' | 'yellow' | 'red'
    score: int
    reasons: list[str] = field(default_factory=list)


def evaluate(outcomes: Iterable[NodeOutcome], missed_calls_before: int = 0) -> RiskResult:
    """Pure risk scoring (docs/03 §6.2). No I/O, deterministic."""
    total = 0
    forced_red = False
    reasons: list[str] = []

    for o in outcomes:
        total += int(o.get("score", 0))
        r = o.get("reason")
        if r:
            reasons.append(r)
        if o.get("forced_red"):
            forced_red = True

    if forced_red:
        level = "red"
    elif total >= 6:
        level = "red"
    elif total >= 2:
        level = "yellow"
    else:
        level = "green"

    if missed_calls_before >= 2 and level == "green":
        level = "yellow"
    if missed_calls_before >= 2:
        # unreachable-reason surfaced as a KPI, J3 (docs/00 J3)
        if "family unreachable on 2 scheduled calls" not in reasons:
            reasons.append("family unreachable on 2 scheduled calls")

    return RiskResult(level=level, score=total, reasons=reasons)