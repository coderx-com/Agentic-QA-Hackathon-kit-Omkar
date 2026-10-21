"""Profile: only synthetic, masked details; no secrets, tokens, or other users' data."""
from __future__ import annotations

import re

from ..oracle import FULL_NUMBER_RE
from .registry import CheckContext, check

MASKED_EMAIL = re.compile(r"^[^@\s]{0,2}[*•\u2022]{2,}[^@\s]*@[^@\s]+$")


@check("C-PROF-01", "Profile shows the display name and a masked email", "Profile", "normal", "high",
       anchors=["profile view displays only synthetic demo-customer details"])
def profile_details(ctx: CheckContext):
    ctx.app.enter_demo()
    ctx.app.nav("Profile")
    ctx.step("Open Profile", shot=True)
    text = ctx.s.body_text()
    status, me = ctx.app.api_get("/api/me")
    name = (me or {}).get("display_name", "")
    emails = re.findall(r"[^\s@]+@[^\s@]+\.[^\s@]+", text)
    ctx.expect("Display name shown", bool(name) and name in text, name, "present" if name in text else "missing")
    ctx.expect("Email is shown masked", bool(emails) and all(MASKED_EMAIL.match(e) for e in emails),
               "e.g. a***@example.test", emails or "(no email shown)")


@check("C-PROF-02", "No passwords, tokens, full account numbers or other users' data are exposed", "Profile",
       "negative", "critical", anchors=["must not reveal passwords", "authentication tokens"])
def no_secrets(ctx: CheckContext):
    ctx.app.enter_demo()
    ctx.app.nav("Profile")
    cookies = ctx.s.context.cookies()
    token_values = [c["value"] for c in cookies if len(c["value"]) >= 16]
    html = ctx.s.page.content()
    storage = ctx.s.page.evaluate("() => JSON.stringify({l: {...localStorage}, s: {...sessionStorage}})")
    text = ctx.s.body_text()
    leaks = [v[:6] + "…" for v in token_values if v in html or v in storage or v in text]
    ctx.expect("Session token never appears in DOM or web storage", not leaks, "absent", leaks)
    ctx.expect("Session cookie is HttpOnly (not readable by scripts)",
               all(c.get("httpOnly") for c in cookies if len(c["value"]) >= 16), "HttpOnly",
               [f"{c['name']}: httpOnly={c.get('httpOnly')}" for c in cookies])
    ctx.expect("No password or secret wording on the page", not re.search(r"password|secret|api[_ -]?key|bearer",
               text, re.I), "none", re.findall(r"password|secret|api[_ -]?key|bearer", text, re.I))
    ctx.expect("No full account numbers on the page", not FULL_NUMBER_RE.search(text), "none",
               FULL_NUMBER_RE.findall(text))
    status, me = ctx.app.api_get("/api/me")
    extra = sorted(set((me or {}).keys()) - {"id", "display_name", "masked_email"})
    ctx.expect("Profile API returns only display fields", not extra, "id, display_name, masked_email", extra)
    ctx.step("Profile inspected for secrets", shot=True)
