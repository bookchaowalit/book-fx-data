#!/usr/bin/env python3
"""Capture Frankfurter FX rates for the scheduler.

The separate ``book-fx-data`` repository owns lake-first ingestion and its
read-only API. This producer validates the public response and writes only
local raw/capture artifacts for the downstream handoff.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import httpx
except ImportError as exc:  # pragma: no cover - requirements.txt supplies httpx
    raise RuntimeError("httpx is required for exchange-rate capture") from exc


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "exported"
FRANKFURTER_API = "https://api.frankfurter.dev/v1/latest"
DEFAULT_BASE = "THB"
DEFAULT_SYMBOLS = ["USD", "EUR", "JPY", "GBP", "CNY", "SGD", "HKD", "AUD", "KRW", "MYR"]
MAX_SYMBOLS = 30
RATE_FIELDS = [
    "date",
    "base",
    "currency",
    "rate",
    "inverse",
    "change_pct",
    "trend_7d",
    "trend_change_pct",
    "updated_at",
]
HISTORY_FIELDS = ["date", "base", "currency", "rate"]
_CURRENCY_RE = re.compile(r"^[A-Z]{3,5}$")


def _as_symbols(values: Iterable[str] | str | None, default: list[str]) -> list[str]:
    if values is None:
        values = default
    if isinstance(values, str):
        values = values.split(",")
    result: list[str] = []
    for value in values:
        symbol = str(value).strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def normalize_request(
    base: str | None,
    symbols: Iterable[str] | str | None,
) -> tuple[str, list[str]]:
    normalized_base = str(base or DEFAULT_BASE).strip().upper()
    normalized_symbols = _as_symbols(symbols, DEFAULT_SYMBOLS)
    if not _CURRENCY_RE.fullmatch(normalized_base):
        raise ValueError("base must be a 3-5 letter currency code")
    if not normalized_symbols or len(normalized_symbols) > MAX_SYMBOLS:
        raise ValueError(f"symbols must contain 1-{MAX_SYMBOLS} unique currencies")
    if any(not _CURRENCY_RE.fullmatch(symbol) for symbol in normalized_symbols):
        raise ValueError("symbols contain an invalid currency code")
    if normalized_base in normalized_symbols:
        raise ValueError("symbols must not include the base currency")
    return normalized_base, normalized_symbols


def _finite_rate(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    rate = float(value)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"{field} must be finite and greater than zero")
    return rate


def _valid_date(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Frankfurter response date is required")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Frankfurter response date must be YYYY-MM-DD") from exc
    return value


def validate_payload(payload: Any, base: str, symbols: list[str]) -> dict[str, Any]:
    """Fail closed on wrong base, missing symbols, invalid date, or bad rates."""

    if not isinstance(payload, dict):
        raise ValueError("Frankfurter response must be a JSON object")
    response_base = str(payload.get("base") or "").strip().upper()
    if response_base != base:
        raise ValueError(f"Frankfurter response base mismatch: expected {base}, got {response_base or 'missing'}")
    date = _valid_date(payload.get("date"))
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise ValueError("Frankfurter response rates must be an object")

    normalized_rates: dict[str, float] = {}
    for symbol in symbols:
        if symbol not in rates:
            raise ValueError(f"Frankfurter response is missing symbol: {symbol}")
        normalized_rates[symbol] = _finite_rate(rates[symbol], f"rates.{symbol}")
    return {"base": base, "date": date, "rates": normalized_rates}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _trend(previous: float | None, current: float, threshold: float) -> tuple[str, float | str]:
    if previous is None:
        return "unknown", ""
    change = round(((current - previous) / previous) * 100, 3)
    if change > threshold:
        direction = "strengthening"
    elif change < -threshold:
        direction = "weakening"
    else:
        direction = "stable"
    return direction, change


def load_previous_rates(history_path: Path) -> dict[str, float]:
    if not history_path.exists():
        return {}
    previous: dict[str, float] = {}
    try:
        with history_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                currency = str(row.get("currency") or "").upper()
                if not currency:
                    continue
                try:
                    rate = float(row.get("rate", ""))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(rate) and rate > 0:
                    previous[currency] = rate
    except OSError:
        return {}
    return previous


def rate_rows(
    payload: dict[str, Any],
    previous_rates: dict[str, float],
    threshold: float,
    observed_at: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for currency, rate in payload["rates"].items():
        direction, change = _trend(previous_rates.get(currency), rate, threshold)
        rows.append(
            {
                "date": payload["date"],
                "base": payload["base"],
                "currency": currency,
                "rate": rate,
                "inverse": round(1 / rate, 6),
                "change_pct": change,
                "trend_7d": direction,
                "trend_change_pct": change,
                "updated_at": observed_at,
            }
        )
    return rows


def fetch_rates(base: str, symbols: list[str]) -> tuple[bytes, dict[str, Any]]:
    response = httpx.get(
        FRANKFURTER_API,
        params={"from": base, "to": ",".join(symbols)},
        timeout=30,
    )
    response.raise_for_status()
    payload = validate_payload(response.json(), base, symbols)
    raw = getattr(response, "content", b"") or json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return raw, payload


def write_raw(raw: bytes, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "exchange_rates_raw.json"
    path.write_bytes(raw)
    return path


def write_snapshot(rows: list[dict[str, Any]], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "exchange_rates.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RATE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def append_history(rows: list[dict[str, Any]], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "exchange_history.csv"
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in HISTORY_FIELDS})
    return path


def print_alerts(rows: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    alerts = [
        row
        for row in rows
        if isinstance(row["change_pct"], (int, float)) and abs(row["change_pct"]) >= threshold
    ]
    for row in sorted(alerts, key=lambda item: abs(float(item["change_pct"])), reverse=True):
        print(
            f"  ALERT {row['trend_7d'].upper()}: {row['base']}->{row['currency']} "
            f"{row['change_pct']:+.3f}%"
        )
    return alerts


class ExchangeRateScraper:
    """Scheduler adapter for bounded Frankfurter rate capture."""

    def __init__(
        self,
        base: str = DEFAULT_BASE,
        symbols: Iterable[str] | str | None = None,
        alert_threshold: float = 0.5,
        output_dir: str | Path | None = None,
        **_: Any,
    ) -> None:
        self.base, self.symbols = normalize_request(base, symbols)
        self.alert_threshold = float(alert_threshold)
        if self.alert_threshold < 0:
            raise ValueError("alert_threshold must be non-negative")
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR

    async def run(self, **_: Any) -> list[dict[str, Any]]:
        raw, payload = fetch_rates(self.base, self.symbols)
        observed_at = _utc_now()
        history_path = self.output_dir / "exchange_history.csv"
        previous_rates = load_previous_rates(history_path)
        rows = rate_rows(payload, previous_rates, self.alert_threshold, observed_at)
        raw_path = write_raw(raw, self.output_dir)
        snapshot_path = write_snapshot(rows, self.output_dir)
        history_path = append_history(rows, self.output_dir)
        alerts = print_alerts(rows, self.alert_threshold)
        print(f"[exchange_rates] {len(rows)} rows -> {snapshot_path}")
        return [
            {
                "source": "exchange_rates",
                "count": len(rows),
                "alerts": len(alerts),
                "output": str(snapshot_path),
                "history": str(history_path),
                "raw": str(raw_path),
            }
        ]


if __name__ == "__main__":
    import asyncio

    asyncio.run(ExchangeRateScraper().run())

