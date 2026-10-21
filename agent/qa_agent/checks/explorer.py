"""Autonomous exploration: discover the app's pages and controls before testing (no spec knowledge used).

Breadth-first walk of same-origin links from the landing page and dashboard. Destructive controls (log out,
submit buttons) are never clicked here. The resulting site map is compared with the PRD's feature areas so
the report can show what was found, and where each requirement area lives in the UI.
"""
from __future__ import annotations

import re
from collections import deque

from .browser import Session
from .driver import DemoBank

FEATURE_HINTS = {"Access": r"enter demo|log ?out", "Accounts": r"available balance|checking|savings",
                 "Transfers": r"transfer", "History": r"transactions?|filter", "Profile": r"profile"}


def explore(s: Session, max_pages: int = 12) -> dict:
    app = DemoBank(s)
    s.goto("/")
    pages, seen = [], set()
    landing = _describe(s, "/")
    pages.append(landing)
    seen.add("/")
    app.enter_demo()
    queue = deque(["/dashboard"])
    while queue and len(pages) < max_pages:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        s.goto(path)
        info = _describe(s, path)
        pages.append(info)
        for href in info["links"]:
            if href.startswith("/") and href not in seen and not re.search(r"logout", href, re.I):
                queue.append(href.split("#")[0])
    features = {}
    blob = " ".join(p["text"] for p in pages)
    for area, rx in FEATURE_HINTS.items():
        where = [p["path"] for p in pages if re.search(rx, p["text"], re.I)]
        features[area] = where
    return {"pages": [{k: v for k, v in p.items() if k != "text"} for p in pages], "features": features,
            "api_origin": s.api_origin, "note": f"{len(pages)} pages visited, {len(blob)} chars of UI text read"}


def _describe(s: Session, path: str) -> dict:
    data = s.page.evaluate(r"""() => ({
        title: document.title, h1: (document.querySelector('h1') || {}).innerText || '',
        links: Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href')),
        buttons: Array.from(document.querySelectorAll('button')).map(b => b.innerText.trim()).filter(Boolean),
        inputs: Array.from(document.querySelectorAll('input,select,textarea')).map(e =>
            (e.labels && e.labels[0] ? e.labels[0].innerText.trim() : e.getAttribute('aria-label') || e.name || e.id)),
        text: document.body.innerText.slice(0, 4000)})""")
    data["path"] = path
    data["screenshot"] = s.screenshot(f"explore {path}")
    data["links"] = sorted(set(l for l in data["links"] if l))
    return data
