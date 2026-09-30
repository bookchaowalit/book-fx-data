# Upgrade plan — book-fx-data

Score: 8/10 -> 8.5/10 — NaN/inf/non-positive/duplicate rates are rejected before Bronze/CSV, cross-rate sanity is tested, CLI codes are validated and projections are atomic; remaining gaps are packaging polish.

## Backlog

- P0: Confirm the first CI run on GitHub is green (it now downloads the pinned
  `solo-empire-data-lake` tarball); keep it required on `main`.
- P1: When `solo-empire-data-lake` moves, bump the pinned commit in `[lake]` together with
  the other book-*-data repos (same SHA everywhere).
- P2: Add a `[project.optional-dependencies] dev` extra and a `[build-system]` table so
  `pip install -e ".[dev]"` is the single documented setup.
- P2: Surface the per-run `rejected_by_reason` counts in `/v1/metadata`.

## Done in this pass (pass 4: edge cases)

- `config.env_bool` returned False for anything but a true-ish word, so
  `FREE_ONLY=` (blank line in .env/compose) or a typo silently disabled the
  free-only guard; blank/unrecognised values now keep the safe default.
- `quality.finite_number` raised `OverflowError` for a JSON integer beyond
  float range; `clean_rates` now reports it as `invalid_rate`. A subnormal
  positive rate (`1e-320`) passed and produced `inverse: inf` in Bronze; such
  rates are rejected too.
- `ingest.detect_trend` crashed with `AttributeError` on a timeseries day with
  `"rates": null`, and reported an infinite change as "strengthening"; both
  now return `unknown`.
- Verified: `tests/test_edge_cases.py` (6 of 6 behaviours fail on the old
  code); full suite 62 passed; ruff 0.15.8 + 0.16.9.

## Done in this pass (pass 3)

- New `quality` module with `clean_rates`: `rate_records_from_api`,
  `history_records_from_api` (plus id de-dup) and the live run drop missing/NaN/inf/
  non-positive rates and duplicate codes; zero valid rates fail before any write.
- `detect_trend` ignores non-finite/non-positive endpoints instead of computing NaN.
- Cross-rate sanity tests: `rate * inverse ~= 1` for records and `fixtures/exchange_rates.csv`;
  fixture history keys are unique.
- CLI: three-letter code validation, base removed from symbols, threshold checks (exit 2).
- `fetch_history_raw` computes the ECB date window in UTC (was host-local time).
- New `fsutil` module: rates CSV replaced atomically, history appended via atomic rewrite.
- README Quick start uses `pip install -e ".[lake]"`; new "Data quality" section.
- Verified: `pytest -q -rs` 53 passed, 0 skipped (was 40) with the `[lake]` venv; ruff
  0.15.8 and 0.16.9 clean.
- Refresh auth: `/v1/refresh` compares the bearer token with `hmac.compare_digest`
  (`_refresh_token_ok`) instead of `==`, which leaked the matching prefix
  length through timing; `tests/test_refresh_token_compare.py` pins it.
- CSV projection stamps (`_projection_timestamp`) are UTC; they were host-local
  but `product_store.parse_ts` reads naive stamps as UTC, so freshness was off
  by the host offset (`tests/test_projection_timestamp_utc.py`).

## Done in pass 2

- Added a `[lake]` extra pinning `solo-empire-data-lake` at `68fb5a9` (plus pyarrow/duckdb); CI
  installs `-e ".[lake]"`, asserts `data_lake` imports, and lake tests now run instead of skipping.
- Test guards use `lake.shared_runtime_available()` (`importlib.util.find_spec("data_lake")`,
  then the `SOLO_EMPIRE_ROOT` / parent-checkout fallback) instead of `find_solo_empire_root()`.
- `[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]` pins the classic rule set: unpinned
  ruff 0.16 widened its defaults and would have failed `ruff check .` in CI.
- `tests/test_fixture_headers.py` pins `fixtures/*.csv` headers to the CSV projection.
- `SharedAdapterTests` now checks the adapter via the installed runtime instead of skipping outside the parent.
- Verified: fresh venv `pip install -e ".[lake]"` from the pinned tarball — 0 skipped of 40 with `[lake]` (was 26 of 39 skipped);
  also against `pip install /home/user/solo-empire-data-lake`, with `SOLO_EMPIRE_ROOT=<parent>`
  (parent adapter wins), and without the extra (lake tests skip with a reason). ruff 0.15 and 0.16 clean.

## Done in pass 1

- `lake.find_solo_empire_root()` now returns `None` when the shared `data_lake`
  adapter cannot be imported (it raised `ModuleNotFoundError` before), and the
  adapter loader also honours `SOLO_EMPIRE_ROOT` so a sibling clone of the parent
  repo works without editing `PYTHONPATH`.
- CI: installs `pytest ruff`, runs `ruff check .` and `python -m pytest -q -rs`
  (skip reasons visible) instead of bare `unittest`.
- Fixed the remaining default-rule ruff findings; `.gitignore` covers egg-info/ruff cache.
- README "Tests" section documents the standalone and full-lake commands.
- Verified: clean venv with `pip install -e . pytest` (CI shape) and a full run with
  `SOLO_EMPIRE_ROOT=<parent>` + pyarrow/duckdb (all lake tests execute and pass).
