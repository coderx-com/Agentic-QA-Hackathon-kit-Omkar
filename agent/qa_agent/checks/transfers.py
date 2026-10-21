"""Transfers between owned accounts: happy path, history records, persistence, validation, funds, races, faults.

Every money assertion is made against server state captured before and after the action. New transactions
created by other users in the meantime are attributed and subtracted, so a shared hosted target cannot
produce false failures. Any money moved by a defect is returned with a compensating transfer and logged.
"""
from __future__ import annotations

import random
import re
from decimal import Decimal

from ..models import Unverifiable
from ..oracle import money_sign, parse_money
from .registry import CheckContext, check

ZERO = Decimal("0.00")


# ----------------------------------------------------------------------------- helpers
def _accounts(ctx: CheckContext):
    a = ctx.app.accounts_by_type()
    if "checking" not in a or "savings" not in a:
        raise Unverifiable("Could not identify one checking and one savings account")
    return a["checking"], a["savings"]


def _snapshot(ctx: CheckContext):
    return ctx.app.api_balances(), {t["id"] for t in ctx.app.api_transactions()}


def _amount(ctx: CheckContext, salt: str) -> str:
    rnd = random.Random(ctx.tag + salt)
    return f"{rnd.randint(1, 2)}.{rnd.randint(11, 98):02d}"


def _diff(ctx: CheckContext, before_bal, before_ids, own_pred):
    after_bal = ctx.app.api_balances()
    new = [t for t in ctx.app.api_transactions() if t["id"] not in before_ids]
    own = [t for t in new if own_pred(t)]
    foreign = [t for t in new if not own_pred(t)]
    foreign_delta: dict[str, Decimal] = {}
    for t in foreign:
        if t["status"] == "completed":
            for d in t["account_deltas"]:
                foreign_delta[d["account_id"]] = foreign_delta.get(d["account_id"], ZERO) + Decimal(d["amount_delta"])
    net = {a: after_bal.get(a, ZERO) - before_bal.get(a, ZERO) - foreign_delta.get(a, ZERO)
           for a in set(before_bal) | set(after_bal)}
    if foreign:
        ctx.result.evidence["concurrent_activity"] = [f"{t['type']} {t['amount']} {t['status']} memo={t['memo']!r}"
                                                      for t in foreign]
    return after_bal, own, net


def _post_api_transfer(ctx: CheckContext, src: str, dst: str, amount: Decimal, memo: str):
    r = ctx.s.context.request.post(ctx.s.api_origin + "/api/transfers", data={
        "source_account_id": src, "destination_account_id": dst, "amount": f"{amount:.2f}", "memo": memo})
    return r.status


def _compensate(ctx: CheckContext, net: dict[str, Decimal], ck: dict, sv: dict, why: str):
    """Return money moved by a defect so later checks (and other users) see the original balances."""
    d_ck, d_sv = net.get(ck["id"], ZERO), net.get(sv["id"], ZERO)
    if d_ck == ZERO and d_sv == ZERO:
        return
    if d_ck + d_sv != ZERO:
        ctx.side_effect(f"Non-zero-sum balance change ({d_ck:+} / {d_sv:+}) after {why}; not auto-restorable")
        return
    src, dst, amt = (ck, sv, d_ck) if d_ck > 0 else (sv, ck, d_sv)
    status = _post_api_transfer(ctx, src["id"], dst["id"], amt, f"{ctx.tag} compensation for {ctx.result.check_id}")
    ctx.side_effect(f"Restored {amt:.2f} from {src['name']} to {dst['name']} after {why} (POST -> {status})")


def _expect_rejected(ctx: CheckContext, out: dict, label: str):
    msgs = out["field_errors"] + out["alerts"]
    ctx.expect(f"{label}: rejected with a clear message", out["ui_state"] == "error" and bool(msgs),
               "visible validation / error message", msgs or f"no message (ui_state={out['ui_state']})")
    shown = out["statuses"][0].splitlines()[0] if out["statuses"] else out["ui_state"]
    new_bal = [f"{k}: {v}" for k, v in out["receipt"].items() if k.lower().startswith("new ")]
    ctx.expect(f"{label}: no success state shown", out["ui_state"] != "success", "no 'Transfer complete'",
               f"'{shown}'" + (f" with receipt {'; '.join(new_bal)}" if new_bal else ""))
    committed = [p["status"] for p in out["posts"] if 200 <= p["status"] < 300]
    ctx.expect(f"{label}: server did not accept the transfer", not committed,
               "no 2xx from POST /api/transfers", [p["status"] for p in out["posts"]] or "no request sent")


