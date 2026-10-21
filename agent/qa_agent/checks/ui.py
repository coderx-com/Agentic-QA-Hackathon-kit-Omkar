"""General interface behaviour: viewports, labels, navigation, recoverable errors, loading states, safety."""
from __future__ import annotations

import re
import uuid

from ..browser import Session
from .registry import CheckContext, check

PAGES = [("/dashboard", "Dashboard"), ("/transactions", "Transactions"), ("/transfer", "Transfer"),
         ("/profile", "Profile")]
_OVERFLOW_JS = "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})"


def _viewport_check(ctx: CheckContext, w: int, h: int, label: str):
    ctx.s.page.set_viewport_size({"width": w, "height": h})
    ctx.app.enter_demo()
    for path, name in PAGES:
        ctx.s.goto(path)
        o = ctx.s.page.evaluate(_OVERFLOW_JS)
        ctx.step(f"{label}: {path}", shot=(path in ("/dashboard", "/transactions")))
        ctx.expect(f"{label} {path}: no horizontal overflow", o["sw"] <= o["cw"] + 2, f"scrollWidth <= {o['cw']}",
                   f"scrollWidth={o['sw']}")
        nav_ok = all(ctx.s.page.get_by_role("link", name=n, exact=True).first.is_visible() for _, n in PAGES)
        ctx.expect(f"{label} {path}: main navigation visible", nav_ok, "4 nav links visible",
                   "visible" if nav_ok else "hidden/cropped")
    ctx.s.goto("/transfer")
    amt = ctx.s.page.get_by_label(re.compile("amount", re.I))
    amt.fill("1.00")
    ctx.expect(f"{label}: transfer form operable", amt.input_value() == "1.00" and amt.is_visible(),
               "amount field usable", amt.input_value())


@check("C-UI-01", "Pages render correctly at the 1280×800 desktop viewport", "Interface", "normal", "medium",
       anchors=["works in a current desktop browser"])
def desktop(ctx: CheckContext):
    _viewport_check(ctx, 1280, 800, "1280x800")


@check("C-UI-02", "Pages remain usable at narrow widths (768 px and 390 px)", "Interface", "edge", "medium",
       anchors=["remains usable at narrower widths"])
def narrow(ctx: CheckContext):
    _viewport_check(ctx, 768, 900, "768x900")
    _viewport_check(ctx, 390, 844, "390x844")


@check("C-UI-03", "Every interactive control has a readable accessible label", "Interface", "normal", "low",
       anchors=["interactive controls have readable labels"])
def labels(ctx: CheckContext):
    ctx.app.enter_demo()
    unlabeled = []
    for path, _ in PAGES:
        ctx.s.goto(path)
        found = ctx.s.page.evaluate(r"""() => Array.from(document.querySelectorAll('a,button,input,select,textarea'))
          .filter(e => e.offsetParent !== null || e.getClientRects().length)
          .filter(e => {
            const byLabel = e.labels && Array.from(e.labels).some(l => l.innerText.trim());
            const aria = (e.getAttribute('aria-label') || '').trim();
            const lb = (e.getAttribute('aria-labelledby') || '').split(' ').map(i => document.getElementById(i))
                       .filter(Boolean).some(n => n.innerText.trim());
            const own = ['A','BUTTON'].includes(e.tagName) && e.innerText.trim();
            return !(byLabel || aria || lb || own);
          }).map(e => e.outerHTML.slice(0, 120))""")
        unlabeled += [f"{path}: {h}" for h in found]
    ctx.expect("No unlabeled controls on the main pages", not unlabeled, "0 unlabeled", unlabeled[:6])


@check("C-UI-04", "Navigation links and buttons lead to their labeled destinations", "Interface", "normal",
       "medium", anchors=["navigation links and buttons lead to the labeled destination"])
def navigation(ctx: CheckContext):
    ctx.app.enter_demo()
    p = ctx.s.page
    for path, name in PAGES:
        ctx.app.nav(name)
        h1 = ctx.app.state()["h1"]
        ctx.expect(f"Nav '{name}' opens {path}", p.url.split("?")[0].endswith(path), path, p.url)
    ctx.app.nav("Dashboard")
    for label, dest in (("Transfer funds", "/transfer"), ("View all transactions", "/transactions")):
        ctx.s.find(label, [lambda pg, l=label: pg.get_by_role("link", name=l)]).click()
        ctx.s.settle()
        ctx.expect(f"Dashboard button '{label}' opens {dest}", dest in p.url, dest, p.url)
        ctx.app.nav("Dashboard")
    acct = ctx.app.accounts_by_type()["savings"]
    ctx.app.read_account_detail(acct["id"])
    p.get_by_role("link", name=re.compile("transfer from this account", re.I)).click()
    ctx.s.settle()
    src = p.get_by_label(re.compile("^from", re.I)).first.evaluate("e => e.options[e.selectedIndex].text")
    ctx.step("'Transfer from this account' from the savings detail view", shot=True)
    ctx.expect("'Transfer from this account' pre-selects that account", acct["name"] in src, acct["name"], src)
    ctx.app.read_account_detail(acct["id"])
    p.get_by_role("link", name=re.compile("filter full history", re.I)).click()
    ctx.s.settle()
    sel = p.get_by_label(re.compile("^account$", re.I)).first.evaluate("e => e.options[e.selectedIndex].text")
    ctx.expect("'Filter full history' opens history filtered to that account", acct["name"] in sel, acct["name"], sel)


