"""Offline unit tests for the deterministic oracles (no browser, no network). Run: python -m unittest -v"""
import unittest
from datetime import date
from decimal import Decimal

from qa_agent.oracle import (balance_attribution, decimal_places, money_sign, parse_money, row_matches,
                             to_pacific_date)


class MoneyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_money("$4,369.75USD"), Decimal("4369.75"))
        self.assertEqual(parse_money("\u2212$120.00"), Decimal("-120.00"))
        self.assertEqual(parse_money("+$1,200.00"), Decimal("1200.00"))
        self.assertIsNone(parse_money("no amount"))

    def test_decimal_places(self):
        self.assertEqual(decimal_places("$4,369.75USD"), 2)   # regression: \b failed before 'USD'
        self.assertEqual(decimal_places("10.005"), 3)
        self.assertEqual(decimal_places("$12"), 0)

    def test_sign(self):
        self.assertEqual(money_sign("\u2212$5.00"), -1)
        self.assertEqual(money_sign("+$5.00"), 1)
        self.assertEqual(money_sign("$5.00"), 0)


class DateTests(unittest.TestCase):
    def test_pacific_conversion(self):
        # 02:45 UTC on Mar 5 is the evening of Mar 4 in Los Angeles
        self.assertEqual(to_pacific_date("2025-03-05T02:45:00Z"), date(2025, 3, 4))
        self.assertEqual(to_pacific_date("2025-04-10T15:20:00Z"), date(2025, 4, 10))

    def test_inclusive_boundaries(self):
        row = {"iso": "2025-03-05T02:45:00Z", "type": "Withdrawal", "status": "Completed", "account": "Everyday Checking"}
        d = date(2025, 3, 4)
        self.assertTrue(row_matches(row, start=d, end=d)[0])
        self.assertFalse(row_matches(row, end=date(2025, 3, 3))[0])
        self.assertFalse(row_matches(row, type_label="Deposit")[0])


class AttributionTests(unittest.TestCase):
    def test_foreign_activity_is_explained(self):
        rep = balance_attribution({"a": Decimal("10")}, {"a": Decimal("7")}, {"a": Decimal("-1")}, {"a": Decimal("-2")})
        self.assertTrue(rep["a"]["explained"])


if __name__ == "__main__":
    unittest.main()
