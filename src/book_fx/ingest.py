#!/usr/bin/env python3
"""
Exchange rates via Frankfurter API (free, ECB rates, no auth).

Lake-first flow (shared product_adapter with crypto/stock):
    Frankfurter API bytes
      → landing/ (exact bytes)
      → Bronze Parquet (exchange_rates + exchange_history) + manifest
      → optional local CSV projection under data/
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def _require_httpx():
    try:
        import httpx as _httpx
    except ImportError:
        print("ERROR: httpx required for live ingestion. Install: pip install httpx")
        raise SystemExit(1)
    return _httpx


class _HttpxProxy:
    def __getattr__(self, name):
        return getattr(_require_httpx(), name)


httpx = _HttpxProxy()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data"

try:
    from . import config as _dp_config
    from .policy import (
        evaluate_provider,
        require_provider,
        require_external_writes,
        external_writes_allowed,
    )
    from .store import seed_fixtures
    from . import lake as _lake
    from .fsutil import atomic_append_csv, atomic_write_csv
    from .quality import clean_rates, finite_number, summarize_rejections
except ImportError:  # pragma: no cover
    _dp_config = None
    _lake = None

    def evaluate_provider(name):
        class D:
            allowed = True
            status = "free"
            reason = ""

        return D()

    def require_provider(name):
        return evaluate_provider(name)

    def require_external_writes(action):
        return None

    def external_writes_allowed():
        return False

    def seed_fixtures(data_dir=None):
        return None


FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"
DEFAULT_BASE = "THB"
DEFAULT_SYMBOLS = ["USD", "EUR", "JPY", "GBP", "CNY", "SGD", "HKD", "AUD", "KRW", "MYR"]


def fetch_latest_raw(base: str, symbols: list[str]) -> tuple[bytes, dict]:
    """Fetch latest rates; return exact response bytes + normalized dict."""
    require_provider("frankfurter_public")
    symbols_str = ",".join(symbols)
    url = f"{FRANKFURTER_BASE}/latest"
    params = {"from": base, "to": symbols_str}
    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    raw = getattr(resp, "content", None) or json.dumps(resp.json()).encode("utf-8")
    data = resp.json()
    return raw, {
        "base": data.get("base", base),
        "date": data.get("date", ""),
        "rates": data.get("rates", {}),
    }


def fetch_latest(base: str, symbols: list) -> dict:
    _raw, data = fetch_latest_raw(base, symbols)
    return data


def fetch_history_raw(base: str, symbols: list[str], days: int = 30) -> tuple[bytes, list]:
    require_provider("frankfurter_public")
    # ECB dates are UTC calendar days; a host-local clock can skip or repeat one.
    today = datetime.now(timezone.utc)
    end_date = today.strftime("%Y-%m-%d")
    start_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    symbols_str = ",".join(symbols)
    url = f"{FRANKFURTER_BASE}/{start_date}..{end_date}"
    params = {"from": base, "to": symbols_str}
    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    raw = getattr(resp, "content", None) or json.dumps(resp.json()).encode("utf-8")
    data = resp.json()
    history = [
        {"date": date, "rates": rates}
        for date, rates in sorted(data.get("rates", {}).items())
    ]
    return raw, history


def fetch_history(base: str, symbols: list, days: int = 30) -> list:
    _raw, history = fetch_history_raw(base, symbols, days=days)
    return history


def detect_trend(history: list, symbol: str, lookback: int = 7) -> dict:
    if len(history) < lookback:
        return {"direction": "unknown", "change_pct": 0}

    recent = history[-lookback:]
    first_rate = finite_number(recent[0].get("rates", {}).get(symbol))
    last_rate = finite_number(recent[-1].get("rates", {}).get(symbol))

    if not first_rate or not last_rate or first_rate <= 0 or last_rate <= 0:
        return {"direction": "unknown", "change_pct": 0}

    change_pct = ((last_rate - first_rate) / first_rate) * 100
    if change_pct > 0.5:
        direction = "strengthening"
    elif change_pct < -0.5:
        direction = "weakening"
    else:
        direction = "stable"

    return {
        "direction": direction,
        "change_pct": round(change_pct, 3),
        "from_rate": first_rate,
        "to_rate": last_rate,
    }


def _projection_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def project_rates_csv(
    data: dict,
    output_dir: Path,
    trends: Optional[dict] = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now = _projection_timestamp()
    rates_file = output_dir / "exchange_rates.csv"
    fieldnames = [
        "date",
        "base",
        "currency",
        "rate",
        "inverse",
        "trend_7d",
        "trend_change_pct",
        "updated_at",
    ]
    rows = []
    for currency, rate in data["rates"].items():
        trend = trends.get(currency, {}) if trends else {}
        rows.append(
            {
                "date": data["date"],
                "base": data["base"],
                "currency": currency,
                "rate": rate,
                "inverse": round(1 / rate, 6) if rate else "",
                "trend_7d": trend.get("direction", ""),
                "trend_change_pct": trend.get("change_pct", ""),
                "updated_at": now,
            }
        )
    atomic_write_csv(rates_file, fieldnames, rows)
    print(f"  Projected {len(rows)} rates → {rates_file}")
    return rates_file


def project_history_csv(data: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    history_file = output_dir / "exchange_history.csv"
    fieldnames = ["date", "base", "currency", "rate"]
    rows = [
        {
            "date": data["date"],
            "base": data["base"],
            "currency": currency,
            "rate": rate,
        }
        for currency, rate in data["rates"].items()
    ]
    atomic_append_csv(history_file, fieldnames, rows)
    print(f"  Projected +{len(rows)} history rows → {history_file}")
    return history_file


# Backward-compatible aliases
def save_rates(data: dict, output_dir: Path, trends: dict = None):
    return project_rates_csv(data, output_dir, trends)


def append_history(data: dict, output_dir: Path):
    return project_history_csv(data, output_dir)


def print_summary(data: dict, trends: dict, threshold: float):
    print(f"\n  Rates ({data['date']}, base: {data['base']}):")
    for currency, rate in sorted(data["rates"].items()):
        trend = trends.get(currency, {})
        direction = trend.get("direction", "")
        change = trend.get("change_pct", 0) or 0
        arrow = "+" if change > 0 else "-" if change < 0 else "="
        alert = ""
        if abs(change) >= threshold:
            alert = f" *** ALERT: {abs(change):.2f}% move in 7 days"
        print(
            f"    1 {data['base']} = {rate:.4f} {currency}  "
            f"[{arrow} {change:+.3f}% 7d {direction}]{alert}"
        )


def _lake_ingest_rates(
    raw: bytes,
    data: dict,
    trends: dict,
    *,
    data_lake_uri: Optional[str],
    output_dir: Path,
) -> dict[str, Any]:
    if _lake is None or _dp_config is None:
        raise RuntimeError("Lake adapter unavailable in this runtime")
    records = _lake.rate_records_from_api(data, trends=trends)
    result = _lake.ingest_to_lake(
        raw=raw,
        records=records,
        dataset=_dp_config.LAKE_DATASET_RATES,
        data_lake_uri=data_lake_uri,
        metadata={"endpoint": "v1/latest", "dataset_role": "snapshot"},
    )
    _lake.write_lineage(
        result, dataset=_dp_config.LAKE_DATASET_RATES, data_dir=output_dir
    )
    print(
        f"  Lake exchange_rates: run_id={result.get('run_id')} "
        f"records={result.get('record_count')} bronze={result.get('bronze_key')}"
    )
    return result


def _lake_ingest_history(
    history_raw: bytes,
    history: list,
    base: str,
    *,
    data_lake_uri: Optional[str],
    output_dir: Path,
) -> dict[str, Any]:
    if _lake is None or _dp_config is None:
        raise RuntimeError("Lake adapter unavailable in this runtime")
    records = _lake.history_records_from_api(history, base=base)
    if not records:
        # Fallback: empty timeseries still should not block rates snapshot.
        raise _lake.LakeIngestError("History payload produced zero records")
    result = _lake.ingest_to_lake(
        raw=history_raw,
        records=records,
        dataset=_dp_config.LAKE_DATASET_HISTORY,
        data_lake_uri=data_lake_uri,
        metadata={"endpoint": "v1/timeseries", "dataset_role": "history"},
    )
    _lake.write_lineage(
        result, dataset=_dp_config.LAKE_DATASET_HISTORY, data_dir=output_dir
    )
    print(
        f"  Lake exchange_history: run_id={result.get('run_id')} "
        f"records={result.get('record_count')} bronze={result.get('bronze_key')}"
    )
    return result


def run_live_ingest(
    *,
    base: str,
    symbols: list[str],
    output_dir: Path,
    alert_threshold: float = 0.5,
    fetch_history_flag: bool = True,
    data_lake_uri: Optional[str] = None,
    project_csv: bool = True,
) -> dict[str, Any]:
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        "Exchange Rate Scraper (lake-first)"
    )
    print(f"  Base: {base} | Symbols: {symbols}")
    if data_lake_uri:
        print(f"  Data lake: {data_lake_uri}")
    elif _lake is not None:
        print(f"  Data lake: {_lake.default_data_lake_uri()}")

    print("  Fetching latest rates...")
    raw_latest, data = fetch_latest_raw(base, symbols)
    data["rates"], rejected = clean_rates(data.get("rates"))
    print(f"  Got {len(data['rates'])} rates (date: {data['date']})")
    if rejected:
        print(f"  Quality: dropped {len(rejected)} rates {summarize_rejections(rejected)}")
    if not data["rates"]:
        raise RuntimeError("Latest payload produced zero valid rates")

    trends: dict[str, Any] = {}
    history_raw: Optional[bytes] = None
    history_list: list = []
    if fetch_history_flag:
        print("  Fetching 30-day history for trend analysis...")
        try:
            history_raw, history_list = fetch_history_raw(base, symbols, days=30)
            for symbol in symbols:
                trends[symbol] = detect_trend(history_list, symbol, lookback=7)
        except Exception as e:  # noqa: BLE001
            print(f"  History fetch failed: {e}")

    # Durable rates write before any CSV; fail-closed if this raises.
    lake_rates = _lake_ingest_rates(
        raw_latest,
        data,
        trends,
        data_lake_uri=data_lake_uri,
        output_dir=output_dir,
    )

    lake_history = None
    if fetch_history_flag:
        try:
            if history_raw is not None and history_list:
                lake_history = _lake_ingest_history(
                    history_raw,
                    history_list,
                    base,
                    data_lake_uri=data_lake_uri,
                    output_dir=output_dir,
                )
            else:
                # Snapshot fallback so exchange_history has lineage when timeseries fails.
                snap_hist = [{"date": data["date"], "rates": data["rates"]}]
                hist_raw = json.dumps(
                    {
                        "base": base,
                        "dataset": "exchange_history",
                        "from_snapshot": True,
                        "days": snap_hist,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                lake_history = _lake_ingest_history(
                    hist_raw,
                    snap_hist,
                    base,
                    data_lake_uri=data_lake_uri,
                    output_dir=output_dir,
                )
        except Exception as e:  # noqa: BLE001
            print(f"  History lake write skipped: {e}")

    if project_csv:
        project_rates_csv(data, output_dir, trends)
        project_history_csv(data, output_dir)

    print_summary(data, trends, alert_threshold)
    print("\n  Done (lake durable; CSV is projection only).")
    return {
        "rates": len(data.get("rates") or {}),
        "lake_rates": lake_rates,
        "lake_history": lake_history,
        "projected_csv": project_csv,
    }


_CURRENCY_RE = re.compile(r"[A-Z]{3}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape exchange rates (lake-first; CSV is projection)"
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"Base currency (default: {DEFAULT_BASE})")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated target currencies",
    )
    parser.add_argument(
        "--alert-threshold",
        type=float,
        default=0.5,
        help="Alert on 7-day change >= this %% (default: 0.5)",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Skip historical timeseries fetch/lake write",
    )
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--data-lake-uri", default=None)
    parser.add_argument(
        "--no-project-csv",
        action="store_true",
        help="Skip local CSV projection after lake write",
    )
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    base = args.base.strip().upper()
    symbols = [
        s for s in dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip())
        if s != base
    ]
    if not _CURRENCY_RE.fullmatch(base):
        parser.error("--base must be a three-letter currency code")
    if not symbols:
        parser.error("--symbols must name at least one currency other than --base")
    bad = [s for s in symbols if not _CURRENCY_RE.fullmatch(s)]
    if bad:
        parser.error(f"--symbols has invalid currency codes: {','.join(bad)}")
    if not math.isfinite(args.alert_threshold) or args.alert_threshold < 0:
        parser.error("--alert-threshold must be a finite, non-negative number")
    output_dir = Path(args.output_dir)

    if getattr(args, "fixture", False) or getattr(args, "dry_run", False):
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        if _dp_config is not None:
            _dp_config.DATA_DIR = out
        seed_fixtures(out)
        print(
            "["
            + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            + "] Fixture mode: seeded local projection under "
            + str(out)
        )
        print("  No upstream providers contacted; no lake write.")
        return 0

    try:
        run_live_ingest(
            base=base,
            symbols=symbols,
            output_dir=output_dir,
            alert_threshold=args.alert_threshold,
            fetch_history_flag=not args.no_history,
            data_lake_uri=args.data_lake_uri,
            project_csv=not args.no_project_csv,
        )
    except Exception as exc:  # noqa: BLE001
        if _lake is not None and isinstance(
            exc, (_lake.LakeUnavailable, _lake.LakeIngestError)
        ):
            print(
                f"ERROR: lake ingest failed before projection: {exc}",
                file=sys.stderr,
            )
            return 2
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


class ExchangeRateScraper:
    """Wrapper class for scheduler compatibility (lake-first)."""

    def __init__(self, base=None, symbols=None, alert_threshold=0.5, **kwargs):
        self.base = base or DEFAULT_BASE
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.alert_threshold = alert_threshold
        self.data_lake_uri = kwargs.get("data_lake_uri")
        self.output_dir = Path(kwargs.get("output_dir") or OUTPUT_DIR)

    async def run(self, **kwargs):
        result = run_live_ingest(
            base=self.base,
            symbols=self.symbols,
            output_dir=Path(kwargs.get("output_dir") or self.output_dir),
            alert_threshold=self.alert_threshold,
            fetch_history_flag=True,
            data_lake_uri=kwargs.get("data_lake_uri", self.data_lake_uri),
            project_csv=True,
        )
        return [{"source": "exchange_rates", "count": result["rates"], "lake": True}]


if __name__ == "__main__":
    raise SystemExit(main())