def _rejection_case(ctx: CheckContext, label: str, amount_fn, source: str = "checking", same: bool = False):
    ctx.app.enter_demo()
    ck, sv = _accounts(ctx)
    src = ck if source == "checking" else sv
    dst = src if same else (sv if src is ck else ck)
    before_bal, before_ids = _snapshot(ctx)
    amount = amount_fn(before_bal, src)
    memo = f"{ctx.tag} {ctx.result.check_id}"
    ctx.step(f"Enter the demo and open Transfer; choose From '{src['name']}', To '{dst['name']}', "
             f"Amount '{amount}', Memo '{memo}'")
    out = ctx.app.transfer(src["name"], dst["name"], amount, memo)
    ctx.step("Click 'Transfer funds' and wait for the outcome", shot=True)
    ctx.result.evidence["ui_outcome"] = out
    _expect_rejected(ctx, out, label)
    post_ids = {p["response"]["transaction"]["id"] for p in out["posts"]
                if isinstance(p.get("response"), dict) and "transaction" in p["response"]}
    _, own, net = _diff(ctx, before_bal, before_ids, lambda t: t.get("memo") == memo or t["id"] in post_ids)
    ctx.expect(f"{label}: no transaction record created", not own, "0 new records",
               [f"{t['type']} {t['amount']} {t['status']}" for t in own])
    changed = {a: v for a, v in net.items() if v != ZERO}
    names = {ck["id"]: ck["name"], sv["id"]: sv["name"]}
    ctx.expect(f"{label}: balances unchanged", not changed, "no balance change",
               {names.get(a, a): f"{v:+.2f}" for a, v in changed.items()})
    if changed:
        ctx.app.nav("Dashboard")
        ctx.step("Open the Dashboard and compare balances with the values before the attempt", shot=True)
        _compensate(ctx, net, ck, sv, f"{label} defect")


# ----------------------------------------------------------------------------- form
@check("C-XFER-01", "Transfer form offers labeled source, destination, amount and optional memo", "Transfers",
       "normal", "high", anchors=["transfer form includes"])
def form_fields(ctx: CheckContext):
    ctx.app.enter_demo()
    ck, sv = _accounts(ctx)
    ctx.app.open_transfer()
    ctx.step("Open the Transfer page", shot=True)
    p = ctx.s.page
    for label in ("From", "To", "Amount", "Memo"):
        n = p.get_by_label(re.compile(rf"^{label}", re.I)).count()
        ctx.expect(f"'{label}' control has a label", n >= 1, "labelled control", f"{n} found")
    memo_label = p.locator("label", has_text=re.compile("memo", re.I)).first.inner_text()
    ctx.expect("Memo is marked optional", "optional" in memo_label.lower(), "'(optional)' on memo", memo_label)
    for lab in ("^from", "^to"):
        opts = p.get_by_label(re.compile(lab, re.I)).first.evaluate("e => Array.from(e.options).map(o => o.text)")
        ctx.expect(f"{lab[1:]} list offers both owned accounts", all(any(a["name"] in o for o in opts) for a in (ck, sv)),
                   f"{ck['name']} and {sv['name']}", opts)


# ----------------------------------------------------------------------------- happy path
@check("C-XFER-02", "Valid transfer moves the exact amount atomically and reports success after commit",
       "Transfers", "normal", "critical", mutates=True,
       anchors=["user can transfer funds between", "valid transfer is atomic", "no partial transfer",
                "reports success only after the transfer has been committed"])
