"""Edge-case regressions: overflowing numbers, subnormal rates, null rates, env flags."""
from __future__ import annotations

import json
import math
import os
import unittest
from unittest import mock

from book_fx import config, ingest, lake, quality


class RateEdgeCases(unittest.TestCase):
    def test_huge_json_int_is_rejected_not_raised(self) -> None:
        huge = json.loads("1" + "0" * 400)
        self.assertIsNone(quality.finite_number(huge))
        clean, rejected = quality.clean_rates({"USD": huge, "THB": 36.5})
        self.assertEqual(clean, {"THB": 36.5})
        self.assertEqual(rejected, [{"id": "USD", "reason": "invalid_rate"}])

    def test_subnormal_rate_never_yields_infinite_inverse(self) -> None:
        records = lake.rate_records_from_api(
            {"base": "EUR", "date": "2026-08-01", "rates": {"XXX": 1e-320, "THB": 38.0}}
        )
        self.assertEqual([r["currency"] for r in records], ["THB"])
        self.assertTrue(all(math.isfinite(r["inverse"]) for r in records))

    def test_trend_with_null_rates_day_is_unknown(self) -> None:
        history = [{"date": f"2026-08-0{i}", "rates": {"THB": 38.0}} for i in range(1, 7)]
        history.append({"date": "2026-08-07", "rates": None})
        self.assertEqual(ingest.detect_trend(history, "THB")["direction"], "unknown")

    def test_trend_overflow_is_unknown(self) -> None:
        history = [{"rates": {"THB": 1e-300}}] + [{"rates": {"THB": 1e300}}] * 6
        trend = ingest.detect_trend(history, "THB")
        self.assertEqual(trend["direction"], "unknown")


class EnvBoolTests(unittest.TestCase):
    def _parse(self, raw, default):
        with mock.patch.dict(os.environ, {"EDGE_CASE_FLAG": raw}):
            return config.env_bool("EDGE_CASE_FLAG", default)

    def test_blank_or_unknown_keeps_the_safe_default(self) -> None:
        for raw in ("", "  ", "ture", "enabled?"):
            with self.subTest(raw=raw):
                self.assertIs(self._parse(raw, True), True)
                self.assertIs(self._parse(raw, False), False)

    def test_explicit_values(self) -> None:
        for raw in ("1", "TRUE", " yes ", "on"):
            self.assertIs(self._parse(raw, False), True)
        for raw in ("0", "False", " no", "OFF"):
            self.assertIs(self._parse(raw, True), False)


if __name__ == "__main__":
    unittest.main()
