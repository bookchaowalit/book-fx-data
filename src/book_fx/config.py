"""Runtime configuration for book-fx-data."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT / "data")))
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", str(PROJECT_ROOT / "fixtures")))

REPO_NAME = "book-fx-data"
DOMAIN = "fx"
SCHEMA_VERSION = "fx.v1"
RECORDS_FILE = "exchange_rates.csv"
HISTORY_FILE = "exchange_history.csv"
LINEAGE_FILE = "lake_lineage.json"
ID_FIELDS = ["base", "currency"]
ID_SEP = ":"

# Lake-first contract (shared product_adapter).
LAKE_SOURCE = REPO_NAME
LAKE_DOMAIN = "market"
LAKE_DATASET_RATES = "exchange_rates"
LAKE_DATASET_HISTORY = "exchange_history"
LAKE_BRONZE_SCHEMA_VERSION = "1"
LAKE_PRIVACY_CLASS = "public"
LAKE_RETENTION_CLASS = "operational"


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


FREE_ONLY = env_bool("FREE_ONLY", True)
ALLOW_PAID_PROVIDERS = env_bool("ALLOW_PAID_PROVIDERS", False)
ALLOW_EXTERNAL_WRITES = env_bool("ALLOW_EXTERNAL_WRITES", False)
ALLOW_REFRESH = env_bool("ALLOW_REFRESH", False)
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "")
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8103"))
CORS_ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
MAX_RESPONSE_BYTES = int(os.environ.get("MAX_RESPONSE_BYTES", str(2_000_000)))
STALE_AFTER_HOURS = float(os.environ.get("STALE_AFTER_HOURS", "24"))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "20"))

DATA_LAKE_URI = os.environ.get(
    "SOLO_EMPIRE_DATA_LAKE_URI",
    os.environ.get("DATA_LAKE_URI", ""),
)
SOLO_EMPIRE_ROOT = os.environ.get("SOLO_EMPIRE_ROOT", "")

# Direct Bronze Parquet is the default. Iceberg is an optional catalog-backed
# read pilot shared with the other lake-first data products.
LAKE_READ_MODE = os.environ.get("LAKE_READ_MODE", "parquet").strip().lower()
LAKE_READ_FALLBACK = os.environ.get("LAKE_READ_FALLBACK", "error").strip().lower()

# Silver parity/serving pilot. Bronze remains the default; compare is
# diagnostic-only and silver is fail-closed until API parity passes.
SILVER_READ_MODE = os.environ.get("SILVER_READ_MODE", "bronze").strip().lower()