def valid_transfer(ctx: CheckContext):
    ctx.app.enter_demo()
    ck, sv = _accounts(ctx)
    before_bal, before_ids = _snapshot(ctx)
    ui_before = {c["id"]: c["balance"] for c in ctx.app.read_dashboard()}
    amt = Decimal(_amount(ctx, "valid"))
    memo = f"{ctx.tag} valid transfer"
    ctx.step(f"Transfer {amt} from {ck['name']} to {sv['name']} with memo '{memo}'")
    out = ctx.app.transfer(ck["name"], sv["name"], f"{amt:.2f}", memo)
    ctx.step("Transfer submitted", shot=True)
    ctx.result.evidence["ui_outcome"] = out
    statuses = [p["status"] for p in out["posts"]]
    ctx.expect("Exactly one POST /api/transfers, committed (2xx)", len(statuses) == 1 and 200 <= statuses[0] < 300,
               "one 201", statuses)
    ctx.expect("UI reports success", out["ui_state"] == "success", "'Transfer complete'", out["ui_state"])
    ctx.expect("Receipt shows the amount", parse_money(out["receipt"].get("Amount", "")) == amt, f"{amt}",
               out["receipt"].get("Amount"))
    ctx.expect("Receipt names source and destination",
               ck["name"] in out["receipt"].get("From", "") and sv["name"] in out["receipt"].get("To", ""),
               f"From {ck['name']} / To {sv['name']}", f"{out['receipt'].get('From')} / {out['receipt'].get('To')}")
    after_bal, own, net = _diff(ctx, before_bal, before_ids, lambda t: t.get("memo") == memo)
    ctx.expect("Exactly one completed transfer record created",
               len(own) == 1 and own[0]["type"] == "transfer" and own[0]["status"] == "completed",
               "1 completed transfer", [f"{t['type']}/{t['status']}/{t['amount']}" for t in own])
    ctx.expect("Source decreased by exactly the amount", net.get(ck["id"]) == -amt, f"-{amt}",
               f"{net.get(ck['id']):+.2f}")
    ctx.expect("Destination increased by exactly the amount", net.get(sv["id"]) == amt, f"+{amt}",
               f"{net.get(sv['id']):+.2f}")
    panel = [parse_money(t) for t in ctx.s.page.get_by_test_id("transfer-balance").all_inner_texts()]
    ctx.app.nav("Dashboard")
    ui_after = {c["id"]: c["balance"] for c in ctx.app.read_dashboard()}
    ctx.step("Dashboard after the transfer", shot=True)
    ctx.expect("Dashboard shows the committed balances", ui_after == after_bal,
               {k[-4:]: v for k, v in after_bal.items()}, {k[-4:]: v for k, v in ui_after.items()})
    ctx.result.evidence.update({"amount": str(amt), "ui_before": {k: str(v) for k, v in ui_before.items()},
                                "server_after": {k: str(v) for k, v in after_bal.items()},
                                "transfer_page_panel": [str(x) for x in panel]})
    if own:
        ctx.run["valid"] = {"memo": memo, "amount": amt, "id": own[0]["id"], "occurred_at": own[0]["occurred_at"],
                            "src": ck, "dst": sv}


@check("C-XFER-03", "Successful transfer appears once in both account histories with all required fields",
       "Transfers", "normal", "high",
       anchors=["creates one completed transfer record", "record shows the amount"])
