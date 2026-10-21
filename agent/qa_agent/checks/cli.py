"""Command-line entry point: python -m qa_agent --url <frontend URL> --prd <PRD.md>"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import __version__
from .browser import Session
from .explorer import explore
from .llm import DEFAULT_MODEL, LLM
from .planner import map_requirements, plan
from .prd import parse_prd
from .report import write_reports
from .reviewer import build_defects
from .runner import aggregate, run_checks

LIMITATIONS = [
    "Server-side behaviour that is not exposed through the UI or its HTTP responses (e.g. outbound calls from the "
    "backend) cannot be observed.",
    "Money assertions use before/after server state; concurrent transactions by other users are attributed and "
    "subtracted. If attribution is impossible the check is reported UNVERIFIED rather than FAILED.",
    "Timezone-sensitive date checks follow the page's own statement that dates use Pacific Time.",
    "Chromium only (Playwright). Other browsers were not exercised.",
    "Transfers performed by the tests (and money moved by defects) are reversed with compensating transfers; they "
    "remain visible in transaction history as tagged records.",
]


def _load_dotenv():
    for p in (Path(".env"), Path(__file__).resolve().parent.parent / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _load_dotenv()
    ap = argparse.ArgumentParser(prog="qa_agent", description="Spec-driven autonomous QA agent for DemoBank")
    ap.add_argument("--url", required=True, help="Frontend URL of the target, e.g. http://localhost:5173")
    ap.add_argument("--prd", required=True, help="Path to PRD.md")
    ap.add_argument("--out", default="runs", help="Output folder (a timestamped run folder is created inside)")
    ap.add_argument("--llm", choices=["anthropic", "none", "mock"], default=os.getenv("QA_LLM", "anthropic"),
                    help="LLM assistance (default anthropic if ANTHROPIC_API_KEY is set, else none)")
    ap.add_argument("--model", default=os.getenv("QA_MODEL", DEFAULT_MODEL))
    ap.add_argument("--only", nargs="*", help="Run only checks whose id starts with these prefixes (e.g. C-XFER)")
    ap.add_argument("--skip", nargs="*", help="Skip checks whose id starts with these prefixes")
    ap.add_argument("--headed", action="store_true", help="Show the browser window (good for the demo video)")
    ap.add_argument("--slowmo", type=int, default=0, help="Slow each browser action by N ms (demo video)")
    ap.add_argument("--trace", action="store_true", help="Record a Playwright trace per check (larger output)")
    ap.add_argument("--no-explore", action="store_true", help="Skip the exploration stage")
    args = ap.parse_args(argv)

    llm_mode = args.llm
    if llm_mode == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set -> running with deterministic planning only (LLM cost $0).")
        llm_mode = "none"
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / run_id
    (out / "screenshots").mkdir(parents=True, exist_ok=True)
    (out / "traces").mkdir(exist_ok=True)
    llm = LLM(mode=llm_mode, model=args.model, cache_dir=Path(args.out) / ".llm_cache")
    t0 = time.time()
    tag = "QA-" + secrets.token_hex(2).upper()
    base = args.url.rstrip("/")

    print(f"\n== DemoBank spec-driven QA agent v{__version__} ==  run {run_id}  tag {tag}")
    print(f"target {base}   prd {args.prd}   llm {llm_mode} ({args.model if llm.enabled else '-'})\n")

    print("[1/5] INTERPRET  parsing PRD into atomic requirements")
    reqs, prd_sha = parse_prd(args.prd)
    print(f"      {len(reqs)} requirements from {len({r.section for r in reqs})} sections")

    print("[2/5] PLAN       mapping requirements to checks")
    map_requirements(reqs, llm)
    ids = plan(args.only, args.skip)
    mapped = sum(1 for r in reqs if r.check_ids)
    print(f"      {mapped}/{len(reqs)} requirements mapped "
          f"({sum(1 for r in reqs if r.mapped_by == 'llm')} by LLM), {len(ids)} checks planned")
    (out / "requirements.json").write_text(json.dumps([r.to_dict() for r in reqs], indent=2), encoding="utf-8")

    run_state = {"tag": tag}
    exploration: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, slow_mo=args.slowmo)
        try:
            probe = browser.new_page()
            try:
                probe.goto(base, wait_until="domcontentloaded", timeout=20_000)
            except Exception as exc:
                print(f"\nPREFLIGHT FAILED: cannot open {base} ({str(exc).splitlines()[0]}).")
                print("Check the URL, that the frontend/backend are running, and your network, then retry.")
                return 2
            finally:
                probe.close()
            if not args.no_explore:
                print("[3/5] EXPLORE    discovering pages and controls")
                s = Session(browser, base, out, "EXPLORE", llm)
                try:
                    exploration = explore(s)
                    run_state["api_origin"] = s.api_origin
                    print(f"      {len(exploration['pages'])} pages, API at {s.api_origin}")
                except Exception as exc:
                    print(f"      exploration failed: {exc}")
                    exploration = {"pages": [], "features": {}, "error": str(exc)}
                finally:
                    s.close()
            print(f"[4/5] EXECUTE    {len(ids)} checks (each in a fresh browser session)")
            results = run_checks(browser, base, out, llm, ids, run_state, trace=args.trace)
        finally:
            browser.close()

    print("[5/5] VERIFY+REPORT")
    aggregate(reqs, results)
    defects, summary = build_defects(results, reqs, llm)
    meta = {"target": base, "prd": str(args.prd), "prd_sha": prd_sha, "run_id": run_id, "tag": tag,
            "started": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "version": __version__,
            "duration_s": round(time.time() - t0, 1), "limitations": LIMITATIONS}
    cost = llm.summary()
    write_reports(out, meta, reqs, results, defects, summary, exploration, cost)
    v = {k: sum(1 for r in reqs if r.verdict == k) for k in ("passed", "failed", "unverified")}
    c = {k: sum(1 for r in results if r.verdict == k) for k in ("passed", "failed", "unverified")}
    print(f"\nRequirements: {v['passed']} passed, {v['failed']} failed, {v['unverified']} unverified "
          f"(of {len(reqs)})")
    print(f"Checks:       {c['passed']} passed, {c['failed']} failed, {c['unverified']} unverified (of {len(results)})")
    print(f"Defects:      {len(defects)}")
    for d in defects:
        print(f"  {d['id']} [{d['severity']}] {d['title']}  ({', '.join(d['checks'])})")
    print(f"LLM cost:     ${cost['total_cost_usd']:.4f} ({cost['live_calls']} live calls, {cost['cached_hits']} cached)")
    print(f"Duration:     {meta['duration_s']} s")
    print(f"\nReport: {(out / 'report.html').resolve()}")
    return 0
