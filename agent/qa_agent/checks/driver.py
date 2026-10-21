"""Stage 3 - EXECUTE: reusable DemoBank 'skills' built on the resilient browser layer.

Skills read the UI semantically (roles, labels, table headers, test-ids as one strategy among several) and
return plain observations. They never decide verdicts; checks compare observations with PRD expectations.
"""
from __future__ import annotations

import re
import time
from decimal import Decimal

from playwright.sync_api import Page

from .browser import Session
from .models import AgentError, Unverifiable
from .oracle import parse_money

PROTECTED_ROUTES = ["/dashboard", "/transactions", "/transfer", "/profile"]

_READ_CARDS_JS = r"""
() => {
  let cards = Array.from(document.querySelectorAll('[data-testid="account-card"]'));
  if (!cards.length) {
    // Semantic fallback: any element showing exactly ONE balance; keep only the innermost such elements
    // (a grid wrapping two cards would otherwise be read as a third, duplicated card).
    const moneyCount = el => (el.innerText.match(/\$\s?[\d,]+\.\d{2}/g) || []).length;
    const cand = Array.from(document.querySelectorAll('main a, main article, main li, main section, main div'))
      .filter(el => /balance/i.test(el.innerText) && moneyCount(el) === 1 && el.innerText.length < 400);
    cards = cand.filter(el => !cand.some(o => o !== el && el.contains(o)));
  }
  return cards.map(el => {
    const lines = el.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    const href = el.getAttribute('href') || (el.querySelector('a') && el.querySelector('a').getAttribute('href')) || '';
    const heading = el.querySelector('h1,h2,h3,h4');
    const bal = el.querySelector('[data-testid="account-balance"]');
    return { lines, href, name: heading ? heading.innerText.trim() : lines[0],
             balance_text: bal ? bal.innerText.trim() : (lines.find(l => /\$\s?[\d,]+\.\d/.test(l)) || ''),
             html: el.outerHTML.slice(0, 3000) };
  });
}
"""

_READ_TABLE_JS = r"""
() => {
  const table = document.querySelector('[data-testid="transaction-table"] table, table[data-testid="transaction-table"]')
               || document.querySelector('main table');
  if (!table) return null;
  const heads = Array.from(table.querySelectorAll('thead th')).map(th => th.innerText.trim().toLowerCase());
  const col = (re) => heads.findIndex(h => re.test(h));
  const idx = { date: col(/date|time/), desc: col(/desc|memo/), type: col(/type/), account: col(/account/),
                amount: col(/amount/), status: col(/status/) };
  const rows = Array.from(table.querySelectorAll('tbody tr')).map((tr, i) => {
    const cells = Array.from(tr.querySelectorAll('td,th'));
    const cell = (k) => idx[k] >= 0 && cells[idx[k]] ? cells[idx[k]] : null;
    const txt = (k) => { const c = cell(k); return c ? c.innerText.replace(/\s+/g, ' ').trim() : ''; };
    const timeEl = tr.querySelector('time[datetime]');
    const amountCell = cell('amount');
    let amountVisible = '';
    if (amountCell) {
      const clone = amountCell.cloneNode(true);
      clone.querySelectorAll('.visually-hidden, .sr-only').forEach(n => n.remove());
      amountVisible = clone.innerText.trim();
    }
    return { id: tr.getAttribute('data-transaction-id') || ('row-' + i), iso: timeEl ? timeEl.getAttribute('datetime') : '',
             date_text: txt('date'), description: txt('desc'), type: txt('type'), account: txt('account'),
             amount_text: amountVisible, amount_full: txt('amount'), status: txt('status') };
  });
  return { headers: heads, rows };
}
"""

_PAGE_STATE_JS = r"""
() => {
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const grab = sel => Array.from(document.querySelectorAll(sel)).filter(vis).map(e => e.innerText.trim()).filter(Boolean);
  const invalid = Array.from(document.querySelectorAll('[aria-invalid="true"]')).map(el => {
    const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
    const msgs = ids.map(id => document.getElementById(id)).filter(Boolean).map(n => n.innerText.trim())
                    .filter(t => t && !/^Up to two decimal places/i.test(t));
    return { field: el.id || el.name, messages: msgs };
  });
  return { alerts: grab('[role="alert"]'), statuses: grab('[role="status"]'), invalid,
           h1: (document.querySelector('h1') || {}).innerText || '', url: location.pathname + location.search };
}
"""


