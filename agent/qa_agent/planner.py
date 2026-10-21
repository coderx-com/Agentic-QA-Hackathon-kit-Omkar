"""Stage 2 - PLAN: map requirements to executable checks and order the run.

1. Anchor mapping (deterministic): each check declares the PRD phrases it verifies.
2. LLM gap-filling (optional, ~1 cheap call): requirements no anchor matched are offered to the model with the
   check catalogue; it may assign checks or explain why a statement is not verifiable through the UI.
3. Anything still unmapped is reported UNVERIFIED with that reason - never silently dropped.
"""
from __future__ import annotations

import json

from .checks import REGISTRY
from .llm import LLM
from .models import Requirement

AREA_ORDER = ["Access", "Accounts", "Seed data", "History", "Transfers", "Profile", "Interface", "Safety", "Scope"]


def map_requirements(reqs: list[Requirement], llm: LLM) -> None:
    for r in reqs:
        text = r.text.lower()
        r.check_ids = [c.id for c in REGISTRY.values() if any(a in text for a in c.anchors)]
        if r.check_ids:
            r.mapped_by = "anchor"
    gaps = [r for r in reqs if not r.check_ids]
    if gaps and llm.enabled:
        catalog = [{"id": c.id, "title": c.title, "area": c.area} for c in REGISTRY.values()]
        prompt = ("PRD requirements that still need a test:\n" +
                  json.dumps([{"id": r.id, "text": r.text} for r in gaps], indent=1) +
                  "\n\nAvailable executable checks:\n" + json.dumps(catalog, indent=1) +
                  "\n\nFor each requirement return the check ids that genuinely verify it (only if the check "
                  "observes that exact behaviour), or an empty list plus a one-sentence reason why it cannot be "
                  'verified through the running UI. JSON: {"mappings":[{"id":"REQ-..","checks":["C-.."],'
                  '"reason":""}]}')
        data = llm.complete_json("map_requirements", "You are a meticulous QA lead. Answer with JSON only.",
                                 prompt, max_tokens=4000) or {}
        for m in data.get("mappings", []):
            r = next((x for x in gaps if x.id == m.get("id")), None)
            if not r:
                continue
            valid = [c for c in m.get("checks", []) if c in REGISTRY]
            if valid:
                r.check_ids, r.mapped_by = valid, "llm"
            elif m.get("reason"):
                r.testable, r.untestable_reason = False, m["reason"]
    for r in reqs:
        if not r.check_ids and not r.untestable_reason:
            r.testable = False
            r.untestable_reason = ("Aggregate/intent statement; its concrete behaviours are verified by the "
                                   "section's other requirements." if r.id.startswith("REQ-PUR")
                                   else "No executable check observes this statement.")


def plan(only: list[str] | None = None, skip: list[str] | None = None) -> list[str]:
    ids = [c.id for c in sorted(REGISTRY.values(), key=lambda c: (AREA_ORDER.index(c.area)
                                                                    if c.area in AREA_ORDER else 99, c.id))]
    if only:
        ids = [i for i in ids if any(i.startswith(o) for o in only)]
    if skip:
        ids = [i for i in ids if not any(i.startswith(s) for s in skip)]
    return ids
