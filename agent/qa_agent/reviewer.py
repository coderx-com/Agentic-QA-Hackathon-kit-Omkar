"""Stage 5a - TRIAGE: group failing checks into defects and add impact notes.

Verdicts are NOT changed by the LLM. It only clusters failures that share a root cause and writes short impact
/ likely-cause notes. Without an LLM a deterministic grouping (by shared requirement) is used.
"""
from __future__ import annotations

import json

from .llm import LLM
from .models import FAIL, CheckResult, Requirement

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def build_defects(results: list[CheckResult], reqs: list[Requirement], llm: LLM) -> tuple[list[dict], str]:
    failed = [r for r in results if r.verdict == FAIL]
    req_text = {q.id: q.text for q in reqs}
    groups: list[dict] = []
    summary = ""
    if failed and llm.enabled:
        payload = [{"check": r.check_id, "title": r.title, "requirements": [req_text.get(i, i) for i in r.requirement_ids],
                    "expected": r.expected[:400], "observed": r.observed[:500]} for r in failed]
        data = llm.complete_json(
            "triage_failures",
            "You are a senior QA engineer writing a defect register. JSON only. Do not invent facts beyond the input.",
            "Failed checks from an automated run against a banking demo app:\n" + json.dumps(payload, indent=1) +
            "\n\nGroup checks that share ONE underlying defect. For each group return: title (<=12 words), "
            "checks (ids), impact (1 sentence, user/business terms), likely_cause (1 sentence, hedged), "
            "confidence (high|medium|low that this is a real product defect). Also write summary: 3-4 sentence "
            'executive summary of product quality. JSON: {"defects":[{"title":"","checks":[],"impact":"",'
            '"likely_cause":"","confidence":""}],"summary":""}', max_tokens=6000) or {}
        known = {r.check_id for r in failed}
        for d in data.get("defects", []):
            ids = [c for c in d.get("checks", []) if c in known]
            if ids:
                groups.append({**d, "checks": ids})
                known -= set(ids)
        for c in sorted(known):
            groups.append({"title": "Not met: " + next(r.title for r in failed if r.check_id == c), "checks": [c]})
        summary = data.get("summary", "")
    else:
        for r in failed:
            groups.append({"title": "Not met: " + r.title, "checks": [r.check_id]})
    by_id = {r.check_id: r for r in results}
    defects = []
    for g in groups:
        rs = [by_id[c] for c in g["checks"]]
        lead = sorted(rs, key=lambda r: SEV_RANK.get(r.severity, 9))[0]
        defects.append({
            "title": g["title"], "checks": g["checks"], "severity": lead.severity,
            "requirements": sorted({i for r in rs for i in r.requirement_ids}),
            "impact": g.get("impact", ""), "likely_cause": g.get("likely_cause", ""),
            "confidence": g.get("confidence", "high (deterministic oracle)"),
            "steps": [s.text for s in lead.steps], "expected": lead.expected, "observed": lead.observed,
            "failed_assertions": [a for r in rs for a in r.evidence.get("assertions", []) if not a["ok"]],
            "screenshots": lead.screenshots[:4]})
    defects.sort(key=lambda d: (SEV_RANK.get(d["severity"], 9), d["checks"][0]))
    for i, d in enumerate(defects, 1):
        d["id"] = f"BUG-{i:02d}"
        for c in d["checks"]:
            by_id[c].review = {"defect": d["id"], "impact": d["impact"], "confidence": d["confidence"]}
    return defects, summary
