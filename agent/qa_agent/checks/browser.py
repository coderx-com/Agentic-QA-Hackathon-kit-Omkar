"""Resilient browser layer (rubric: autonomous resilience, evidence).

* Multi-strategy locators: test-id -> ARIA role/name -> label -> visible text, tried in order.
* Self-healing: if every strategy misses, the current ARIA snapshot is sent to the LLM (or a fuzzy matcher
  when offline) to pick the intended control; heals are logged in the report.
* Settling: waits for in-flight API calls and "Loading/Checking/Submitting" states instead of fixed sleeps.
* Evidence: per-step screenshots, full API network log (request + JSON response) and console errors.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, BrowserContext, Locator, Page
from playwright.sync_api import TimeoutError as PWTimeout

from .llm import LLM
from .models import AgentError, Step

BUSY_TEXT = re.compile(r"(Loading|Checking your demo session|Submitting transfer|Entering demo|Logging out)", re.I)


@dataclass
class NetEvent:
    method: str
    url: str
    status: int
    request_body: str | None
    response: object
    t: float

    def short(self) -> str:
        return f"{self.method} {self.url.split('://', 1)[-1]} -> {self.status}"


@dataclass
class Session:
    browser: Browser
    base_url: str
    evidence_dir: Path
    check_id: str
    llm: LLM
    viewport: tuple[int, int] = (1280, 800)
    trace: bool = False
    context: BrowserContext = field(init=False)
    page: Page = field(init=False)
    steps: list[Step] = field(default_factory=list)
    net: list[NetEvent] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    heals: list[str] = field(default_factory=list)
    api_origin: str | None = None
    _shot_n: int = 0

    def __post_init__(self):
        self.context = self.browser.new_context(viewport={"width": self.viewport[0], "height": self.viewport[1]})
        self.context.set_default_timeout(10_000)
        if self.trace:
            self.context.tracing.start(screenshots=True, snapshots=True)
        self.page = self.context.new_page()
        self.page.on("response", self._on_response)
        self.page.on("console", lambda m: m.type == "error" and self.console_errors.append(m.text[:300]))
        self.page.on("pageerror", lambda e: self.console_errors.append(f"pageerror: {str(e)[:300]}"))

    # ------------------------------------------------------------------ evidence
    def _on_response(self, resp):
        from urllib.parse import urlsplit
        url = resp.url
        parts = urlsplit(url)
        # Only real API paths count (dev servers also serve files like /src/api/client.ts)
        is_api = parts.path.startswith("/api/") or parts.path.rstrip("/") == "/health"
        if not is_api:
            if not url.startswith(self.base_url) and not url.startswith(("data:", "blob:")):
                self.net.append(NetEvent(resp.request.method, url, resp.status, None, None, time.time()))
            return
        if parts.path.startswith("/api/") and self.api_origin is None:
            self.api_origin = f"{parts.scheme}://{parts.netloc}"
        body = None
        try:
            if resp.request.method != "OPTIONS" and resp.status != 204:
                body = resp.json()
        except Exception:
            body = None
        self.net.append(NetEvent(resp.request.method, url, resp.status, resp.request.post_data, body, time.time()))

    def api_calls(self, method: str | None = None, contains: str = "") -> list[NetEvent]:
        return [e for e in self.net if (method is None or e.method == method) and contains in e.url
                and e.method != "OPTIONS"]

    def step(self, text: str, shot: bool = False) -> Step:
        s = Step(text=text, screenshot=self.screenshot(text) if shot else None)
        self.steps.append(s)
        return s

    def screenshot(self, label: str = "", full_page: bool = True) -> str:
        self._shot_n += 1
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "shot"
        rel = Path("screenshots") / f"{self.check_id}-{self._shot_n:02d}-{slug}.png"
        try:
            self.page.screenshot(path=str(self.evidence_dir / rel), full_page=full_page)
        except Exception:
            return ""
        return rel.as_posix()

    def close(self) -> str | None:
        trace_rel = None
        try:
            if self.trace:
                trace_rel = f"traces/{self.check_id}.zip"
                self.context.tracing.stop(path=str(self.evidence_dir / trace_rel))
        finally:
            self.context.close()
        return trace_rel

    # ------------------------------------------------------------------ navigation / waiting
    def url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def goto(self, path: str):
        self.page.goto(self.url(path), wait_until="domcontentloaded")
        self.settle()

    def reload(self):
        self.page.reload(wait_until="domcontentloaded")
        self.settle()

    def settle(self, timeout_s: float = 12.0, quiet_ms: int = 350):
        """Wait until the network is idle and no busy indicator is visible, and the DOM text is stable."""
        deadline = time.time() + timeout_s
        try:
            self.page.wait_for_load_state("networkidle", timeout=int(timeout_s * 1000))
        except PWTimeout:
            pass
        last, stable_since = None, time.time()
        while time.time() < deadline:
            try:
                text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                time.sleep(0.1)
                continue
            if BUSY_TEXT.search(text or ""):
                stable_since, last = time.time(), None
            elif text != last:
                last, stable_since = text, time.time()
            elif (time.time() - stable_since) * 1000 >= quiet_ms:
                return
            time.sleep(0.08)

    def body_text(self) -> str:
        return self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""

    def cors_headers(self) -> dict:
        """Headers an injected cross-origin API response needs so the browser hands it to the app."""
        origin = "/".join(self.base_url.split("/")[:3])
        return {"access-control-allow-origin": origin, "access-control-allow-credentials": "true",
                "content-type": "application/json"}

    def set_latency(self, ms: int):
        """Chromium network emulation: makes async states observable without touching the server."""
        cdp = self.context.new_cdp_session(self.page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": ms,
                                                       "downloadThroughput": -1, "uploadThroughput": -1})
        return cdp

    # ------------------------------------------------------------------ resilient locating
    def find(self, intent: str, strategies: list[Callable[[Page], Locator]], timeout_ms: int = 6000,
             heal_role: str | None = None) -> Locator:
        per = max(800, timeout_ms // max(1, len(strategies)))
        for build in strategies:
            try:
                loc = build(self.page)
                loc.first.wait_for(state="visible", timeout=per)
                return loc.first
            except (PWTimeout, Exception):
                continue
        healed = self._heal(intent, heal_role)
        if healed is not None:
            return healed
        shot = self.screenshot(f"locator-miss-{intent}")
        raise AgentError(f"Could not locate '{intent}' with {len(strategies)} strategies or self-healing "
                         f"(screenshot: {shot})")

    def _heal(self, intent: str, role_hint: str | None) -> Locator | None:
        try:
            snapshot = self.page.locator("body").aria_snapshot()
        except Exception:
            return None
        choice = None
        if self.llm.enabled:
            choice = self.llm.complete_json(
                "heal_locator",
                "You map a UI intent to one element in an ARIA snapshot. Reply with JSON only: "
                '{"role": "<aria role>", "name": "<exact accessible name>"} or {"role": null}.',
                f"Intent: {intent}\nPreferred role: {role_hint or 'any'}\nARIA snapshot:\n{snapshot[:6000]}",
                max_tokens=1500)
        if not choice or not choice.get("role"):
            choice = _fuzzy_pick(intent, snapshot, role_hint)
        if not choice or not choice.get("role"):
            return None
        loc = self.page.get_by_role(choice["role"], name=choice["name"])
        try:
            loc.first.wait_for(state="visible", timeout=2500)
        except Exception:
            return None
        self.heals.append(f"'{intent}' healed to role={choice['role']} name='{choice['name']}'")
        return loc.first


def _fuzzy_pick(intent: str, snapshot: str, role_hint: str | None):
    best, best_score = None, 0.0
    for m in re.finditer(r'-\s+(button|link|textbox|combobox|heading)\s+"([^"]+)"', snapshot):
        role, name = m.group(1), m.group(2)
        if role_hint and role != role_hint:
            continue
        score = SequenceMatcher(None, intent.lower(), name.lower()).ratio()
        if score > best_score:
            best, best_score = {"role": role, "name": name}, score
    return best if best_score >= 0.55 else None


def jdump(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
