"""Seed data expectations: one customer, one checking, one savings, varied history incl. an internal transfer."""
from __future__ import annotations

from ..oracle import to_pacific_date
from .registry import CheckContext, check


@check("C-SEED-01", "Seed data: one customer, checking + savings, varied dates/types/statuses, an internal transfer",
       "Seed data", "normal", "medium",
       anchors=["target has one seeded demo customer", "the demo begins with one customer",
                "seed records should include multiple dates"])
def seed(ctx: CheckContext):
    ctx.app.enter_demo()
    h = ctx.app.open_history()
    rows = h["rows"]
    ctx.step("Unfiltered history used as seed inventory", shot=True)
    accts = list(ctx.app.accounts_by_type().values())
    status, body = ctx.app.api_get("/api/accounts")
    types = sorted(a["type"] for a in (body or {}).get("accounts", []))
    ctx.expect("Exactly one checking and one savings account", types == ["checking", "savings"],
               "checking, savings", types)
    status, me = ctx.app.api_get("/api/me")
    ctx.app.enter_demo()
    status2, me2 = ctx.app.api_get("/api/me")
    ctx.expect("Every new demo session is the same single customer", me and me2 and me["id"] == me2["id"],
               "same customer id", f"{(me or {}).get('display_name')} / {(me2 or {}).get('display_name')}")
    dates = {to_pacific_date(r["iso"]) for r in rows if r["iso"]}
    ctx.expect("History spans multiple dates", len(dates) >= 2, ">= 2 dates", f"{len(dates)} dates")
    ctx.expect("All transaction types present", {r["type"] for r in rows} >= {"Deposit", "Withdrawal", "Transfer"},
               "Deposit, Withdrawal, Transfer", sorted({r["type"] for r in rows}))
    ctx.expect("All statuses present", {r["status"] for r in rows} >= {"Completed", "Pending", "Failed"},
               "Completed, Pending, Failed", sorted({r["status"] for r in rows}))
    names = [a["name"] for a in accts]
    internal = [r for r in rows if r["type"] == "Transfer" and all(n in r["account"] for n in names)]
    ctx.expect("At least one transfer between the two accounts", len(internal) >= 1, ">= 1", len(internal))
