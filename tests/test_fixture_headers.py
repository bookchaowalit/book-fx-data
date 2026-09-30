"""Offline fixtures must keep the same columns as the CSV projection.

``--offline`` seeds ``fixtures/*.csv`` into ``data/``; if a projection gains a
column the fixture silently drifts from what a real run writes.
"""
from __future__ import annotations

import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from book_fx import config, ingest

_FX_EMPTY = {"date": "2026-01-01", "base": "USD", "rates": {}}


def _header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        return next(csv.reader(f))


class FixtureHeaderTests(unittest.TestCase):
    def test_fixture_headers_match_csv_projection(self):
        projections = [
            ("project_rates_csv", lambda out: ingest.project_rates_csv(_FX_EMPTY, out)),
            ("project_history_csv", lambda out: ingest.project_history_csv(_FX_EMPTY, out)),
        ]
        for name, project in projections:
            with self.subTest(projection=name), tempfile.TemporaryDirectory() as tmp:
                with contextlib.redirect_stdout(io.StringIO()):
                    written = Path(project(Path(tmp)))
                fixture = config.FIXTURES_DIR / written.name
                self.assertTrue(fixture.is_file(), f"missing fixture {fixture.name}")
                self.assertEqual(_header(fixture), _header(written))


if __name__ == "__main__":
    unittest.main()