def record_in_histories(ctx: CheckContext):
    v = ctx.run.get("valid")
    if not v:
        ctx.unverified("Depends on C-XFER-02 creating a transfer, which did not happen")
        return
    ctx.app.enter_demo()
    hist = ctx.app.open_history()
    rows = [r for r in hist["rows"] if v["memo"] in r["description"]]
    ctx.step("Combined history", shot=True)
    ctx.expect("Combined history contains the transfer exactly once", len(rows) == 1, "1 row", f"{len(rows)} rows")
    if rows:
        r = rows[0]
        ctx.expect("Row amount equals transfer amount", abs(parse_money(r["amount_text"]) or 0) == v["amount"],
                   f"{v['amount']}", r["amount_text"])
        ctx.expect("Row shows date and time", bool(re.search(r"\d{1,2}:\d{2}", r["date_text"])) and bool(r["iso"]),
                   "date + time", r["date_text"])
        ctx.expect("Row status is Completed", r["status"].lower() == "completed", "Completed", r["status"])
        ctx.expect("Row type is Transfer", r["type"].lower() == "transfer", "Transfer", r["type"])
        acc = r["account"]
        ok = v["src"]["name"] in acc and v["dst"]["name"] in acc and acc.find(v["src"]["name"]) < acc.find(v["dst"]["name"])
        ctx.expect("Row shows source then destination", ok, f"From {v['src']['name']} To {v['dst']['name']}", acc)
        ctx.expect("Row shows the memo", v["memo"] in r["description"], v["memo"], r["description"])
    for side, sign in (("src", -1), ("dst", 1)):
        acct = v[side]
        detail = ctx.app.read_account_detail(acct["id"])
        found = [r for r in detail["history"]["rows"] if v["memo"] in r["description"]]
        ctx.step(f"{acct['name']} account history", shot=True)
        ctx.expect(f"{acct['name']} history contains the transfer once", len(found) == 1, "1 row", f"{len(found)}")
        if found:
            ctx.expect(f"{acct['name']} row shows direction {'out (−)' if sign < 0 else 'in (+)'}",
                       money_sign(found[0]["amount_text"]) == sign, "−" if sign < 0 else "+", found[0]["amount_text"])


@check("C-XFER-04", "Balances and the transfer record survive a page refresh", "Transfers", "edge", "high",
       anchors=["refreshing after a successful transfer"])
def refresh_after_transfer(ctx: CheckContext):
    v = ctx.run.get("valid")
    if not v:
        ctx.unverified("Depends on C-XFER-02 creating a transfer, which did not happen")
        return
    ctx.app.enter_demo()
    ctx.s.reload()
    ui = {c["id"]: c["balance"] for c in ctx.app.read_dashboard()}
    server = ctx.app.api_balances()
    ctx.step("Dashboard after refresh", shot=True)
    ctx.expect("Refreshed dashboard equals persisted balances", ui == server,
               {k[-4:]: x for k, x in server.items()}, {k[-4:]: x for k, x in ui.items()})
    ctx.app.open_history()
    ctx.s.reload()
    rows = [r for r in ctx.app.read_history_raw()["rows"] if v["memo"] in r["description"]]
    ctx.expect("Transfer record still listed after refresh", len(rows) == 1, "1 row", f"{len(rows)} rows")


@check("C-XFER-05", "Memo is optional: a transfer without memo succeeds and is described in history",
       "Transfers", "normal", "medium", mutates=True, anchors=["optional memo", "memo if supplied"])
def memo_optional(ctx: CheckContext):
    ctx.app.enter_demo()
    ck, sv = _accounts(ctx)
    v = ctx.run.get("valid")
    amt = v["amount"] if v else Decimal(_amount(ctx, "memo"))
    before_bal, before_ids = _snapshot(ctx)
    ctx.step(f"Transfer {amt} from {sv['name']} back to {ck['name']} with an empty memo")
    out = ctx.app.transfer(sv["name"], ck["name"], f"{amt:.2f}", "")
    ctx.step("Submitted without memo", shot=True)
    ids = {p["response"]["transaction"]["id"] for p in out["posts"]
           if isinstance(p.get("response"), dict) and "transaction" in p["response"]}
    ctx.expect("Transfer without memo succeeds", out["ui_state"] == "success" and len(ids) == 1, "success",
               out["ui_state"])
    _, own, net = _diff(ctx, before_bal, before_ids, lambda t: t["id"] in ids)
    ctx.expect("Balances moved by the amount", net.get(sv["id"]) == -amt and net.get(ck["id"]) == amt,
               f"{sv['name']} -{amt}, {ck['name']} +{amt}", {k[-4:]: f"{x:+.2f}" for k, x in net.items()})
    hist = ctx.app.open_history()
    row = next((r for r in hist["rows"] if r["id"] in ids), None)
    ctx.expect("History row has a readable description without memo", bool(row and row["description"].strip()),
               "non-empty description", row["description"] if row else "row not found")
    if v:
        ctx.side_effect(f"This transfer returned the {amt} moved by C-XFER-02")


