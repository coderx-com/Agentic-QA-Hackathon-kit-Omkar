"""Users and access: Enter Demo, header identity, refresh persistence, logout and route protection."""
from __future__ import annotations

import re

from ..driver import PROTECTED_ROUTES
from .registry import CheckContext, check


def _on_landing(ctx: CheckContext) -> bool:
    path = ctx.s.page.url.split("://", 1)[-1].split("/", 1)[-1].split("?")[0]
    has_enter = ctx.s.page.get_by_role("button", name=re.compile("enter demo", re.I)).count() > 0
    return (path in ("", "/") or has_enter) and not ctx.app.is_protected_content_visible()


@check("C-ACC-01", "Enter Demo is clearly labeled and opens the dashboard without credentials", "Access", "normal",
       "critical", anchors=["clearly labeled enter demo", "without requiring a password",
                            "user should be able to enter a demo session"])
def enter_demo(ctx: CheckContext):
    ctx.s.goto("/")
    ctx.step("Open the landing page", shot=True)
    btn = ctx.s.page.get_by_role("button", name=re.compile(r"^\s*enter demo\s*$", re.I))
    ctx.expect("Landing page has a button whose accessible name is 'Enter Demo'", btn.count() == 1,
               "exactly one 'Enter Demo' button", f"{btn.count()} matching button(s)")
    pw = ctx.s.page.locator("input[type=password], input[type=email], input[autocomplete*=username]").count()
    ctx.expect("No password or identity fields are required", pw == 0, "0 credential inputs", f"{pw} credential inputs")
    ctx.step("Click 'Enter Demo'")
    ctx.app.enter_demo()
    cards = ctx.app.read_dashboard()
    ctx.step("Dashboard reached", shot=True)
    ctx.expect("Enter Demo lands on the dashboard with accounts", "/dashboard" in ctx.s.page.url and len(cards) > 0,
               "URL /dashboard with account cards", f"URL {ctx.s.page.url}, {len(cards)} account card(s)")


@check("C-ACC-02", "Header identifies the active demo customer and offers Log out", "Access", "normal", "high",
       anchors=["header identifies the active demo customer"])
def header_identity(ctx: CheckContext):
    ctx.app.enter_demo()
    status, me = ctx.app.api_get("/api/me")
    name = (me or {}).get("display_name", "")
    banner = ctx.s.page.get_by_role("banner")
    header_text = banner.first.inner_text() if banner.count() else ctx.s.body_text()[:300]
    ctx.step("Read the page header", shot=True)
    ctx.expect("Header shows the session customer's display name", bool(name) and name in header_text,
               f"header contains '{name}' (from GET /api/me)", f"header text: {header_text.strip()[:120]!r}")
    lo = ctx.s.page.get_by_role("button", name=re.compile("log ?out", re.I))
    ctx.expect("Header provides a Log out action", lo.count() >= 1 and lo.first.is_visible(),
               "visible 'Log out' button", f"{lo.count()} button(s)")


@check("C-ACC-03", "Refreshing protected pages keeps a valid session", "Access", "edge", "high",
       anchors=["refreshing while in a valid demo session"])
def refresh_keeps_session(ctx: CheckContext):
    ctx.app.enter_demo()
    for label, path in (("Dashboard", "/dashboard"), ("Transactions", "/transactions"), ("Transfer", "/transfer"),
                        ("Profile", "/profile")):
        ctx.s.goto(path)
        ctx.s.reload()
        ctx.step(f"Reload {path}")
        ok = path in ctx.s.page.url and ctx.app.is_protected_content_visible()
        ctx.expect(f"{label} still usable after browser refresh", ok, f"still on {path} with content",
                   f"url={ctx.s.page.url}", shot=(path == "/dashboard"))


@check("C-ACC-04", "Log out returns the user to the landing page", "Access", "normal", "high",
       anchors=["logging out returns the user to the landing page"])