class DemoBank:
    def __init__(self, s: Session):
        self.s = s
        self.page: Page = s.page

    # ---------------------------------------------------------------- session
    def enter_demo(self):
        self.s.goto("/")
        btn = self.s.find("Enter Demo", [
            lambda p: p.get_by_role("button", name=re.compile(r"enter demo", re.I)),
            lambda p: p.get_by_text(re.compile(r"^\s*enter demo\s*$", re.I)),
        ], heal_role="button")
        btn.click()
        try:
            self.page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=10_000)
        except Exception:
            pass
        self.s.settle()
        if "/dashboard" not in self.page.url:
            raise AgentError(f"Enter Demo did not reach the dashboard (url={self.page.url})")

    def logout(self):
        btn = self.s.find("Log out", [
            lambda p: p.get_by_role("button", name=re.compile(r"log ?out|sign ?out", re.I)),
            lambda p: p.get_by_role("link", name=re.compile(r"log ?out|sign ?out", re.I)),
        ], heal_role="button")
        btn.click()
        try:
            self.page.wait_for_url(lambda u: not any(r in u for r in PROTECTED_ROUTES), timeout=8000)
        except Exception:
            pass
        self.s.settle()

    def nav(self, label: str):
        link = self.s.find(f"{label} navigation link", [
            lambda p: p.get_by_role("navigation").get_by_role("link", name=label, exact=True),
            lambda p: p.get_by_role("link", name=label, exact=True),
            lambda p: p.get_by_role("link", name=re.compile(label, re.I)),
        ], heal_role="link")
        link.click()
        self.s.settle()

    def state(self) -> dict:
        return self.page.evaluate(_PAGE_STATE_JS)

    def is_protected_content_visible(self) -> bool:
        txt = self.s.body_text()
        return bool(re.search(r"Available balance|Transfer between your accounts|Your profile|Apply filters", txt))

    # ---------------------------------------------------------------- accounts
    def read_dashboard(self) -> list[dict]:
        if "/dashboard" not in self.page.url:
            self.nav("Dashboard")
        self.s.find("account cards", [
            lambda p: p.get_by_test_id("account-card"),
            lambda p: p.get_by_text(re.compile("Available balance", re.I)),
        ])
        self.s.settle()
        cards = self.page.evaluate(_READ_CARDS_JS)
        out = []
        for c in cards:
            lines = c["lines"]
            masked = next((l for l in lines if re.search(r"[•*xX\u2022]{2,}\s*\d{2,4}", l)), "")
            typ = next((l for l in lines if re.fullmatch(r"(checking|savings)", l, re.I)), "")
            out.append({"name": c["name"], "masked": masked, "type": typ, "balance_text": c["balance_text"],
                        "balance": parse_money(c["balance_text"]), "href": c["href"],
                        "id": c["href"].rstrip("/").split("/")[-1] if c["href"] else "", "lines": lines,
                        "html": c["html"]})
        return out

    def read_account_detail(self, account_id: str) -> dict:
        self.s.goto(f"/accounts/{account_id}")
        st = self.state()
        bal_loc = self.page.get_by_test_id("account-balance")
        bal_text = bal_loc.first.inner_text() if bal_loc.count() else ""
        if not bal_text:
            m = re.search(r"Available balance\s*\n?\s*([^\n]+)", self.s.body_text())
            bal_text = m.group(1) if m else ""
        hist = self.read_history_raw()
        return {"name": st["h1"].strip(), "balance_text": bal_text.strip(), "balance": parse_money(bal_text),
                "history": hist, "state": st}

    # ---------------------------------------------------------------- history
    def read_history_raw(self) -> dict:
        self.s.settle()
        table = self.page.evaluate(_READ_TABLE_JS)
        st = self.state()
        txt = self.s.body_text()
        count = re.search(r"Showing (\d+) transactions?", txt)
        if table:
            return {"kind": "table", "rows": table["rows"], "headers": table["headers"],
                    "count_text": count.group(0) if count else "", "state": st}
        if re.search(r"no transactions", txt, re.I):
            empty = next((s for s in st["statuses"] if re.search(r"no transactions", s, re.I)), "")
            return {"kind": "empty", "rows": [], "message": empty or "No transactions", "state": st}
        if st["alerts"]:
            return {"kind": "error", "rows": [], "message": " | ".join(st["alerts"]), "state": st}
        return {"kind": "unknown", "rows": [], "message": txt[:300], "state": st}

    def open_history(self):
        if not self.page.url.split("?")[0].endswith("/transactions"):
            self.nav("Transactions")
        return self.read_history_raw()

    def _select_by_text(self, label_re: str, contains: str):
        sel = self.s.find(f"{label_re} select", [
            lambda p: p.get_by_label(re.compile(label_re, re.I)),
            lambda p: p.get_by_role("combobox", name=re.compile(label_re, re.I)),
        ], heal_role="combobox")
        options = sel.evaluate("el => Array.from(el.options).map(o => ({v: o.value, t: o.text}))")
        if contains == "":
            target = next((o for o in options if o["v"] == ""), None)
        else:
            target = next((o for o in options if contains.lower() in o["t"].lower()), None)
        if target is None:
            raise AgentError(f"No option containing '{contains}' in '{label_re}' (have {[o['t'] for o in options]})")
        sel.select_option(value=target["v"])
        return target

    def apply_filters(self, account: str = "", type_label: str = "", status_label: str = "",
                      start: str = "", end: str = "") -> dict:
        self.open_history()
        self._select_by_text(r"^account$", account)
        self._select_by_text(r"^type$", type_label)
        self._select_by_text(r"^status$", status_label)
        for label, value in ((r"start date", start), (r"end date", end)):
            box = self.s.find(label, [lambda p, l=label: p.get_by_label(re.compile(l, re.I))])
            box.fill(value)
        self.s.find("Apply filters", [lambda p: p.get_by_role("button", name=re.compile("apply", re.I))],
                    heal_role="button").click()
        self.s.settle()
        return self.read_history_raw()

    def clear_filters(self) -> dict:
        self.s.find("Clear filters", [lambda p: p.get_by_role("button", name=re.compile("clear filters", re.I))],
                    heal_role="button").click()
        self.s.settle()
        return self.read_history_raw()

    # ---------------------------------------------------------------- transfers
    def open_transfer(self):
        if "/transfer" not in self.page.url:
            self.nav("Transfer")
        self.s.find("transfer form", [lambda p: p.get_by_role("button", name=re.compile("transfer funds", re.I)),
                                      lambda p: p.get_by_role("button", name=re.compile("transfer", re.I))])

    def fill_transfer(self, source: str, destination: str, amount: str, memo: str = ""):
        self.open_transfer()
        self._select_by_text(r"^from", source)
        self._select_by_text(r"^to", destination)
        amt = self.s.find("Amount input", [lambda p: p.get_by_label(re.compile(r"amount", re.I)),
                                           lambda p: p.get_by_role("textbox", name=re.compile("amount", re.I))],
                          heal_role="textbox")
        amt.fill(amount)
        memo_box = self.s.find("Memo input", [lambda p: p.get_by_label(re.compile(r"memo", re.I)),
                                              lambda p: p.get_by_role("textbox", name=re.compile("memo", re.I))],
                               heal_role="textbox")
        memo_box.fill(memo)

    def submit_button(self):
        return self.s.find("Transfer funds button", [
            lambda p: p.locator("form").get_by_role("button", name=re.compile(r"transfer", re.I)),
            lambda p: p.get_by_role("button", name=re.compile(r"transfer funds|submit", re.I)),
        ], heal_role="button")

    def transfer(self, source: str, destination: str, amount: str, memo: str = "", click: str = "click") -> dict:
        self.fill_transfer(source, destination, amount, memo)
        before_posts = len(self.s.api_calls("POST", "/api/transfers"))
        btn = self.submit_button()
        if click == "dblclick":
            btn.dblclick()
        else:
            btn.click()
        self.s.settle()
        posts = self.s.api_calls("POST", "/api/transfers")[before_posts:]
        st = self.state()
        text = self.s.body_text()
        success = bool(re.search(r"transfer (complete|successful|succeeded)", text, re.I))
        failure = any(re.search(r"not completed|failed|could not|invalid|insufficient|must", a, re.I)
                      for a in st["alerts"])
        field_errors = [m for f in st["invalid"] for m in f["messages"]]
        receipt = self.page.evaluate("""() => { const o = {}; document.querySelectorAll('dl.receipt div, dl div')
            .forEach(d => { const t = d.querySelector('dt'), v = d.querySelector('dd');
            if (t && v) o[t.innerText.trim()] = v.innerText.trim(); }); return o; }""") if success else {}
        ui_state = "success" if success else "error" if (failure or field_errors) else "none"
        return {"ui_state": ui_state, "alerts": st["alerts"], "statuses": st["statuses"],
                "field_errors": field_errors, "receipt": receipt,
                "posts": [{"status": e.status, "request": e.request_body, "response": e.response} for e in posts]}

    # ---------------------------------------------------------------- API ground truth (same session cookie)
    def api_get(self, path: str):
        if not self.s.api_origin:
            raise Unverifiable("API origin not yet observed in network traffic")
        r = self.s.context.request.get(self.s.api_origin + path)
        try:
            body = r.json()
        except Exception:
            body = None
        return r.status, body

    def api_balances(self) -> dict[str, Decimal]:
        status, body = self.api_get("/api/accounts")
        if status != 200:
            raise Unverifiable(f"GET /api/accounts returned {status}")
        return {a["id"]: Decimal(a["balance"]) for a in body["accounts"]}

    def api_transactions(self, query: str = "") -> list[dict]:
        status, body = self.api_get("/api/transactions" + query)
        if status != 200:
            raise Unverifiable(f"GET /api/transactions returned {status}")
        return body["transactions"]

    def accounts_by_type(self) -> dict[str, dict]:
        status, body = self.api_get("/api/accounts")
        if status != 200:
            raise Unverifiable(f"GET /api/accounts returned {status}")
        return {a["type"]: a for a in body["accounts"]}


def wait(seconds: float):
    time.sleep(seconds)
