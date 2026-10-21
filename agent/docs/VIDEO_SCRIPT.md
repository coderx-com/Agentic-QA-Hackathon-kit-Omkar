# Walkthrough video script (target 4:00, limit 3–5 min)

Record with OBS at 1920×1080. Before recording: reset the local database (or use the hosted URL), run
`chcp 65001` in cmd, increase the cmd font size, and have a finished full-run `report.html` open in a browser tab.

| Time | Screen | Say (roughly) |
|---|---|---|
| 0:00–0:20 | GitHub repo README (architecture diagram) | "This is a spec-driven QA agent for DemoBank. It reads the PRD, drives the real site in a browser, and answers one question per requirement: does the app behave the way the spec says? Passed, failed or unverified, each with evidence." |
| 0:20–0:50 | `PRD.md` beside `runs\<run>\requirements.json` | "Step one is interpretation. Every sentence of the PRD becomes an atomic requirement with an ID and the PRD line it came from: 56 of them. Requirements are mapped to 53 checks through anchors; any gaps would go to the LLM, and anything unmapped is reported, never dropped." |
| 0:50–2:00 | cmd: `python -m qa_agent --url <URL> --prd ..\test-website\PRD.md --headed --slowmo 300 --only C-ACC-01 C-XFER-02 C-XFER-13 C-HIST-09` | Narrate while the browser moves: "It enters the demo, transfers money, and then checks the server, not the success banner: exactly one record, and both balances moved by exactly the amount. Now it tries one cent more than the balance. The UI says Transfer complete, and checking goes to minus one cent. That's a critical defect. The agent records it, then moves the money back with a compensating transfer so the shared demo stays clean. Now the date filter: end date set to a day that has a transaction, and the transaction is missing. The PRD says dates are inclusive." |
| 2:00–2:20 | cmd summary output | "Each check runs in a fresh browser session. Agent problems such as a missing button are retried and reported as unverified, never as product failures. That keeps false positives down." |
| 2:20–3:30 | `report.html` (full run): scoreboard → defect register → one defect expanded → traceability matrix → one check's assertion table and network log link | "Here is the full audit. The defect register is ordered by severity: overdraft allowed, zero and negative amounts accepted, end date not inclusive, single-day range empty, and pending or failed rows showing $0.00 instead of their amount. Each has steps to reproduce, expected versus observed values, screenshots and the raw network log. The traceability matrix maps all 56 PRD requirements to their verdicts." |
| 3:30–3:50 | Report → Cost breakdown section | "Cost: the browser runs locally for free. Verdicts come from deterministic oracles, so the LLM only clusters defects and writes notes, typically one Haiku call costing a fraction of a cent. Reruns hit the cache at zero, and `--llm none` gives the same verdicts for free." |
| 3:50–4:00 | README "Design decisions" | "Resilience comes from layered locators with self-healing, waiting on real UI state instead of sleeps, and attributing other users' concurrent transfers. Thanks for watching." |

Tips: do one dry run first; if a scene runs long, cut the 2:00–2:20 segment. Upload to YouTube as **Unlisted**.
