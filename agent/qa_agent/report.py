"""Stage 5b - REPORT: requirement-level audit report (HTML + Markdown + JSON) with evidence and cost."""
from __future__ import annotations

import json
from collections import Counter
from html import escape as e
from pathlib import Path

from .models import FAIL, PASS, UNVERIFIED, CheckResult, Requirement

LABEL = {PASS: "PASSED", FAIL: "FAILED", UNVERIFIED: "UNVERIFIED"}


def _counts(items, attr="verdict"):
    c = Counter(getattr(i, attr) for i in items)
    return {PASS: c.get(PASS, 0), FAIL: c.get(FAIL, 0), UNVERIFIED: c.get(UNVERIFIED, 0)}


def write_reports(out: Path, meta: dict, reqs: list[Requirement], results: list[CheckResult], defects: list[dict],
                  summary: str, exploration: dict, cost: dict) -> dict:
    data = {"meta": meta, "summary": summary, "requirements": [r.to_dict() for r in reqs],
            "checks": [r.to_dict() for r in results], "defects": defects, "exploration": exploration, "cost": cost}
    (out / "results.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    (out / "report.md").write_text(_markdown(meta, reqs, results, defects, summary, cost), encoding="utf-8")
    (out / "report.html").write_text(_html(meta, reqs, results, defects, summary, exploration, cost), encoding="utf-8")
    return data


def _auto_summary(reqs, results, defects) -> str:
    rc, cc = _counts(reqs), _counts(results)
    top = ", ".join(d["title"] for d in defects[:4])
    return (f"{rc[PASS]} of {len(reqs)} PRD requirements passed, {rc[FAIL]} failed and {rc[UNVERIFIED]} could not be "
            f"verified. {cc[FAIL]} of {len(results)} executed checks found discrepancies, grouped into "
            f"{len(defects)} defect(s)" + (f"; most important: {top}." if top else "."))


# ------------------------------------------------------------------------------------------- markdown
def _markdown(meta, reqs, results, defects, summary, cost) -> str:
    rc, cc = _counts(reqs), _counts(results)
    cat = Counter((r.category, r.verdict) for r in results)
    L = [f"# QA Audit Report - {meta['target']}", "",
         f"Run `{meta['run_id']}` | {meta['started']} | {meta['duration_s']} s | agent v{meta['version']} | "
         f"PRD sha256 `{meta['prd_sha']}` | LLM: {cost['mode']} ({cost.get('model') or 'none'})", "",
         "## Summary", "", summary or _auto_summary(reqs, results, defects), "",
         "| | Passed | Failed | Unverified | Total |", "|---|---:|---:|---:|---:|",
         f"| Requirements | {rc[PASS]} | {rc[FAIL]} | {rc[UNVERIFIED]} | {len(reqs)} |",
         f"| Checks | {cc[PASS]} | {cc[FAIL]} | {cc[UNVERIFIED]} | {len(results)} |"]
    for c in ("normal", "negative", "edge"):
        L.append(f"| {c.title()} scenarios | {cat[(c, PASS)]} | {cat[(c, FAIL)]} | {cat[(c, UNVERIFIED)]} | "
                 f"{sum(v for (k, _), v in cat.items() if k == c)} |")
    L += ["", "## Defects", ""]
    if not defects:
        L.append("No defects found.")
    for d in defects:
        L += [f"### {d['id']} [{d['severity'].upper()}] {d['title']}", "",
              f"- **Requirements:** {', '.join(d['requirements'])}", f"- **Checks:** {', '.join(d['checks'])}",
              f"- **Confidence:** {d['confidence']}"]
        if d.get("impact"):
            L.append(f"- **Impact:** {d['impact']}")
        if d.get("likely_cause"):
            L.append(f"- **Likely cause (hypothesis):** {d['likely_cause']}")
        L += ["", "**Steps to reproduce**", ""] + [f"{i}. {s}" for i, s in enumerate(d["steps"], 1)]
        fa = d.get("failed_assertions") or []
        if fa:
            L += ["", "| Failed assertion | Expected (PRD) | Observed |", "|---|---|---|"]
            L += [f"| {a['assertion']} | {a['expected'][:200]} | {a['observed'][:250]} |".replace("\n", " ")
                  for a in fa[:8]]
            L.append("")
        else:
            L += ["", f"**Expected:** {d['expected']}", "", f"**Observed:** {d['observed']}", ""]
        L += [f"![{d['id']}]({s})" for s in d["screenshots"][:2]] + [""]
    L += ["## Requirement traceability", "", "| ID | Requirement | Verdict | Checks | Reason |",
          "|---|---|---|---|---|"]
    for q in reqs:
        L.append(f"| {q.id} | {q.text.replace('|', '/')} | **{LABEL[q.verdict]}** | {', '.join(q.check_ids) or '-'} | "
                 f"{q.verdict_reason.replace('|', '/')[:220]} |")
    L += ["", "## Checks", "", "| Check | Category | Verdict | Title | Duration |", "|---|---|---|---|---:|"]
    for r in results:
        L.append(f"| {r.check_id} | {r.category} | {LABEL[r.verdict]} | {r.title} | {r.duration_s}s |")
    L += ["", "## Cost breakdown", "", _cost_md(cost, meta), "", "## Side effects and cleanup", ""]
    se = [f"- {r.check_id}: {x}" for r in results for x in r.side_effects]
    L += se or ["- None"]
    L += ["", "## Limitations", ""] + [f"- {x}" for x in meta["limitations"]]
    return "\n".join(L) + "\n"


def _cost_md(cost, meta) -> str:
    rows = ["| Purpose | Live calls | Cache hits | Input tok | Output tok | Cost (USD) |", "|---|---:|---:|---:|---:|---:|"]
    for p, v in cost["by_purpose"].items():
        rows.append(f"| {p} | {v['calls']} | {v['cached_hits']} | {v['input_tokens']} | {v['output_tokens']} | "
                    f"${v['cost_usd']:.4f} |")
    rows.append(f"| **Total** | {cost['live_calls']} | {cost['cached_hits']} | {cost['input_tokens']} | "
                f"{cost['output_tokens']} | **${cost['total_cost_usd']:.4f}** |")
    p = cost["price_per_mtok_usd"]
    rows += ["", f"Pricing used: ${p['input']}/MTok input, ${p['output']}/MTok output ({cost.get('model') or 'no LLM'}). "
             f"Browser execution: {meta['duration_s']} s of local headless Chromium (no paid service). "
             f"Deterministic oracles decide every verdict, so the LLM is optional; requirement-mapping answers "
             f"are cached per PRD and triage is a single call per run."]
    return "\n".join(rows)


# ------------------------------------------------------------------------------------------- html
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#17202a;--mut:#5b6573;--line:#e3e6ea;--ok:#13795b;--okbg:#e6f4ee;
--bad:#b42318;--badbg:#fdecea;--unv:#8a5a00;--unvbg:#fff4dc;--acc:#1d4ed8}
@media (prefers-color-scheme: dark){:root{--bg:#0f1318;--card:#171c23;--ink:#e6e9ee;--mut:#9aa4b2;--line:#2a313b;
--ok:#4ade80;--okbg:#0f2a1d;--bad:#f87171;--badbg:#2c1313;--unv:#fbbf24;--unvbg:#2b220c;--acc:#93b4ff}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
main{max-width:1180px;margin:0 auto;padding:28px 20px 60px}h1{font-size:24px;margin:0 0 4px}h2{margin:34px 0 12px;font-size:19px}
.meta{color:var(--mut);font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.tile b{font-size:26px;display:block}
.tile span{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:.03em}
.passed{background:var(--okbg);color:var(--ok)}.failed{background:var(--badbg);color:var(--bad)}.unverified{background:var(--unvbg);color:var(--unv)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}th{color:var(--mut);font-weight:600}
.wrap{overflow-x:auto}details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:10px 14px}
summary{cursor:pointer;font-weight:600}.shots{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
.shots img{width:300px;max-width:100%;border:1px solid var(--line);border-radius:6px}code{font-size:12px}
.ok{color:var(--ok)}.no{color:var(--bad)}ol{padding-left:20px}.sev{font-size:11px;color:var(--mut);text-transform:uppercase}
"""


def _pill(v):
    return f'<span class="pill {v}">{LABEL[v]}</span>'


def _shots(paths):
    return '<div class="shots">' + "".join(f'<a href="{e(p)}" target="_blank"><img loading="lazy" src="{e(p)}" '
                                           f'alt="{e(p)}"></a>' for p in paths if p) + "</div>"


def _html(meta, reqs, results, defects, summary, exploration, cost) -> str:
    rc, cc = _counts(reqs), _counts(results)
    cat = Counter((r.category, r.verdict) for r in results)
    H = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
         f"content='width=device-width, initial-scale=1'><title>QA Audit - {e(meta['target'])}</title>"
         f"<style>{CSS}</style></head><body><main>",
         f"<h1>QA Audit Report — DemoBank</h1><div class='meta'>Target <code>{e(meta['target'])}</code> · run "
         f"<code>{e(meta['run_id'])}</code> · {e(meta['started'])} · {meta['duration_s']} s · PRD sha256 "
         f"<code>{e(meta['prd_sha'])}</code> · LLM {e(cost['mode'])} ({e(str(cost.get('model') or 'none'))})</div>",
         "<div class='grid'>",
         f"<div class='tile'><span>Requirements passed</span><b class='ok'>{rc[PASS]}/{len(reqs)}</b></div>",
         f"<div class='tile'><span>Requirements failed</span><b class='no'>{rc[FAIL]}</b></div>",
         f"<div class='tile'><span>Unverified</span><b>{rc[UNVERIFIED]}</b></div>",
         f"<div class='tile'><span>Defects</span><b class='no'>{len(defects)}</b></div>",
         f"<div class='tile'><span>Checks (pass/fail/unv)</span><b>{cc[PASS]}/{cc[FAIL]}/{cc[UNVERIFIED]}</b></div>",
         f"<div class='tile'><span>LLM cost this run</span><b>${cost['total_cost_usd']:.4f}</b></div></div>",
         f"<div class='card'>{e(summary or _auto_summary(reqs, results, defects))}</div>",
         "<div class='wrap'><table><tr><th>Scenario type</th><th>Passed</th><th>Failed</th><th>Unverified</th></tr>"]
    for c in ("normal", "negative", "edge"):
        H.append(f"<tr><td>{c}</td><td>{cat[(c, PASS)]}</td><td>{cat[(c, FAIL)]}</td><td>{cat[(c, UNVERIFIED)]}</td></tr>")
    H.append("</table></div><h2>Defect register</h2>")
    if not defects:
        H.append("<p>No defects found.</p>")
    for d in defects:
        H.append(f"<div class='card'><div class='sev'>{e(d['severity'])} · confidence: {e(d['confidence'])}</div>"
                 f"<h3 style='margin:4px 0'>{e(d['id'])} — {e(d['title'])}</h3>"
                 f"<div class='meta'>Requirements: {e(', '.join(d['requirements']))} · Checks: {e(', '.join(d['checks']))}</div>")
        if d.get("impact"):
            H.append(f"<p><b>Impact:</b> {e(d['impact'])}</p>")
        if d.get("likely_cause"):
            H.append(f"<p><b>Likely cause (hypothesis):</b> {e(d['likely_cause'])}</p>")
        H.append("<b>Steps to reproduce</b><ol>" + "".join(f"<li>{e(s)}</li>" for s in d["steps"]) + "</ol>")
        fa = d.get("failed_assertions") or []
        if fa:
            H.append("<div class='wrap'><table><tr><th>Failed assertion</th><th>Expected (PRD)</th><th>Observed</th></tr>" +
                     "".join(f"<tr><td>{e(a['assertion'])}</td><td>{e(a['expected'][:300])}</td>"
                             f"<td class='no'>{e(a['observed'][:400])}</td></tr>" for a in fa[:8]) + "</table></div>")
        else:
            H.append(f"<p><b>Expected:</b> {e(d['expected'])}</p><p><b>Observed:</b> {e(d['observed'])}</p>")
        H.append(_shots(d["screenshots"]) + "</div>")
    H.append("<h2>Requirement traceability matrix</h2><div class='wrap'><table><tr><th>ID</th><th>PRD line</th>"
             "<th>Requirement (verbatim)</th><th>Verdict</th><th>Checks</th><th>Basis</th></tr>")
    for q in reqs:
        H.append(f"<tr><td><code>{q.id}</code></td><td>L{q.source_line}</td><td>{e(q.text)}</td><td>{_pill(q.verdict)}</td>"
                 f"<td>{e(', '.join(q.check_ids) or '—')}<br><span class='meta'>{e(q.mapped_by)}</span></td>"
                 f"<td>{e(q.verdict_reason[:300])}</td></tr>")
    H.append("</table></div><h2>Check details</h2>")
    for r in results:
        H.append(f"<details{' open' if r.verdict == FAIL else ''}><summary>{_pill(r.verdict)} <code>{r.check_id}</code> "
                 f"{e(r.title)} <span class='meta'>· {r.category} · {r.duration_s}s"
                 f"{' · attempts ' + str(r.attempts) if r.attempts > 1 else ''}</span></summary>")
        H.append(f"<p class='meta'>Requirements: {e(', '.join(r.requirement_ids) or '—')} · {e(r.reason[:300])}</p>")
        if r.steps:
            H.append("<b>Steps</b><ol>" + "".join(f"<li>{e(s.text)}</li>" for s in r.steps) + "</ol>")
        rows = r.evidence.get("assertions", [])
        if rows:
            H.append("<div class='wrap'><table><tr><th></th><th>Assertion</th><th>Expected</th><th>Observed</th></tr>" +
                     "".join(f"<tr><td class='{'ok' if a['ok'] else 'no'}'>{'✔' if a['ok'] else '✘'}</td>"
                             f"<td>{e(a['assertion'])}</td><td>{e(a['expected'][:300])}</td><td>{e(a['observed'][:400])}</td></tr>"
                             for a in rows) + "</table></div>")
        if r.heals:
            H.append("<p><b>Self-healing:</b> " + e("; ".join(r.heals)) + "</p>")
        if r.side_effects:
            H.append("<p><b>Side effects / cleanup:</b> " + e("; ".join(r.side_effects)) + "</p>")
        if r.evidence.get("concurrent_activity"):
            H.append("<p><b>Concurrent activity attributed:</b> " + e("; ".join(r.evidence["concurrent_activity"])) + "</p>")
        H.append(f"<p class='meta'>Network log: <a href='network/{r.check_id}.json'>network/{r.check_id}.json</a>"
                 + (f" · Trace: <a href='{r.evidence['trace']}'>{r.evidence['trace']}</a>" if r.evidence.get("trace") else "")
                 + "</p>" + _shots(r.screenshots) + "</details>")
    H.append("<h2>Autonomous exploration</h2><div class='wrap'><table><tr><th>Page</th><th>Heading</th><th>Buttons</th>"
             "<th>Inputs</th></tr>")
    for p in exploration.get("pages", []):
        H.append(f"<tr><td><code>{e(p['path'])}</code></td><td>{e(p.get('h1', ''))}</td><td>{e(', '.join(p.get('buttons', [])))}</td>"
                 f"<td>{e(', '.join(i for i in p.get('inputs', []) if i))}</td></tr>")
    H.append("</table></div><p class='meta'>Feature areas located: " +
             e("; ".join(f"{k}: {', '.join(v[:3]) or 'not found'}" for k, v in exploration.get("features", {}).items())) + "</p>")
    H.append("<h2>Cost breakdown</h2><div class='wrap'><table><tr><th>Purpose</th><th>Live calls</th><th>Cache hits</th>"
             "<th>Input tokens</th><th>Output tokens</th><th>Cost (USD)</th></tr>")
    for p, v in cost["by_purpose"].items():
        H.append(f"<tr><td>{e(p)}</td><td>{v['calls']}</td><td>{v['cached_hits']}</td><td>{v['input_tokens']}</td>"
                 f"<td>{v['output_tokens']}</td><td>${v['cost_usd']:.4f}</td></tr>")
    pr = cost["price_per_mtok_usd"]
    H.append(f"<tr><th>Total</th><th>{cost['live_calls']}</th><th>{cost['cached_hits']}</th><th>{cost['input_tokens']}</th>"
             f"<th>{cost['output_tokens']}</th><th>${cost['total_cost_usd']:.4f}</th></tr></table></div>"
             f"<p class='meta'>Prices: ${pr['input']}/MTok in, ${pr['output']}/MTok out. Browser: {meta['duration_s']} s local "
             f"headless Chromium (free). Every verdict comes from deterministic oracles; the LLM only maps leftover "
             f"requirements, heals broken locators and writes triage notes. Mapping answers are cached per PRD.</p>")
    se = [f"{r.check_id}: {x}" for r in results for x in r.side_effects]
    H.append("<h2>Side effects and cleanup</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in se or ["None"]) + "</ul>")
    H.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in meta["limitations"]) + "</ul>")
    H.append("</main></body></html>")
    return "\n".join(H)