@check("C-UI-05", "Recoverable errors offer retry or a way back to the dashboard", "Interface", "negative",
       "medium", anchors=["recoverable error must leave the user able to retry"])
def recoverable(ctx: CheckContext):
    ctx.app.enter_demo()
    p = ctx.s.page
    for path, why in ((f"/accounts/{uuid.uuid4()}", "unknown account"), ("/accounts/not-an-id", "malformed id"),
                      ("/no-such-page", "unknown route")):
        ctx.s.goto(path)
        ctx.step(f"Open {path} ({why})", shot=True)
        main = p.get_by_role("main")   # the error view itself, not the header navigation
        back = main.get_by_role("link", name=re.compile("dashboard", re.I))
        retry = main.get_by_role("button", name=re.compile("try again|retry", re.I))
        ctx.expect(f"{why}: error state offers a way out", back.count() + retry.count() > 0,
                   "Back to dashboard / Try again", f"{back.count()} link(s), {retry.count()} button(s)")
        if back.count():
            back.last.click()
            ctx.s.settle()
            ctx.expect(f"{why}: 'back' returns to a working dashboard", "/dashboard" in p.url and
                       ctx.app.is_protected_content_visible(), "/dashboard", p.url)
    p.route("**/api/accounts", lambda r: r.fulfill(status=503, headers=ctx.s.cors_headers(),
                                                   body='{"error":{"code":"x","message":"injected"}}')
            if r.request.method == "GET" else r.continue_())
    ctx.s.goto("/dashboard")
    ctx.step("Dashboard while the accounts API fails (injected 503)", shot=True)
    retry = p.get_by_role("button", name=re.compile("try again|retry", re.I))
    ctx.expect("Failed dashboard load offers retry", retry.count() > 0, "Try again button", f"{retry.count()}")
    p.unroute("**/api/accounts")
    if retry.count():
        retry.first.click()
        ctx.s.settle()
        ctx.expect("Retry recovers once the API is back", ctx.app.is_protected_content_visible(), "accounts shown",
                   p.url)


@check("C-UI-06", "Slow responses show a loading state instead of a blank or stale page", "Interface", "edge",
       "low", anchors=["communicate loading", "while waiting, the ui should show a loading state"])
def loading(ctx: CheckContext):
    ctx.app.enter_demo()
    cdp = ctx.s.set_latency(1500)
    seen = {}
    for path, name in (("/transactions", "Transactions"), ("/profile", "Profile")):
        ctx.s.page.get_by_role("link", name=name, exact=True).first.click()
        ctx.s.page.wait_for_timeout(400)
        txt = ctx.s.body_text()
        seen[path] = bool(re.search(r"loading", txt, re.I)) or ctx.s.page.locator("[aria-busy=true]").count() > 0
        ctx.step(f"{path} 400 ms into a 1.5 s response", shot=True)
        ctx.s.settle()
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1,
                                                   "uploadThroughput": -1})
    ctx.expect("Loading indicator visible while waiting", all(seen.values()), "loading on every page", seen)


@check("C-SAFE-01", "Browser only talks to the DemoBank app and API (no external bank / payment hosts)",
       "Safety", "negative", "critical",
       anchors=["must never initiate real payments or connect to an external bank", "real bank integrations"])
def no_external(ctx: CheckContext):
    ctx.app.enter_demo()
    for path, _ in PAGES:
        ctx.s.goto(path)
    allowed = {ctx.s.base_url.split("/")[2], (ctx.s.api_origin or "").split("/")[-1]}
    hosts = sorted({e.url.split("/")[2] for e in ctx.s.net if e.url.startswith("http")} - allowed)
    ctx.expect("No requests to third-party hosts while using every page", not hosts, f"only {sorted(allowed)}", hosts)
    ctx.result.evidence["limitation"] = "Client-side observation only; server-side outbound calls are not observable."


OOS = r"sign ?up|register|create account|forgot password|reset password|bill ?pay|pay (a )?bill|loan|credit card|" \
      r"debit card|invest|budget|joint account|add (a )?payee|external (account|bank)|link (a )?bank|app store|google play"


@check("C-OOS-01", "No out-of-scope features (signup, bill-pay, loans, cards, external payments) are offered",
       "Scope", "negative", "low", anchors=["demo has no signup", "out of scope (must not be offered)"])
def out_of_scope(ctx: CheckContext):
    ctx.s.goto("/")
    found = [f"/: {m}" for m in re.findall(OOS, ctx.s.body_text(), re.I)]
    ctx.app.enter_demo()
    for path, _ in PAGES:
        ctx.s.goto(path)
        controls = " | ".join(ctx.s.page.evaluate(
            "() => Array.from(document.querySelectorAll('a,button')).map(e => e.innerText.trim())"))
        found += [f"{path}: {m if isinstance(m, str) else m[0]}" for m in re.findall(OOS, controls, re.I)]
    ctx.expect("No controls for out-of-scope features", not found, "none", found[:6])