def logout_to_landing(ctx: CheckContext):
    ctx.app.enter_demo()
    ctx.step("Click 'Log out' in the header")
    ctx.app.logout()
    ctx.step("After logout", shot=True)
    ctx.expect("Landing page shown after logout", _on_landing(ctx), "landing page with 'Enter Demo'",
               f"url={ctx.s.page.url}")
    status, _ = ctx.app.api_get("/api/me")
    ctx.expect("Session is ended on the server", status == 401, "GET /api/me -> 401", f"GET /api/me -> {status}")


@check("C-ACC-05", "Protected views are not reachable by direct navigation after logout", "Access", "negative",
       "critical", anchors=["protected dashboard views should not remain usable"])
def protected_after_logout(ctx: CheckContext):
    ctx.app.enter_demo()
    acct_id = ctx.app.read_dashboard()[0]["id"]
    ctx.app.logout()
    for path in PROTECTED_ROUTES + [f"/accounts/{acct_id}"]:
        ctx.s.goto(path)
        ctx.step(f"Navigate directly to {path} after logout")
        ctx.expect(f"{path} is blocked after logout", _on_landing(ctx) or "access" in ctx.s.body_text().lower()
                   and not ctx.app.is_protected_content_visible(),
                   "redirect to landing / access-required", f"url={ctx.s.page.url}",
                   shot=(path == "/dashboard"))
    status, _ = ctx.app.api_get("/api/accounts")
    ctx.expect("Account API rejects the logged-out browser", status == 401, "GET /api/accounts -> 401",
               f"-> {status}")


@check("C-ACC-06", "Refresh and Back do not restore access after logout", "Access", "edge", "critical",
       anchors=["must not regain access merely by refreshing"])
def no_regain_on_refresh(ctx: CheckContext):
    ctx.app.enter_demo()
    ctx.app.nav("Transactions")
    ctx.app.logout()
    ctx.s.page.go_back()
    ctx.s.settle()
    ctx.step("Press browser Back after logout", shot=True)
    ctx.expect("Back button does not show protected data", not ctx.app.is_protected_content_visible(),
               "no balances / history visible", f"url={ctx.s.page.url}")
    ctx.s.reload()
    ctx.step("Refresh the protected URL", shot=True)
    ctx.expect("Refreshing a protected URL does not regain access", _on_landing(ctx),
               "landing / access-required", f"url={ctx.s.page.url}")


@check("C-ACC-07", "Server revokes the session: replaying the old session cookie is rejected", "Access",
       "negative", "critical", anchors=["protected dashboard views should not remain usable"])
def cookie_replay(ctx: CheckContext):
    ctx.app.enter_demo()
    cookies = ctx.s.context.cookies()
    ctx.app.logout()
    other = ctx.s.browser.new_context()
    try:
        other.add_cookies(cookies)
        r = other.request.get(ctx.s.api_origin + "/api/me")
        ctx.step(f"Replay pre-logout cookie(s) [{', '.join(c['name'] for c in cookies)}] in a fresh browser")
        ctx.expect("Pre-logout cookie no longer authorises API calls", r.status == 401,
                   "GET /api/me -> 401", f"-> {r.status}")
        page = other.new_page()
        page.goto(ctx.s.url("/dashboard"))
        page.wait_for_timeout(1500)
        shot = f"screenshots/{ctx.result.check_id}-replay-dashboard.png"
        page.screenshot(path=str(ctx.s.evidence_dir / shot))
        ctx.result.screenshots.append(shot)
        blocked = "/dashboard" not in page.url or "Available balance" not in page.inner_text("body")
        ctx.expect("Dashboard is blocked for the replayed cookie", blocked, "redirect to landing",
                   f"url={page.url}")
    finally:
        other.close()


@check("C-ACC-08", "A visitor without a session is redirected from protected routes", "Access", "negative",
       "high", anchors=["direct navigation to them should return the user to the landing page"])
def fresh_visitor(ctx: CheckContext):
    for path in PROTECTED_ROUTES:
        ctx.s.goto(path)
        ctx.step(f"Open {path} without entering the demo")
        ctx.expect(f"{path} requires a session", _on_landing(ctx), "landing / access-required state",
                   f"url={ctx.s.page.url}", shot=(path == "/transfer"))
