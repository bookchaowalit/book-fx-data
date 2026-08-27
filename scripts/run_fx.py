#!/usr/bin/env python3
"""Run bounded Frankfurter FX capture owned by this repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scrape_exchange_rates import ExchangeRateScraper

SYMBOLS = ["USD", "EUR", "JPY", "GBP", "CNY", "SGD", "HKD", "AUD", "KRW", "MYR"]


async def run_fx(output_dir: Path) -> list[dict[str, Any]]:
    scraper = ExchangeRateScraper(
        base="THB",
        symbols=SYMBOLS,
        alert_threshold=0.5,
        output_dir=output_dir,
    )
    batch = await scraper.run()
    print(f"[run_fx] exchange_rates: {batch[0].get('count') if batch else 0}")
    return batch


def main() -> int:
    parser = argparse.ArgumentParser(description="Run book-fx-data Frankfurter collection")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "exported")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = asyncio.run(run_fx(args.output_dir))
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
