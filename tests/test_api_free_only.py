"""Offline tests for book-fx-data free-only API + policy."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from book_fx import config, lake, policy
from book_fx.api import DataProductHandler
from book_fx.http_client import UpstreamError, request_json
from book_fx.store import (
    get_record,
    load_csv_projection,
    load_history,
    load_records,
    seed_fixtures,
    seed_lake_from_rates,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"

SAMPLE_RATES = {
    "base": "THB",
    "date": "2026-08-01",
    "rates": {"USD": 0.028, "EUR": 0.026},
}
SAMPLE_TRENDS = {
    "USD": {"direction": "stable", "change_pct": 0.1},
    "EUR": {"direction": "stable", "change_pct": -0.2},
}


def _set_data_dir(path: Path):
    return mock.patch.object(config, "DATA_DIR", path)


def _set_lake_uri(uri: str):
    return mock.patch.object(config, "DATA_LAKE_URI", uri)


def _set_read_mode(mode: str, fallback: str = "error"):
    return mock.patch.multiple(
        config,
        LAKE_READ_MODE=mode,
        LAKE_READ_FALLBACK=fallback,
    )


def _set_silver_mode(mode: str):
    return mock.patch.object(config, "SILVER_READ_MODE", mode)


def _write_fx_silver(lake_uri: str):
    root = lake.find_solo_empire_root()
    if root is None:
        raise RuntimeError("Solo Empire root is required for the Silver pilot")
    scripts = root / "infra" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from data_lake.silver import SilverProductContract, transform_bronze_to_silver

    contract = SilverProductContract(
        source=config.LAKE_SOURCE,
        domain=config.LAKE_DOMAIN,
        bronze_dataset=config.LAKE_DATASET_RATES,
        silver_dataset=config.LAKE_DATASET_RATES,
        product_schema_version="fx.silver.v1",
        bronze_product_schema_version=config.SCHEMA_VERSION,
        bronze_schema_version=config.LAKE_BRONZE_SCHEMA_VERSION,
        normalizer="fx_rates",
        required_fields=("source_record_id", "base_currency", "quote_currency", "rate"),
        privacy_class=config.LAKE_PRIVACY_CLASS,
        retention_class=config.LAKE_RETENTION_CLASS,
    )
    return transform_bronze_to_silver(contract, data_lake_uri=lake_uri)


def _lake_deps_available() -> bool:
    try:
        import duckdb  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    try:
        return lake.find_solo_empire_root() is not None
    except (ImportError, ModuleNotFoundError):
        return False


@unittest.skipUnless(_lake_deps_available(), "pyarrow/duckdb + monorepo data_lake required")
class StoreLakeTests(unittest.TestCase):
    def test_bronze_records_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(
                SAMPLE_RATES, data_lake_uri=lake_uri, trends=SAMPLE_TRENDS
            )
            with _set_lake_uri(lake_uri):
                payload = load_records()
            self.assertEqual(payload["source_kind"], "bronze_parquet")
            self.assertIn(payload["data_status"], {"ok", "stale"})
            self.assertGreaterEqual(len(payload["items"]), 1)
            self.assertNotIn("exchange_rates.csv", payload["path"])

    def test_empty_bronze(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "empty-lake")
            Path(lake_uri).mkdir()
            with _set_lake_uri(lake_uri):
                payload = load_records()
            self.assertEqual(payload["data_status"], "empty")

    def test_csv_does_not_feed_load_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            lake_uri = str(root / "empty-lake")
            Path(lake_uri).mkdir()
            with mock.patch.object(config, "FIXTURES_DIR", FIXTURE_ROOT):
                seed_fixtures(data_dir)
            self.assertGreaterEqual(
                len(load_csv_projection(kind="records", data_dir=data_dir)["items"]), 1
            )
            with _set_lake_uri(lake_uri), _set_data_dir(data_dir):
                payload = load_records(data_dir)
            self.assertEqual(payload["items"], [])

    def test_history_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(SAMPLE_RATES, data_lake_uri=lake_uri)
            with _set_lake_uri(lake_uri):
                hist = load_history()
            self.assertEqual(hist["source_kind"], "bronze_parquet")
            self.assertIn("exchange_history", hist["path"])
            self.assertGreaterEqual(len(hist["items"]), 1)

    def test_invalid_record_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(SAMPLE_RATES, data_lake_uri=lake_uri)
            with _set_lake_uri(lake_uri):
                self.assertIsNone(get_record("NOPE:ZZZ"))

    def test_compare_mode_reports_silver_parity_but_serves_bronze(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(
                SAMPLE_RATES, data_lake_uri=lake_uri, trends=SAMPLE_TRENDS
            )
            _write_fx_silver(lake_uri)
            with _set_lake_uri(lake_uri), _set_silver_mode("compare"):
                payload = load_records()
            self.assertEqual(payload["source_kind"], "bronze_parquet")
            self.assertEqual(payload["silver_read_mode"], "compare")
            self.assertEqual(payload["silver_parity"]["status"], "passed")

    def test_silver_mode_serves_silver_without_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lake_uri = str(root / "lake")
            seed_lake_from_rates(
                SAMPLE_RATES, data_lake_uri=lake_uri, trends=SAMPLE_TRENDS
            )
            _write_fx_silver(lake_uri)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / config.RECORDS_FILE).write_text(
                "base,currency,rate\nPOISON,ZZZ,0\n", encoding="utf-8"
            )
            with _set_lake_uri(lake_uri), _set_data_dir(data_dir), _set_silver_mode("silver"):
                payload = load_records(data_dir)
            self.assertEqual(payload["source_kind"], "silver_parquet")
            self.assertEqual(payload["silver_parity"]["status"], "passed")
            self.assertNotIn("POISON", {item["base"] for item in payload["items"]})

    def test_silver_mode_fails_closed_when_silver_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(SAMPLE_RATES, data_lake_uri=lake_uri)
            with _set_lake_uri(lake_uri), _set_silver_mode("silver"):
                payload = load_records()
            self.assertEqual(payload["data_status"], "malformed")
            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["source_kind"], "silver_parquet")
            self.assertIn("Silver serving is fail-closed", payload["error"])

    def test_iceberg_mode_keeps_rates_and_history_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(
                SAMPLE_RATES, data_lake_uri=lake_uri, trends=SAMPLE_TRENDS
            )

            def read_catalog_rows(contract, dataset, *, data_lake_uri=None, sql=None):
                return lake.read_bronze_rows(
                    dataset, data_lake_uri=data_lake_uri, sql=sql
                )

            with _set_lake_uri(lake_uri), _set_read_mode("parquet"):
                expected_rates = load_records()
                expected_history = load_history()
            with _set_lake_uri(lake_uri), _set_read_mode("iceberg"), mock.patch(
                "data_lake.product_store.read_iceberg_rows",
                side_effect=read_catalog_rows,
            ):
                actual_rates = load_records()
                actual_history = load_history()

            self.assertEqual(actual_rates["items"], expected_rates["items"])
            self.assertEqual(actual_history["items"], expected_history["items"])
            self.assertEqual(actual_rates["source_kind"], "iceberg_rest_duckdb")
            self.assertEqual(actual_history["source_kind"], "iceberg_rest_duckdb")
            self.assertEqual(actual_rates["read_mode"], "iceberg")

    def test_iceberg_mode_fallback_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(SAMPLE_RATES, data_lake_uri=lake_uri)
            with _set_lake_uri(lake_uri), _set_read_mode("iceberg", "parquet"), mock.patch(
                "data_lake.product_store.read_iceberg_rows",
                side_effect=lake.LakeUnavailable("catalog unavailable"),
            ):
                payload = load_records()

            self.assertGreaterEqual(len(payload["items"]), 1)
            self.assertEqual(payload["source_kind"], "bronze_parquet_fallback")
            self.assertIn("catalog unavailable", payload["fallback_reason"])

    def test_iceberg_mode_fails_closed_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_rates(SAMPLE_RATES, data_lake_uri=lake_uri)
            with _set_lake_uri(lake_uri), _set_read_mode("iceberg", "error"), mock.patch(
                "data_lake.product_store.read_iceberg_rows",
                side_effect=lake.LakeUnavailable("catalog credentials unavailable"),
            ):
                payload = load_records()

            self.assertEqual(payload["data_status"], "error")
            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["source_kind"], "iceberg_rest_duckdb")
            self.assertIn("catalog credentials unavailable", payload["error"])


@unittest.skipUnless(_lake_deps_available(), "pyarrow/duckdb + monorepo data_lake required")
class CsvProjectionOnlyTests(unittest.TestCase):
    def test_csv_projection_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with mock.patch.object(config, "FIXTURES_DIR", FIXTURE_ROOT):
                seed_fixtures(data_dir)
            payload = load_csv_projection(kind="records", data_dir=data_dir)
            self.assertEqual(payload["source_kind"], "csv_projection")


class PolicyTests(unittest.TestCase):
    def test_frankfurter_allowed(self):
        decision = policy.evaluate_provider("frankfurter_public")
        self.assertTrue(decision.allowed)

    def test_paid_blocked(self):
        decision = policy.evaluate_provider("paid_fx_feed")
        self.assertFalse(decision.allowed)

    def test_external_writes_blocked(self):
        with mock.patch.object(config, "FREE_ONLY", True), mock.patch.object(
            config, "ALLOW_EXTERNAL_WRITES", False
        ):
            self.assertFalse(policy.external_writes_allowed())
            with self.assertRaises(PermissionError):
                policy.require_external_writes("telegram")


class HttpClientTests(unittest.TestCase):
    def test_blocks_paid_provider(self):
        with self.assertRaises(PermissionError):
            request_json("GET", "https://example.invalid/paid", provider="paid_fx_feed")

    def test_timeout(self):
        class Boom:
            def request(self, *args, **kwargs):
                raise TimeoutError("timed out")

        fake_httpx = mock.Mock()
        fake_httpx.request = Boom().request
        with mock.patch("book_fx.http_client.httpx", fake_httpx), mock.patch.object(
            config, "MAX_RETRIES", 0
        ):
            with self.assertRaises(UpstreamError) as ctx:
                request_json(
                    "GET",
                    "https://example.invalid/timeout",
                    provider="frankfurter_public",
                    sleep=lambda _: None,
                )
            self.assertEqual(ctx.exception.kind, "timeout")


@unittest.skipUnless(_lake_deps_available(), "pyarrow/duckdb + monorepo data_lake required")
class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data_dir = root / "data"
        self.data_dir.mkdir()
        self.lake_uri = str(root / "lake")
        seed_lake_from_rates(
            SAMPLE_RATES,
            data_lake_uri=self.lake_uri,
            data_dir=self.data_dir,
            trends=SAMPLE_TRENDS,
        )
        self.assertFalse((self.data_dir / config.RECORDS_FILE).exists())
        self.data_patch = _set_data_dir(self.data_dir)
        self.lake_patch = _set_lake_uri(self.lake_uri)
        self.read_patch = _set_read_mode("parquet", "error")
        self.data_patch.start()
        self.lake_patch.start()
        self.read_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DataProductHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.lake_patch.stop()
        self.data_patch.stop()
        self.read_patch.stop()
        self.tmp.cleanup()

    def _get(self, path: str):
        try:
            with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _post(self, path: str):
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_healthz(self):
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["repository"], config.REPO_NAME)

    def test_metadata_lake_first(self):
        status, body = self._get("/v1/metadata")
        self.assertEqual(status, 200)
        meta = body["items"][0]
        self.assertEqual(meta["storage_model"], "lake_first_bronze_duckdb")
        self.assertEqual(meta["records_source_kind"], "bronze_parquet")
        self.assertEqual(meta["shared_adapter"], "data_lake.product_adapter")
        self.assertEqual(meta["lake_dataset_rates"], "exchange_rates")
        self.assertEqual(meta["read_mode"], "parquet")
        self.assertEqual(meta["read_fallback"], "error")

    def test_metadata_reports_silver_serving_mode(self):
        _write_fx_silver(self.lake_uri)
        with _set_silver_mode("silver"):
            status, body = self._get("/v1/metadata")
        self.assertEqual(status, 200)
        meta = body["items"][0]
        self.assertEqual(meta["storage_model"], "lake_first_silver_duckdb")
        self.assertEqual(meta["records_source_kind"], "silver_parquet")
        self.assertEqual(meta["history_source_kind"], "bronze_parquet")
        self.assertEqual(meta["silver_read_mode"], "silver")
        self.assertEqual(meta["silver_parity"]["status"], "passed")

    def test_metadata_reports_iceberg_mode(self):
        def read_catalog_rows(contract, dataset, *, data_lake_uri=None, sql=None):
            return lake.read_bronze_rows(
                dataset, data_lake_uri=data_lake_uri, sql=sql
            )

        with _set_read_mode("iceberg"), mock.patch(
            "data_lake.product_store.read_iceberg_rows",
            side_effect=read_catalog_rows,
        ):
            status, body = self._get("/v1/metadata")

        self.assertEqual(status, 200)
        meta = body["items"][0]
        self.assertEqual(meta["storage_model"], "lake_first_iceberg_rest_duckdb")
        self.assertEqual(meta["records_source_kind"], "iceberg_rest_duckdb")
        self.assertEqual(meta["history_source_kind"], "iceberg_rest_duckdb")
        self.assertEqual(meta["read_mode"], "iceberg")

    def test_records_and_history(self):
        status, body = self._get("/v1/records?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["items"]), 1)
        status, hist = self._get("/v1/history?limit=1")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(hist["items"]), 1)

    def test_api_serves_without_csv_files(self):
        self.assertFalse(any(self.data_dir.glob("*.csv")))
        status, body = self._get("/v1/records")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(body["items"]), 1)
        (self.data_dir / config.RECORDS_FILE).write_text(
            "date,base,currency,rate,inverse,trend_7d,trend_change_pct,updated_at\n"
            "2099-01-01,THB,FAKE,1,1,stable,0,2099-01-01 00:00:00\n",
            encoding="utf-8",
        )
        status, body = self._get("/v1/records")
        currencies = {item["currency"] for item in body["items"]}
        self.assertIn("USD", currencies)
        self.assertNotIn("FAKE", currencies)

    def test_refresh_forbidden(self):
        status, body = self._post("/v1/refresh")
        self.assertEqual(status, 403)


class SmokeCompileTests(unittest.TestCase):
    def test_modules_import(self):
        from book_fx import api, config as cfg, store

        self.assertTrue(cfg.FREE_ONLY)
        self.assertEqual(cfg.LAKE_DOMAIN, "market")
        self.assertEqual(cfg.LAKE_DATASET_RATES, "exchange_rates")
        self.assertEqual(cfg.SCHEMA_VERSION, "fx.v1")
        self.assertEqual(cfg.LAKE_READ_MODE, "parquet")
        self.assertEqual(cfg.LAKE_READ_FALLBACK, "error")
        self.assertTrue(hasattr(store, "load_csv_projection"))
        self.assertTrue(hasattr(api, "main"))


if __name__ == "__main__":
    unittest.main()
