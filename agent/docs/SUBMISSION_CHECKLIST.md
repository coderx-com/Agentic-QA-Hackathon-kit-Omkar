# Submission checklist

## 1. Repository (public GitHub fork)
- [ ] Fork of the kit with the agent in `agent/` and the target untouched in `test-website/`
- [ ] `agent/README.md` (architecture, setup, run commands, limitations) renders on GitHub, including the Mermaid diagram
- [ ] `agent/docs/WINDOWS_SETUP.md` present
- [ ] `python -m unittest -v` passes from `agent/`
- [ ] **No secrets:** `.env` is git-ignored; search the repo for `sk-ant` before pushing
- [ ] Suggested logical commits (in this order):
  1. `Add QA agent core: PRD interpreter, oracles, models`
  2. `Add resilient browser layer and DemoBank driver`
  3. `Add checks: access, accounts, history, transfers, profile, UI`
  4. `Add planner, runner, reviewer, HTML/Markdown reports, CLI`
  5. `Add tests, docs and Windows launcher`
  6. `Add QA audit report from hosted run`

## 2. QA audit report (from the HOSTED target)
- [ ] Full run against the organizers' URL: `python -m qa_agent --url <hosted URL> --prd ..\test-website\PRD.md`
- [ ] Copy that run folder to `agent/audit-report/` (report.html, report.md, results.json, requirements.json, screenshots/, network/)
- [ ] Open `report.md` on GitHub and confirm the defect screenshots render
- [ ] Review every FAILED item once yourself. Open its screenshot and confirm it is a product defect, not an environment hiccup. Rerun just that check with `--only <check-id>` if unsure.
- [ ] Cost breakdown section present (tokens + USD; state "$0 LLM" if run with `--llm none`)

## 3. Video (3–5 min)
- [ ] Follows `docs/VIDEO_SCRIPT.md`: spec ingestion → live browser execution → generated report
- [ ] Uploaded to YouTube as Unlisted; link opens in a private window
- [ ] Link added to the top of `agent/README.md`

## 4. Final submission form
- [ ] Repository URL
- [ ] Video URL
- [ ] Report path / link (`agent/audit-report/report.md`)
- [ ] Short description: "Spec-driven QA agent: 56 atomic PRD requirements → 53 browser checks (normal/negative/edge) with deterministic oracles, concurrency-safe money verification, self-healing locators, and requirement-level HTML/MD/JSON reports. LLM optional; run cost < $0.01."
