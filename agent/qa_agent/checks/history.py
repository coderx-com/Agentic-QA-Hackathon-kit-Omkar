"""Transaction history: fields, direction, every filter, inclusive date boundaries, empty state, clearing.

Oracle: the UNFILTERED history the user sees is the ground truth. For any filter combination the expected
row set is computed locally (Pacific calendar dates, per the page's own hint) and compared row-by-row with
what the filtered view shows, so both extra rows and missing rows are caught.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from ..oracle import diff_sets, money_sign, parse_money, row_matches, to_pacific_date
from .registry import CheckContext, check

TYPES = ["Deposit", "Withdrawal", "Transfer"]
STATUSES = ["Completed", "Pending", "Failed"]


def _baseline(ctx: CheckContext):
    ctx.app.enter_demo()
    h = ctx.app.open_history()
    if h["kind"] != "table" or not h["rows"]:
        raise RuntimeError(f"Unfiltered history not readable (state={h['kind']})")
    hint = ctx.s.body_text()
    ctx.result.evidence["timezone_basis"] = ("Pacific Time (page hint)" if re.search(r"pacific", hint, re.I)
                                             else "Pacific Time (assumed; no hint found)")
    return h["rows"], {r["id"]: r for r in h["rows"]}


def _compare(ctx: CheckContext, label: str, rows_all, by_id, ui, **flt):
    expected = {r["id"] for r in rows_all if row_matches(r, **flt)[0]}
    observed = {r["id"] for r in ui["rows"]}
    bad_rows = [f"{r['date_text']} {r['description']}: {'; '.join(row_matches(r, **flt)[1])}"
                for r in ui["rows"] if not row_matches(r, **flt)[0]]
    ctx.expect(f"{label}: every displayed row satisfies the filters", not bad_rows, "all rows match", bad_rows[:5])
    d = diff_sets(expected, observed, {**by_id, **{r['id']: r for r in ui['rows']}})
    ctx.expect(f"{label}: no matching transaction is missing", not d["missing"],
               f"{len(expected)} row(s)", f"{len(observed)} row(s); missing: {d['missing'][:5]}")
    if ui["kind"] == "error":
        ctx.expect(f"{label}: filter request succeeded", False, "results or empty state", ui.get("message"))
    return expected, observed


@check("C-HIST-01", "Combined and account-specific histories are both available", "History", "normal", "high",
       anchors=["view a combined transaction history"])
def views_exist(ctx: CheckContext):
    rows, _ = _baseline(ctx)
    ctx.step("Combined history", shot=True)
    ctx.expect("Combined history lists transactions", len(rows) > 0, "> 0 rows", len(rows))
    for a in ctx.app.accounts_by_type().values():
        d = ctx.app.read_account_detail(a["id"])
        ctx.expect(f"{a['name']} has an account-specific history", d["history"]["kind"] in ("table", "empty"),
                   "history table or empty state", d["history"]["kind"])


@check("C-HIST-02", "Every row shows date/time, description, type, account, amount and status", "History",
       "normal", "medium", anchors=["each row shows date and time"])
def row_fields(ctx: CheckContext):
    rows, _ = _baseline(ctx)
    api = {t["id"]: t for t in ctx.app.api_transactions()}
    missing = [f"{r['description'] or r['id']}: missing {k}" for r in rows
               for k in ("date_text", "description", "type", "account", "amount_text", "status") if not r[k].strip()]
    ctx.expect("No empty cells in required columns", not missing, "all fields present", missing[:6])
    wrong = []
    for r in rows:
        t = api.get(r["id"])
        shown = parse_money(r["amount_text"])
        if t and shown is not None and abs(shown) != abs(parse_money(t["amount"])):
            wrong.append(f"{r['description']} ({t['type']}, {t['status']}): shows {r['amount_text']} "
                         f"but transaction amount is ${t['amount']}")
    ctx.step("History table", shot=True)
    ctx.expect("Amount column shows each transaction's amount", not wrong, "UI amount = transaction amount",
               wrong[:6])
    ctx.result.evidence["rows_checked"] = len(rows)


@check("C-HIST-03", "Amount direction is clear for deposits, withdrawals and transfers", "History", "normal",
       "medium", anchors=["amount direction should be clear"])
def direction(ctx: CheckContext):
    rows, _ = _baseline(ctx)
    problems = []
    for r in rows:
        if r["status"].lower() != "completed":
            continue
        t, s = r["type"].lower(), money_sign(r["amount_text"])
        if t == "deposit" and s != 1:
            problems.append(f"combined: deposit '{r['description']}' shown as {r['amount_text']}")
        if t == "withdrawal" and s != -1:
            problems.append(f"combined: withdrawal '{r['description']}' shown as {r['amount_text']}")
        if t == "transfer" and not re.search(r"from.+to", r["account"], re.I):
            problems.append(f"combined: transfer '{r['description']}' lacks From/To")
    for a in ctx.app.accounts_by_type().values():
        for r in ctx.app.read_account_detail(a["id"])["history"]["rows"]:
            if r["status"].lower() != "completed":
                continue
            s = money_sign(r["amount_text"])
            if r["type"].lower() == "transfer":
                out = re.search(r"from\s+" + re.escape(a["name"]), r["account"], re.I) is not None
                if s != (-1 if out else 1):
                    problems.append(f"{a['name']}: transfer '{r['description']}' shown as {r['amount_text']}")
            elif s == 0:
                problems.append(f"{a['name']}: '{r['description']}' has no direction ({r['amount_text']})")
    ctx.step("Direction inspected in combined and per-account views", shot=True)
    ctx.expect("Completed rows show money-in (+) / money-out (−) consistently", not problems,
               "deposits +, withdrawals −, transfers From/To and ± per account", problems[:6])


@check("C-HIST-04", "Filter by account shows exactly that account's transactions", "History", "normal", "high",
       anchors=["filter history by account", "every displayed row must satisfy all selected filters"])
def filter_account(ctx: CheckContext):
    rows, by_id = _baseline(ctx)
    for a in ctx.app.accounts_by_type().values():
        ui = ctx.app.apply_filters(account=a["name"])
        ctx.step(f"Filter account = {a['name']}", shot=True)
        _compare(ctx, f"account={a['name']}", rows, by_id, ui, account_name=a["name"])


@check("C-HIST-05", "Filter by transaction type (each type)", "History", "normal", "high",
       anchors=["filter history by account, transaction type"])
def filter_type(ctx: CheckContext):
    rows, by_id = _baseline(ctx)
    for t in TYPES:
        ui = ctx.app.apply_filters(type_label=t)
        ctx.step(f"Filter type = {t}", shot=(t == "Transfer"))
        _compare(ctx, f"type={t}", rows, by_id, ui, type_label=t)


@check("C-HIST-06", "Filter by status (each status)", "History", "normal", "high",
       anchors=["filter history by account, transaction type, status"])
def filter_status(ctx: CheckContext):
    rows, by_id = _baseline(ctx)
    for s in STATUSES:
        ui = ctx.app.apply_filters(status_label=s)
        ctx.step(f"Filter status = {s}", shot=(s == "Pending"))
        _compare(ctx, f"status={s}", rows, by_id, ui, status_label=s)


@check("C-HIST-07", "Combined filters: every row satisfies ALL selected filters", "History", "edge", "high",
       anchors=["every displayed row must satisfy all selected filters"])
def filter_combined(ctx: CheckContext):
    rows, by_id = _baseline(ctx)
    accounts = list(ctx.app.accounts_by_type().values())
    combos = Counter((a["name"], r["type"], r["status"]) for r in rows for a in accounts
                     if a["name"].lower() in r["account"].lower())
    (acct, typ, st), _ = combos.most_common(1)[0]
    dates = sorted(to_pacific_date(r["iso"]) for r in rows if r["iso"])
    start, end = dates[0].isoformat(), dates[-1].isoformat()
    ui = ctx.app.apply_filters(account=acct, type_label=typ, status_label=st, start=start, end=end)
    ctx.step(f"Filter {acct} + {typ} + {st} + {start}..{end}", shot=True)
    _compare(ctx, f"{acct}+{typ}+{st}+dates", rows, by_id, ui, account_name=acct, type_label=typ,
             status_label=st, start=date.fromisoformat(start), end=date.fromisoformat(end))


def _boundary_dates(rows) -> list[date]:
    """Prefer dates that make boundary bugs visible: oldest, newest seeded, and one whose UTC date differs."""
    by_date: dict[date, list] = {}
    for r in rows:
        if r["iso"]:
            by_date.setdefault(to_pacific_date(r["iso"]), []).append(r)
    ds = sorted(by_date)
    picks = [ds[0], ds[len(ds) // 2]]
    skew = next((to_pacific_date(r["iso"]) for r in rows if r["iso"] and to_pacific_date(r["iso"]).isoformat()
                 != r["iso"][:10]), None)
    if skew:
        picks.append(skew)
    return list(dict.fromkeys(picks))


@check("C-HIST-08", "Start date is inclusive (transaction ON the start date is listed)", "History", "edge", "high",
       anchors=["start and end dates are inclusive", "either boundary date"])
def start_inclusive(ctx: CheckContext):
    rows, by_id = _baseline(ctx)
    for d in _boundary_dates(rows):
        ui = ctx.app.apply_filters(start=d.isoformat())
        ctx.step(f"Start date = {d}", shot=True)
        _compare(ctx, f"start={d}", rows, by_id, ui, start=d)


@check("C-HIST-09", "End date is inclusive (transaction ON the end date is listed)", "History", "edge", "high",
       anchors=["start and end dates are inclusive", "either boundary date"])
def end_inclusive(ctx: CheckContext):
    rows, by_id = _baseline(ctx)
    for d in _boundary_dates(rows):
        ui = ctx.app.apply_filters(end=d.isoformat())
        ctx.step(f"End date = {d}", shot=True)
        _compare(ctx, f"end={d}", rows, by_id, ui, end=d)


@check("C-HIST-10", "Single-day range (start = end) returns that day's transactions", "History", "edge", "high",
       anchors=["start and end dates are inclusive", "either boundary date"])
def single_day(ctx: CheckContext):
    rows, by_id = _baseline(ctx)
    for d in _boundary_dates(rows):
        ui = ctx.app.apply_filters(start=d.isoformat(), end=d.isoformat())
        ctx.step(f"Range {d} .. {d}", shot=True)
        _compare(ctx, f"{d}..{d}", rows, by_id, ui, start=d, end=d)


@check("C-HIST-11", "No-match filters show a clear empty state (not an error or unrelated rows)", "History",
       "negative", "medium", anchors=["empty result set must show a clear empty state"])
def empty_state(ctx: CheckContext):
    _baseline(ctx)
    ui = ctx.app.apply_filters(start="2099-01-01", end="2099-12-31")
    ctx.step("Filter to a future year with no transactions", shot=True)
    ctx.expect("Empty state is shown", ui["kind"] == "empty", "'No transactions match…' message",
               ui.get("message") or ui["kind"])
    ctx.expect("No rows and no error alert", not ui["rows"] and not ui["state"]["alerts"], "0 rows, no alert",
               f"{len(ui['rows'])} rows, alerts={ui['state']['alerts']}")


@check("C-HIST-12", "Clearing filters restores the unfiltered history", "History", "normal", "medium",
       anchors=["clearing filters restores the unfiltered history"])
def clear_filters(ctx: CheckContext):
    rows, _ = _baseline(ctx)
    ctx.app.apply_filters(type_label="Withdrawal", status_label="Completed")
    ui = ctx.app.clear_filters()
    ctx.step("After 'Clear filters'", shot=True)
    ctx.expect("Same rows as before filtering", {r["id"] for r in ui["rows"]} == {r["id"] for r in rows},
               f"{len(rows)} rows", f"{len(ui['rows'])} rows")
    vals = ctx.s.page.evaluate("() => Array.from(document.querySelectorAll('form select, form input')).map(e => e.value)")
    ctx.expect("Filter controls reset", all(v == "" for v in vals), "all blank", vals)


@check("C-HIST-13", "Start date after end date is handled with an explanation and a way to recover", "History",
       "negative", "low", anchors=["recoverable error must leave the user able to retry"])
def inverted_range(ctx: CheckContext):
    _baseline(ctx)
    ui = ctx.app.apply_filters(start="2025-06-30", end="2025-01-01")
    ctx.step("Start after end", shot=True)
    msg = ui.get("message", "")
    ctx.expect("User gets an explanation (no silent failure)", ui["kind"] in ("error", "empty") and bool(msg),
               "message explaining the range", msg or ui["kind"])
    has_way_out = ctx.s.page.get_by_role("button", name=re.compile("clear|try again", re.I)).count() > 0 or \
        ctx.s.page.get_by_role("link", name=re.compile("dashboard", re.I)).count() > 0
    ctx.expect("User can recover (retry / clear / back to dashboard)", has_way_out, "recovery control",
               "present" if has_way_out else "none")
