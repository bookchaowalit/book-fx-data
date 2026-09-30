# Upgrade plan — book-fx-data

Score: 6/10 -> 7/10 — Tests already skipped without the adapter, but CI had no lint and only `unittest`.

## Backlog

- P0: Confirm the first CI run on GitHub is green; keep it required on `main`.
- P1: Publish the adapter pieces this product uses in `solo-empire-data-lake` and pin it
  as a `[lake]` extra (as `book-job-data` does) so CI can run the lake tests instead of
  skipping them; today most lake coverage only runs inside the parent repo.
- P1: Add a test that `fixtures/*.csv` headers match the CSV projection `fieldnames` in
  `ingest.py` so the offline fixtures cannot drift from the projection contract.
- P2: Add a `[project.optional-dependencies] dev` extra and a `[build-system]` table so
  `pip install -e ".[dev]"` is the single documented setup.
- P1: Add a THB cross-rate sanity test (inverse ~= 1/rate) on the fixture rows.

## Done in this pass

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