# ----------------------------------------------------------------------------- negative / edge inputs
@check("C-XFER-06", "Same-account transfer is rejected without side effects", "Transfers", "negative", "high",
       mutates=True, anchors=["source and destination must be different", "same-account transfers must be rejected"])
def same_account(ctx: CheckContext):
    _rejection_case(ctx, "same account", lambda bal, src: "5.00", same=True)


_AMOUNT_CASES = [
    ("C-XFER-07", "blank", "", "Blank amount"),
    ("C-XFER-08", "zero", "0", "Zero amount"),
    ("C-XFER-09", "zero with decimals", "0.00", "Zero amount written as 0.00"),
    ("C-XFER-10", "negative", "-5.00", "Negative amount"),
    ("C-XFER-11", "non-numeric", "abc", "Non-numeric amount"),
    ("C-XFER-12", "over-precision", "10.005", "Amount with three decimal places"),
]


def _register_amount_case(cid: str, label: str, value: str, title: str):
    @check(cid, f"{title} ('{value}') is rejected with a validation message and no side effects", "Transfers",
           "negative" if label not in ("over-precision", "zero with decimals") else "edge",
           "high" if label in ("negative", "zero", "zero with decimals") else "medium", mutates=True,
           anchors=["blank, zero, negative, non-numeric", "amount must be a positive number",
                    "rejected input must not create"])
    def _case(ctx: CheckContext):
        _rejection_case(ctx, label, lambda bal, src: value)


for _c in _AMOUNT_CASES:
    _register_amount_case(*_c)


@check("C-XFER-13", "Transfer above the available balance is rejected (balance + $0.01)", "Transfers", "edge",
       "critical", mutates=True,
       anchors=["must not exceed the source account", "if funds are insufficient"])
def insufficient_funds(ctx: CheckContext):
    _rejection_case(ctx, "insufficient funds",
                    lambda bal, src: f"{bal[src['id']] + Decimal('0.01'):.2f}")


@check("C-XFER-14", "Transfer of exactly the available balance is allowed (boundary)", "Transfers", "edge",
       "medium", mutates=True, anchors=["must not exceed the source account"])
def exact_balance(ctx: CheckContext):
    ctx.app.enter_demo()
    ck, sv = _accounts(ctx)
    before_bal, before_ids = _snapshot(ctx)
    amt = before_bal[ck["id"]]
    if amt <= ZERO:
        ctx.unverified(f"{ck['name']} balance is {amt}; boundary cannot be exercised")
        return
    memo = f"{ctx.tag} exact balance"
    ctx.step(f"Transfer the full available balance {amt} from {ck['name']} to {sv['name']}")
    out = ctx.app.transfer(ck["name"], sv["name"], f"{amt:.2f}", memo)
    ctx.step("Submitted", shot=True)
    ctx.expect("Full-balance transfer accepted", out["ui_state"] == "success", "success", out["alerts"] or out["ui_state"])
    _, own, net = _diff(ctx, before_bal, before_ids, lambda t: t.get("memo") == memo)
    ctx.expect("Source reaches exactly 0.00", before_bal[ck["id"]] + net.get(ck["id"], ZERO) == ZERO, "0.00",
               f"{before_bal[ck['id']] + net.get(ck['id'], ZERO):.2f}")
    _compensate(ctx, net, ck, sv, "boundary transfer (test restore)")


# ----------------------------------------------------------------------------- async / resilience
@check("C-XFER-15", "Slow submission shows a busy state and a double submit creates only one transfer",
       "Transfers", "edge", "high", mutates=True,
       anchors=["prevent accidental duplicate submission", "should not silently disappear",
                "communicate loading, success, validation, and failure"])
