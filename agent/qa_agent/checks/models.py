"""Core data structures shared by every stage of the pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PASS, FAIL, UNVERIFIED = "passed", "failed", "unverified"
VERDICT_ORDER = {FAIL: 0, PASS: 1, UNVERIFIED: 2}


@dataclass
class Requirement:
    id: str
    section: str
    text: str                     # atomic, verbatim sentence from the PRD
    source_line: int              # 1-based line in PRD.md, for traceability
    testable: bool = True
    untestable_reason: str = ""
    check_ids: list[str] = field(default_factory=list)
    mapped_by: str = ""           # "anchor", "llm" or "" (unmapped)
    verdict: str = UNVERIFIED
    verdict_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Step:
    text: str
    screenshot: str | None = None


@dataclass
class CheckResult:
    check_id: str
    title: str
    category: str                 # normal | negative | edge
    area: str
    verdict: str = UNVERIFIED
    severity: str = "medium"      # critical | high | medium | low | info
    expected: str = ""
    observed: str = ""
    reason: str = ""
    steps: list[Step] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    screenshots: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    attempts: int = 1
    heals: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steps"] = [asdict(s) for s in self.steps]
        return d


class AgentError(Exception):
    """The agent could not perform a step (selector, timeout, environment). Never a product verdict."""


class Unverifiable(Exception):
    """The check reached a state where a reliable verdict is impossible (e.g. concurrent writes)."""
