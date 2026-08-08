"""Lake-first tests for book-fx-data (shared product_adapter)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_fx import config, lake
from book_fx.ingest import run_live_ingest
from book_fx.store import load_records, seed_lake_from_rates


SAMPLE_RATES = {
    "base": "THB",
    "date": "2026-08-01",
    "rates": {"USD": 0.028, "EUR": 0.026},
}


class NormalizeTests(unittest.TestCase):
    def test_rate_records_include_id(self):
        records = lake.rate_records_from_api(SAMPLE_RATES)
        self.assertEqual(len(records), 2)
        ids = {r["id"] for r in records}
        self.assertEqual(ids, {"THB:USD", "THB:EUR"})
        for row in records:
            self.assertIn("event_time", row)
            self.assertEqual(row["base"], "THB")


class LakeFirstOrderingTests(unittest.TestCase):
    def test_csv_not_written_when_lake_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            raw = json.dumps(SAMPLE_RATES).encode("utf-8")

            def boom(**_k):
                raise lake.LakeIngestError("simulated lake failure")

            with mock.patch.object(lake, "ingest_to_lake", side_effect=boom):
                with mock.patch(
                    "book_fx.ingest.fetch_latest_raw",
                    return_value=(raw, SAMPLE_RATES),
                ):
                    with self.assertRaises(lake.LakeIngestError):
                        run_live_ingest(
                            base="THB",
                            symbols=["USD", "EUR"],
                            output_dir=out,
                            fetch_history_flag=False,
                            project_csv=True,
                        )
            self.assertFalse((out / "exchange_rates.csv").exists())

    def test_csv_after_successful_lake(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            raw = json.dumps(SAMPLE_RATES).encode("utf-8")
            order: list[str] = []

            def fake_ingest(**kwargs):
                order.append(f"lake:{kwargs['dataset']}")
                return {
                    "status": "success",
                    "run_id": "test-run",
                    "record_count": len(kwargs["records"]),
                    "raw_key": "landing/test",
                    "bronze_key": "bronze/test",
                    "manifest_key": "control/test",
                    "data_lake": {"uri": "file:///tmp/lake"},
                }

            def fake_rates(*_a, **_k):
                order.append("csv_rates")
                return out / "exchange_rates.csv"

            def fake_hist(*_a, **_k):
                order.append("csv_history")
                return out / "exchange_history.csv"

            with mock.patch.object(lake, "ingest_to_lake", side_effect=fake_ingest):
                with mock.patch.object(
                    lake, "write_lineage", return_value=out / "lake_lineage.json"
                ):
                    with mock.patch(
                        "book_fx.ingest.fetch_latest_raw",
                        return_value=(raw, SAMPLE_RATES),
                    ):
                        with mock.patch(
                            "book_fx.ingest.project_rates_csv",
                            side_effect=fake_rates,
                        ):
                            with mock.patch(
                                "book_fx.ingest.project_history_csv",
                                side_effect=fake_hist,
                            ):
                                run_live_ingest(
                                    base="THB",
                                    symbols=["USD", "EUR"],
                                    output_dir=out,
                                    fetch_history_flag=False,
                                    project_csv=True,
                                )
            # With no-history flag, rates lake then CSV only (+ snapshot history fallback)
            self.assertIn(f"lake:{config.LAKE_DATASET_RATES}", order)
            self.assertLess(
                order.index(f"lake:{config.LAKE_DATASET_RATES}"),
                order.index("csv_rates"),
            )


@unittest.skipUnless(
    lake.find_solo_empire_root() is not None,
    "Solo Empire monorepo with data_lake not found",
)
class RealLakeIngestTests(unittest.TestCase):
    def test_ingest_landing_bronze_manifest_and_lineage(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            out = Path(tmp) / "projection"
            out.mkdir()
            raw = json.dumps(SAMPLE_RATES, separators=(",", ":")).encode("utf-8")
            records = lake.rate_records_from_api(SAMPLE_RATES)
            result = lake.ingest_to_lake(
                raw=raw,
                records=records,
                dataset=config.LAKE_DATASET_RATES,
                data_lake_uri=lake_uri,
                metadata={"test": True},
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["record_count"], 2)
            self.assertTrue(result["raw_key"].startswith("landing/"))
            self.assertIn("exchange_rates", result["bronze_key"])
            self.assertTrue(result["manifest_key"].startswith("control/"))
            self.assertEqual((Path(lake_uri) / result["raw_key"]).read_bytes(), raw)
            lineage_path = lake.write_lineage(
                result, dataset=config.LAKE_DATASET_RATES, data_dir=out
            )
            lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
            self.assertEqual(lineage["source"], config.LAKE_SOURCE)
            self.assertEqual(lineage["domain"], "market")

    def test_replay_from_landing(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            raw = b'{"base":"THB","date":"2026-08-01","rates":{"USD":0.028}}'
            records = lake.rate_records_from_api(json.loads(raw.decode("utf-8")))
            result = lake.ingest_to_lake(
                raw=raw,
                records=records,
                dataset=config.LAKE_DATASET_RATES,
                data_lake_uri=lake_uri,
            )
            self.assertEqual(
                lake.landing_object_bytes(result["raw_key"], data_lake_uri=lake_uri),
                raw,
            )

    def test_api_load_without_csv(self):
        try:
            import duckdb  # noqa: F401
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("duckdb/pyarrow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(SAMPLE_RATES, data_lake_uri=lake_uri)
            with mock.patch.object(config, "DATA_LAKE_URI", lake_uri):
                payload = load_records()
            self.assertEqual(payload["source_kind"], "bronze_parquet")
            self.assertGreaterEqual(len(payload["items"]), 2)


class SharedAdapterTests(unittest.TestCase):
    def test_uses_shared_product_adapter(self):
        root = lake.find_solo_empire_root()
        if root is None:
            self.skipTest("not under monorepo")
        self.assertTrue(
            (root / "infra" / "scripts" / "data_lake" / "product_adapter.py").is_file()
        )
        self.assertEqual(config.LAKE_DOMAIN, "market")
        self.assertEqual(config.LAKE_SOURCE, "book-fx-data")
        self.assertEqual(config.SCHEMA_VERSION, "fx.v1")


if __name__ == "__main__":
    src = Path(__file__).resolve().parents[1] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    unittest.main()
