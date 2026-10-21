"""Dashboard and accounts: account cards, masking, money formatting, detail view consistency."""
from __future__ import annotations

import re
from decimal import Decimal

from ..oracle import FULL_NUMBER_RE, UUID_RE, decimal_places
from .registry import CheckContext, check


@check("C-DASH-01", "Dashboard shows the checking and savings accounts", "Accounts", "normal", "critical",
       anchors=["dashboard shows the customer's checking and savings"])
def dashboard_accounts(ctx: CheckContext):
    ctx.app.enter_demo()
    cards = ctx.app.read_dashboard()
    ctx.step("Read account cards on the dashboard", shot=True)
    types = sorted(c["type"].lower() for c in cards)
    ctx.expect("Both a checking and a savings account are shown", "checking" in types and "savings" in types,
               "checking + savings", types)
    status, body = ctx.app.api_get("/api/accounts")
    api_n = len((body or {}).get("accounts", []))
    ctx.expect("Every account returned by the API is shown", len(cards) == api_n, f"{api_n} cards",
               f"{len(cards)} cards")


@check("C-DASH-02", "Each account shows name, masked identifier, type and USD balance to 2 decimals", "Accounts",
       "normal", "high", anchors=["each account displays", "balances are displayed to two decimal places"])
def card_fields(ctx: CheckContext):
    ctx.app.enter_demo()
    for c in ctx.app.read_dashboard():
        n = c["name"] or "?"
        ctx.expect(f"[{n}] readable name", bool(re.search(r"[A-Za-z]{3,}", n)) and not UUID_RE.search(n),
                   "human-readable name", n)
        ctx.expect(f"[{n}] masked identifier", bool(re.search(r"[•*xX\u2022]{2,}\s*\d{2,4}$", c["masked"])),
                   "mask like '•••• 1234'", c["masked"] or "(missing)")
        ctx.expect(f"[{n}] account type", c["type"].lower() in ("checking", "savings"), "Checking/Savings",
                   c["type"] or "(missing)")
        bt = c["balance_text"]
        ctx.expect(f"[{n}] balance in USD with exactly 2 decimals",
                   ("$" in bt or "USD" in bt) and decimal_places(bt) == 2, "$#,###.## (USD)", bt)
    ctx.step("Account cards inspected", shot=True)


@check("C-DASH-03", "No full account identifier is exposed in the UI or API payloads", "Accounts", "negative",
       "critical", anchors=["must not expose a full account identifier", "full account identifiers"])
def no_full_identifier(ctx: CheckContext):
    ctx.app.enter_demo()
    cards = ctx.app.read_dashboard()
    pages = ["/dashboard", "/transactions", "/transfer", "/profile"] + [f"/accounts/{c['id']}" for c in cards]
    leaks = []
    for p in pages:
        ctx.s.goto(p)
        text = ctx.s.body_text()
        for m in FULL_NUMBER_RE.findall(text):
            leaks.append(f"{p}: '{m}'")
        for m in re.findall(r"[•*xX\u2022]{2,}\s*(\d+)", text):
            if len(m) > 4:
                leaks.append(f"{p}: mask reveals {len(m)} digits")
    ctx.expect("No 8+ digit account numbers in visible text on any page", not leaks, "no full numbers", leaks)
    status, body = ctx.app.api_get("/api/accounts")
    api_leaks = [f"{k}={v}" for a in (body or {}).get("accounts", []) for k, v in a.items()
                 if k != "id" and isinstance(v, str) and FULL_NUMBER_RE.search(v)]
    ctx.expect("Account API payload carries only masked numbers", not api_leaks, "masked_number only", api_leaks)
    ctx.result.evidence["note"] = ("Internal record UUIDs appear in /accounts/<uuid> URLs; they are database keys, "
                                   "not account numbers, so they are not treated as a leak.")


@check("C-DASH-04", "Selecting an account opens its detail view with matching balance and its own history",
       "Accounts", "normal", "high",
       anchors=["selecting an account opens an account detail view", "account-specific history"])
def account_detail(ctx: CheckContext):
    ctx.app.enter_demo()
    cards = ctx.app.read_dashboard()
    for c in cards:
        ctx.app.nav("Dashboard")
        ctx.s.page.get_by_role("link", name=re.compile(re.escape(c["name"]))).first.click()
        ctx.s.settle()
        st = ctx.app.state()
        ctx.step(f"Open '{c['name']}' from the dashboard", shot=True)
        ctx.expect(f"[{c['name']}] detail view opened", "/accounts/" in ctx.s.page.url and c["name"] in st["h1"],
                   f"/accounts/<id> titled '{c['name']}'", f"url={st['url']} h1={st['h1']!r}")
        detail = ctx.app.read_account_detail(c["id"])
        ctx.expect(f"[{c['name']}] detail balance equals dashboard balance", detail["balance"] == c["balance"],
                   c["balance_text"], detail["balance_text"])
        rows = detail["history"]["rows"]
        api_ids = {t["id"] for t in ctx.app.api_transactions(f"?account_id={c['id']}")}
        ui_ids = {r["id"] for r in rows}
        ctx.expect(f"[{c['name']}] history lists exactly this account's transactions", ui_ids == api_ids,
                   f"{len(api_ids)} rows involving the account", f"{len(ui_ids)} rows; "
                   f"missing={len(api_ids - ui_ids)} extra={len(ui_ids - api_ids)}")
        foreign = [r["description"] for r in rows if c["name"].lower() not in r["account"].lower()]
        ctx.expect(f"[{c['name']}] every row references this account", not foreign, "account column names it",
                   foreign[:5])


@check("C-DASH-05", "Dashboard, detail view and server agree on every balance", "Accounts", "normal", "critical",
       anchors=["must agree with the latest successful transactions"])
def balances_agree(ctx: CheckContext):
    ctx.app.enter_demo()
    cards = ctx.app.read_dashboard()
    api = ctx.app.api_balances()
    for c in cards:
        detail = ctx.app.read_account_detail(c["id"])
        ctx.expect(f"[{c['name']}] dashboard = server", c["balance"] == api.get(c["id"]),
                   f"{api.get(c['id'])}", c["balance_text"])
        ctx.expect(f"[{c['name']}] detail = server", detail["balance"] == api.get(c["id"]),
                   f"{api.get(c['id'])}", detail["balance_text"])
    ctx.result.evidence["server_balances"] = {k: str(v) for k, v in api.items()}
