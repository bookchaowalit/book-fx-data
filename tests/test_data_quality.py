"""Data-quality guards, cross-rate sanity and CLI validation for book-fx-data."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_fx import config, ingest, lake, quality
from book_fx.fsutil import atomic_append_csv, atomic_write_csv

EVENT_TIME = "2026-08-01T12:00:00Z"

# json.loads accepts NaN/Infinity, so a misbehaving upstream can send them.
DIRTY_LATEST = json.loads(
    '{"base": "THB", "date": "2026-08-01", "rates": '
    '{"USD": 0.028, "usd": 0.03, "EUR": NaN, "JPY": Infinity, "GBP": 0, '
    '"CNY": -0.2, "SGD": "0.037", "HKD": "n/a", " ": 1.0}}'
)


class CleanRatesTests(unittest.TestCase):
    def test_only_finite_positive_unique_rates_survive(self):
        clean, rejected = quality.clean_rates(DIRTY_LATEST["rates"])
        self.assertEqual(clean, {"USD": 0.028, "SGD": 0.037})
        self.assertEqual(
            quality.summarize_rejections(rejected),
            {"duplicate_id": 1, "invalid_rate": 5, "missing_currency": 1},
        )

    def test_non_object_rates(self):
        self.assertEqual(quality.clean_rates(None)[0], {})
        self.assertEqual(quality.clean_rates([1, 2])[0], {})


class RecordQualityTests(unittest.TestCase):
    def test_rate_records_drop_invalid_rates(self):
        records = lake.rate_records_from_api(DIRTY_LATEST, event_time=EVENT_TIME)
        self.assertEqual(sorted(r["id"] for r in records), ["THB:SGD", "THB:USD"])
        for record in records:
            self.assertIsInstance(record["rate"], float)

    def test_inverse_is_reciprocal_cross_rate(self):
        records = lake.rate_records_from_api(
            {"base": "thb", "date": "2026-08-01", "rates": {"USD": 0.028, "EUR": 0.026, "JPY": 4.1}},
            event_time=EVENT_TIME,
        )
        for record in records:
            with self.subTest(currency=record["currency"]):
                self.assertEqual(record["base"], "THB")
                self.assertAlmostEqual(record["rate"] * record["inverse"], 1.0, places=4)

    def test_history_records_drop_invalid_and_duplicate_days(self):
        history = [
            {"date": "2026-07-31", "rates": {"USD": 0.0279, "EUR": float("nan")}},
            {"date": "2026-07-31", "rates": {"USD": 0.0300}},  # repeated date
            {"date": "2026-08-01", "rates": {"USD": -1, "EUR": 0.026}},
        ]
        with mock.patch.object(lake, "utc_now_iso", return_value=EVENT_TIME):
            records = lake.history_records_from_api(history, base="THB")
        self.assertEqual(
            [(r["id"], r["rate"]) for r in records],
            [("THB:USD:2026-07-31", 0.0279), ("THB:EUR:2026-08-01", 0.026)],
        )


class FixtureSanityTests(unittest.TestCase):
    def test_fixture_inverse_matches_rate(self):
        with (config.FIXTURES_DIR / "exchange_rates.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(currency=row["currency"]):
                rate, inverse = float(row["rate"]), float(row["inverse"])
                self.assertGreater(rate, 0)
                # Fixture inverses are rounded to 2 decimals.
                self.assertAlmostEqual(inverse, 1 / rate, delta=0.01)

    def test_fixture_history_has_unique_keys_and_positive_rates(self):
        with (config.FIXTURES_DIR / "exchange_history.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        keys = [(r["date"], r["base"], r["currency"]) for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(float(r["rate"]) > 0 for r in rows))


class TrendTests(unittest.TestCase):
    def test_non_finite_rates_give_unknown_trend(self):
        history = [{"date": str(i), "rates": {"USD": 0.028}} for i in range(7)]
        history[0]["rates"]["USD"] = float("nan")
        self.assertEqual(ingest.detect_trend(history, "USD")["direction"], "unknown")
        history[0]["rates"]["USD"] = 0.027
        self.assertEqual(ingest.detect_trend(history, "USD")["direction"], "strengthening")


class LiveIngestQualityTests(unittest.TestCase):
    def test_zero_valid_rates_fail_before_lake_or_csv(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(
                ingest, "fetch_latest_raw",
                return_value=(b"{}", {"base": "THB", "date": "2026-08-01", "rates": {"USD": float("nan")}}),
            ), mock.patch.object(lake, "ingest_to_lake") as write:
                with self.assertRaises(RuntimeError):
                    ingest.run_live_ingest(
                        base="THB", symbols=["USD"], output_dir=Path(tmp),
                        fetch_history_flag=False, data_lake_uri=tmp,
                    )
            write.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])


class CliValidationTests(unittest.TestCase):
    def _exit_code(self, argv: list[str]) -> int:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            ingest.main(argv)
        return ctx.exception.code

    def test_rejects_bad_arguments(self):
        self.assertEqual(self._exit_code(["--base", "BAHT"]), 2)
        self.assertEqual(self._exit_code(["--symbols", "THB", "--base", "thb"]), 2)
        self.assertEqual(self._exit_code(["--symbols", "USD,E1R"]), 2)
        self.assertEqual(self._exit_code(["--alert-threshold", "nan"]), 2)

    def test_codes_are_normalized(self):
        with mock.patch.object(ingest, "run_live_ingest") as run:
            self.assertEqual(ingest.main(["--base", "thb", "--symbols", "usd,USD,thb,eur"]), 0)
        self.assertEqual(run.call_args.kwargs["base"], "THB")
        self.assertEqual(run.call_args.kwargs["symbols"], ["USD", "EUR"])


class AtomicWriteTests(unittest.TestCase):
    def test_projections_leave_no_temp_files(self):
        data = {"base": "THB", "date": "2026-08-01", "rates": {"USD": 0.028}}
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            out = Path(tmp)
            ingest.project_rates_csv(data, out, {})
            ingest.project_history_csv(data, out)
            ingest.project_history_csv(data, out)
            self.assertEqual(
                sorted(p.name for p in out.iterdir()), ["exchange_history.csv", "exchange_rates.csv"]
            )
            lines = (out / "exchange_history.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "date,base,currency,rate")
            self.assertEqual(len(lines), 3)

    def test_failed_write_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            atomic_write_csv(path, ["a"], [{"a": 1}])
            with self.assertRaises(ValueError):
                atomic_append_csv(path, ["a"], [{"b": 2}])
            self.assertEqual(path.read_bytes(), b"a\r\n1\r\n")


if __name__ == "__main__":
    unittest.main()
