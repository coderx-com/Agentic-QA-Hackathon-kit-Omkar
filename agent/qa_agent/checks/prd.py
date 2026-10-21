"""Stage 1 - INTERPRET: turn PRD prose into atomic, traceable requirements.

Deterministic first pass (zero cost, fully reproducible): every normative sentence in the PRD becomes one
requirement with a stable ID and the PRD line it came from. An optional LLM pass (planner.py) only maps
leftover requirements to checks; it never rewrites the requirement text, so the audit trail stays verbatim.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .models import Requirement

SECTION_CODES = {
    "purpose": "PUR", "users and access": "ACC", "dashboard and accounts": "DASH",
    "transfers between owned accounts": "XFER", "transaction history": "HIST", "profile": "PROF",
    "general interface behavior": "UI", "seed data expectations": "SEED", "out of scope": "OOS",
}
META_SECTIONS = {"requirement coverage expectations"}       # instructions for the QA report, not the app
NORMATIVE = re.compile(r"\b(must|should|never|can|cannot|shows?|displays?|provides?|includes?|opens?|"
                       r"returns?|identif(?:y|ies)|has no|begins|are displayed|is allowed|works|reports)\b", re.I)
NON_TESTABLE_HINTS = {
    "PUR": "Statement of purpose; verified indirectly through the concrete requirements below.",
}


def _code(heading: str) -> str:
    key = heading.strip().lower()
    if key in SECTION_CODES:
        return SECTION_CODES[key]
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", heading)).upper()[:4] or "GEN"


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if len(p.strip()) > 3]


def parse_prd(path: str | Path) -> tuple[list[Requirement], str]:
    raw = Path(path).read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    reqs: list[Requirement] = []
    section, code, counter = "Preamble", "GEN", 0
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip()
            code, counter = _code(section), 0
            continue
        if not stripped or stripped.startswith("#") or section.lower() in META_SECTIONS:
            continue
        is_bullet = stripped.startswith(("- ", "* "))
        body = stripped[2:] if is_bullet else stripped
        if code == "OOS" and is_bullet:
            counter += 1
            reqs.append(Requirement(
                id=f"REQ-{code}-{counter:02d}", section=section, source_line=lineno,
                text=f"Out of scope (must not be offered): {body.rstrip('.')}.",
            ))
            continue
        for sentence in _sentences(body):
            # Purpose/preamble prose only yields explicitly normative sentences; every sentence in a
            # behavioural section is a requirement (PRD bullets are terse and all of them matter).
            if code in ("PUR", "GEN") and not NORMATIVE.search(sentence):
                continue
            if sentence.lower().startswith(("this document", "implementations may", "all records are synthetic")):
                continue
            counter += 1
            req = Requirement(id=f"REQ-{code}-{counter:02d}", section=section, text=sentence, source_line=lineno)
            if code in NON_TESTABLE_HINTS and "must" not in sentence.lower():
                req.testable, req.untestable_reason = False, NON_TESTABLE_HINTS[code]
            reqs.append(req)
    return reqs, digest
