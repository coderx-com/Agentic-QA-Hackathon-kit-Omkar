"""Check registry and the per-check context that turns observations into auditable assertions."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from ..browser import Session
from ..driver import DemoBank
from ..models import FAIL, PASS, UNVERIFIED, CheckResult


@dataclass
class CheckSpec:
    id: str
    title: str
    area: str
    category: str            # normal | negative | edge
    severity: str            # severity if it fails
    anchors: list[str]       # PRD phrases this check verifies (used for requirement mapping)
    fn: Callable
    mutates: bool = False    # moves money -> never auto-retried; compensated instead
    description: str = ""


REGISTRY: dict[str, CheckSpec] = {}


def check(id: str, title: str, area: str, category: str, severity: str = "medium",
          anchors: list[str] | None = None, mutates: bool = False):
    def deco(fn):
        REGISTRY[id] = CheckSpec(id, title, area, category, severity, [a.lower() for a in (anchors or [])], fn,
                                 mutates, (fn.__doc__ or "").strip())
        return fn
    return deco


@dataclass
class CheckContext:
    s: Session
    app: DemoBank
    result: CheckResult
    run: dict                       # shared run state (tag, discovered accounts, created transfers, ...)
    assertions: list[dict] = field(default_factory=list)
    _explicit: str | None = None

    @property
    def tag(self) -> str:
        return self.run["tag"]

    def step(self, text: str, shot: bool = False):
        return self.s.step(text, shot)

    def expect(self, name: str, ok: bool, expected: Any, observed: Any, shot: bool = False) -> bool:
        a = {"assertion": name, "ok": bool(ok), "expected": _s(expected), "observed": _s(observed)}
        if shot or not ok:
            a["screenshot"] = self.s.screenshot(("FAIL " if not ok else "") + name)
            if a["screenshot"]:
                self.result.screenshots.append(a["screenshot"])
        self.assertions.append(a)
        return bool(ok)

    def unverified(self, reason: str):
        self._explicit = UNVERIFIED
        self.result.reason = reason

    def side_effect(self, text: str):
        self.result.side_effects.append(text)

    def finalize(self):
        r = self.result
        r.evidence["assertions"] = self.assertions
        failed = [a for a in self.assertions if not a["ok"]]
        if self._explicit == UNVERIFIED and not failed:
            r.verdict = UNVERIFIED
        elif failed:
            r.verdict = FAIL
            r.expected = "; ".join(f"{a['assertion']}: {a['expected']}" for a in failed)[:900]
            r.observed = "; ".join(f"{a['assertion']}: {a['observed']}" for a in failed)[:900]
            r.reason = r.reason or "; ".join(a["assertion"] for a in failed)
        elif self.assertions:
            r.verdict = PASS
            r.expected = "; ".join(a["expected"] for a in self.assertions[:4])
            r.observed = "; ".join(a["observed"] for a in self.assertions[:4])
            r.reason = r.reason or f"{len(self.assertions)} assertion(s) held"
        else:
            r.verdict = UNVERIFIED
            r.reason = r.reason or "No assertion could be evaluated"


def _s(v: Any) -> str:
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, (list, tuple, set)):
        return ", ".join(_s(x) for x in v) if v else "(none)"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_s(x)}" for k, x in v.items())
    return str(v)
