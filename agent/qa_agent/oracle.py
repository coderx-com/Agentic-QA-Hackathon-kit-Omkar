"""Deterministic oracles: exact money parsing, Pacific-date math, filter expectations, ledger attribution.

Verdicts are decided here, never by an LLM, so that every pass/fail is reproducible and explainable.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
_MONEY_RE = re.compile(r"([+\-\u2212])?\s*(?:US\$|\$|USD\s*)?\s*([\d,]+(?:\.\d+)?)")
MASKED_RE = re.compile(r"^[•*xX\u2022.\s]{2,}\s*\d{2,4}$")
FULL_NUMBER_RE = re.compile(r"(?<![\d.,])\d{8,}(?![\d])")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def parse_money(text: str) -> Decimal | None:
    """'−$1,234.50' -> Decimal('-1234.50'); '+$2.00' -> 2.00. Returns None when no amount is present."""
    if text is None:
        return None
    m = _MONEY_RE.search(text.replace("\xa0", " "))
    if not m:
        return None
    sign, digits = m.group(1), m.group(2).replace(",", "")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if sign in ("-", "\u2212") else value


def decimal_places(text: str) -> int | None:
    m = re.search(r"\d\.(\d+)(?!\d)", text or "")
    return len(m.group(1)) if m else 0 if re.search(r"\d", text or "") else None


def money_sign(text: str) -> int:
    t = (text or "").strip()
    if t.startswith(("-", "\u2212")):
        return -1
    if t.startswith("+"):
        return 1
    return 0


def to_pacific_date(iso_utc: str) -> date:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PACIFIC).date()


def parse_iso(iso_utc: str) -> datetime:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def row_matches(row: dict, *, account_name: str | None = None, type_label: str | None = None,
                status_label: str | None = None, start: date | None = None, end: date | None = None) -> tuple[bool, list[str]]:
    """Independent oracle for the PRD filter rule: a row must satisfy ALL selected filters (dates inclusive)."""
    why: list[str] = []
    if account_name and account_name.lower() not in row.get("account", "").lower():
        why.append(f"account column '{row.get('account')}' does not involve '{account_name}'")
    if type_label and row.get("type", "").strip().lower() != type_label.lower():
        why.append(f"type '{row.get('type')}' != '{type_label}'")
    if status_label and row.get("status", "").strip().lower() != status_label.lower():
        why.append(f"status '{row.get('status')}' != '{status_label}'")
    if (start or end) and row.get("iso"):
        d = to_pacific_date(row["iso"])
        if start and d < start:
            why.append(f"date {d} is before start {start}")
        if end and d > end:
            why.append(f"date {d} is after end {end}")
    return (not why), why


def expected_ids(rows: list[dict], **filters) -> set[str]:
    return {r["id"] for r in rows if row_matches(r, **filters)[0]}


def diff_sets(expected: set[str], observed: set[str], rows_by_id: dict[str, dict]) -> dict:
    def describe(ids):
        out = []
        for i in sorted(ids):
            r = rows_by_id.get(i, {})
            out.append(f"{r.get('date_text', '?')} | {r.get('description', '?')} | {r.get('type', '?')} | "
                       f"{r.get('amount_text', '?')} | {r.get('status', '?')}")
        return out
    return {"missing": describe(expected - observed), "unexpected": describe(observed - expected)}


def balance_attribution(before: dict[str, Decimal], after: dict[str, Decimal], own_deltas: dict[str, Decimal],
                        foreign_deltas: dict[str, Decimal]) -> dict:
    """Explain an observed balance change as (our action) + (other users' concurrent completed transactions).

    Returns per-account expected/observed so that drift caused by someone else is never reported as our defect.
    """
    report = {}
    for acc in set(before) | set(after):
        b, a = before.get(acc, Decimal("0")), after.get(acc, Decimal("0"))
        own, foreign = own_deltas.get(acc, Decimal("0")), foreign_deltas.get(acc, Decimal("0"))
        report[acc] = {"before": str(b), "after": str(a), "observed_delta": str(a - b),
                       "own_delta": str(own), "foreign_delta": str(foreign),
                       "explained": (a - b) == own + foreign}
    return report