def duplicate_submission(ctx: CheckContext):
    ctx.app.enter_demo()
    ck, sv = _accounts(ctx)
    before_bal, before_ids = _snapshot(ctx)
    memo = f"{ctx.tag} double submit"
    ctx.app.fill_transfer(ck["name"], sv["name"], "0.11", memo)
    btn = ctx.app.submit_button()
    box = btn.bounding_box()
    cdp = ctx.s.set_latency(1500)
    n0 = len(ctx.s.api_calls("POST", "/api/transfers"))
    btn.click()
    ctx.s.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    ctx.s.page.wait_for_timeout(250)
    busy = ctx.s.page.evaluate("""() => ({ disabled: !!document.querySelector('form button[type=submit]:disabled'),
        ariaBusy: !!document.querySelector('[aria-busy=true]'),
        text: document.body.innerText.match(/Submitting[^\\n]*/)?.[0] || '' })""")
    ctx.step("Second click while the first request is in flight (1.5 s latency)", shot=True)
    ctx.s.page.get_by_label(re.compile("amount", re.I)).press("Enter")
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1,
                                                   "uploadThroughput": -1})
    ctx.s.settle()
    ctx.expect("Busy/loading state visible while waiting", busy["disabled"] or busy["ariaBusy"] or bool(busy["text"]),
               "disabled button / aria-busy / 'Submitting…'", busy)
    posts = ctx.s.api_calls("POST", "/api/transfers")[n0:]
    ctx.expect("Only one transfer request sent", len(posts) == 1, "1 POST", f"{len(posts)} POSTs")
    _, own, net = _diff(ctx, before_bal, before_ids, lambda t: t.get("memo") == memo)
    ctx.expect("Only one transfer record created", len(own) == 1, "1 record", f"{len(own)} records")
    ctx.expect("Outcome communicated to the user", ctx.app.state()["statuses"] != [] or ctx.app.state()["alerts"] != [],
               "success or error message", ctx.app.state()["statuses"][:1])
    _compensate(ctx, net, ck, sv, "duplicate-submission test (test restore)")


@check("C-XFER-16", "Server/network failure shows 'not completed', no success, and the user can retry",
       "Transfers", "negative", "high", mutates=True,
       anchors=["if a transfer is rejected or fails", "recoverable error must leave the user able to retry"])
def failure_injection(ctx: CheckContext):
    ctx.app.enter_demo()
    ck, sv = _accounts(ctx)
    before_bal, before_ids = _snapshot(ctx)
    memo = f"{ctx.tag} fault injection"
    page = ctx.s.page
    def make_handler(m: str):
        # NB: Playwright passes (route, request) to 2-arg callables, so the mode is bound by closure.
        def handler(route):
            if route.request.method != "POST":
                return route.continue_()
            if m == "http-500":
                return route.fulfill(status=500, headers=ctx.s.cors_headers(),
                                     body='{"error":{"code":"internal_error","message":"Injected by QA agent"}}')
            return route.abort("failed")
        return handler

    for mode in ("http-500", "network-drop"):
        handler = make_handler(mode)
        page.route("**/api/transfers", handler)
        out = ctx.app.transfer(ck["name"], sv["name"], "0.13", memo)
        ctx.step(f"Submit with injected {mode}", shot=True)
        page.unroute("**/api/transfers")
        msgs = " ".join(out["alerts"] + out["field_errors"])
        ctx.expect(f"[{mode}] UI says the transfer did not complete",
                   out["ui_state"] == "error" and re.search(r"not complet|did not|unchanged|could not|try again", msgs, re.I) is not None,
                   "explicit not-completed message", msgs or out["ui_state"])
        ctx.expect(f"[{mode}] no success state", out["ui_state"] != "success", "no success", out["ui_state"])
    _, own, net = _diff(ctx, before_bal, before_ids, lambda t: t.get("memo") == memo)
    ctx.expect("No money moved by failed attempts", not own and all(v == ZERO for v in net.values()),
               "no records, no balance change", f"{len(own)} records")
    ctx.app.submit_button().click()
    ctx.s.settle()
    st = ctx.app.state()
    ctx.step("Retry after the fault is removed", shot=True)
    ok = any(re.search(r"transfer complete", s, re.I) for s in st["statuses"]) or "Transfer complete" in ctx.s.body_text()
    ctx.expect("User can retry from the same screen", ok, "retry succeeds", st["alerts"][:1] or st["statuses"][:1])
    _, own2, net2 = _diff(ctx, before_bal, before_ids, lambda t: t.get("memo") == memo)
    _compensate(ctx, net2, ck, sv, "fault-injection retry (test restore)")
