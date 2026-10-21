"""Stage 3/4 - EXECUTE + VERIFY: run each check in an isolated browser context with recovery.

* Fresh context per check -> no state leaks between scenarios; each verdict is reproducible on its own.
* Agent problems (timeouts, missing controls) are retried once for read-only checks and otherwise reported
  as UNVERIFIED with the reason - an agent error is never reported as a product failure.
"""
from __future__ import annotations

import json
import time
import traceback

from playwright.sync_api import Browser

from .browser import Session
from .checks import REGISTRY, CheckContext
from .driver import DemoBank
from .llm import LLM
from .models import FAIL, PASS, UNVERIFIED, AgentError, CheckResult, Requirement, Unverifiable

SYMBOL = {PASS: "PASS", FAIL: "FAIL", UNVERIFIED: "UNVR"}


def run_checks(browser: Browser, base_url: str, out_dir, llm: LLM, ids: list[str], run_state: dict,
               trace: bool = False) -> list[CheckResult]:
    results = []
    (out_dir / "network").mkdir(parents=True, exist_ok=True)
    for n, cid in enumerate(ids, 1):
        spec = REGISTRY[cid]
        attempts, t0 = 0, time.time()
        while True:
            attempts += 1
            s = Session(browser, base_url, out_dir, cid, llm, trace=trace)
            if run_state.get("api_origin"):
                s.api_origin = run_state["api_origin"]
            res = CheckResult(cid, spec.title, spec.category, spec.area, severity=spec.severity)
            ctx = CheckContext(s, DemoBank(s), res, run_state)
            error = None
            try:
                spec.fn(ctx)
            except Unverifiable as exc:
                ctx.unverified(str(exc))
            except AgentError as exc:
                error = f"AgentError: {exc}"
            except Exception as exc:  # timeouts, unexpected page states
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
                res.evidence["traceback"] = traceback.format_exc(limit=3)[-1500:]
            if error and not spec.mutates and attempts < 2 and not any(not a["ok"] for a in ctx.assertions):
                print(f"      retrying {cid} after: {error[:120]}")
                s.close()
                continue
            if error:
                shot = s.screenshot("agent-error state")
                if shot:
                    res.screenshots.append(shot)
                ctx.unverified(f"The agent could not complete this scenario reliably ({error}).")
            ctx.finalize()
            if s.api_origin and not run_state.get("api_origin"):
                run_state["api_origin"] = s.api_origin
            res.steps, res.heals, res.attempts = s.steps, s.heals, attempts
            res.evidence["api_calls"] = [e.short() for e in s.net if "/api/" in e.url][:60]
            if s.console_errors:
                res.evidence["console_errors"] = s.console_errors[:10]
            (out_dir / "network" / f"{cid}.json").write_text(json.dumps(
                [{"method": e.method, "url": e.url, "status": e.status, "request": e.request_body,
                  "response": e.response} for e in s.net], indent=1, default=str), encoding="utf-8")
            trace_rel = s.close()
            if trace_rel:
                res.evidence["trace"] = trace_rel
            break
        res.duration_s = round(time.time() - t0, 2)
        results.append(res)
        print(f"  [{n:02d}/{len(ids)}] {SYMBOL[res.verdict]}  {cid:<10} {res.title[:70]}  ({res.duration_s}s)")
    return results


def aggregate(reqs: list[Requirement], results: list[CheckResult]) -> None:
    by_id = {r.check_id: r for r in results}
    for r in results:
        r.requirement_ids = [q.id for q in reqs if r.check_id in q.check_ids]
    for q in reqs:
        ran = [by_id[c] for c in q.check_ids if c in by_id]
        if not q.testable and not ran:
            q.verdict, q.verdict_reason = UNVERIFIED, q.untestable_reason
            continue
        if not ran:
            q.verdict, q.verdict_reason = UNVERIFIED, "Mapped checks were not executed in this run."
            continue
        fails = [r for r in ran if r.verdict == FAIL]
        passes = [r for r in ran if r.verdict == PASS]
        if fails:
            q.verdict = FAIL
            q.verdict_reason = "; ".join(f"{r.check_id}: {r.reason}" for r in fails)[:600]
        elif passes:
            q.verdict = PASS
            unv = [r.check_id for r in ran if r.verdict == UNVERIFIED]
            q.verdict_reason = (f"Verified by {', '.join(r.check_id for r in passes)}" +
                                (f" (not verifiable in this run: {', '.join(unv)})" if unv else ""))
        else:
            q.verdict = UNVERIFIED
            q.verdict_reason = "; ".join(f"{r.check_id}: {r.reason}" for r in ran)[:600]
