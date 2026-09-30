# book-fx-data

FX exchange rates (Frankfurter free ECB API).

Migration status: **lake-first** via shared Solo Empire
`data_lake.product_adapter` (same path as `book-crypto-data` / `book-stock-data`).

- Producer: exact API bytes → landing → Bronze → manifest → optional CSV
- API: DuckDB over Bronze by default; guarded Silver parity mode (no CSV)
- PostgreSQL/ClickHouse: multi-engine lab only (not production)

Shared procedure (monorepo root):

- `docs/systems/data-lake-architecture.md`
- `learning/platform-engineering/app-cli-data-lake-playbook.md`
- `infra/scripts/data_lake/product_adapter.py`
- `infra/scripts/data_lake/product_store.py`

## Data contract

| Field | Value |
|---|---|
| `source` | `book-fx-data` |
| `domain` | `market` |
| `dataset` (snapshot) | `exchange_rates` |
| `dataset` (history) | `exchange_history` |
| Bronze `schema_version` | `1` |
| Product envelope | `fx.v1` |
| `privacy_class` | `public` |
| `retention_class` | `operational` |

```text
Frankfurter API
    → landing/source=book-fx-data/.../payload.json
    → bronze/domain=market/dataset=exchange_rates|exchange_history/...
    → control/manifests/
    → HTTP API  ← DuckDB query of Bronze
    → data/*.csv  (optional CLI projection only)
```

## Optional Iceberg REST catalog read pilot

FX uses the shared `product_store` feature flag. Direct Bronze Parquet remains
the default:

| Variable | Default | Meaning |
|---|---|---|
| `LAKE_READ_MODE` | `parquet` | Direct Bronze read; `iceberg` resolves the registered catalog table and queries it with DuckDB |
| `LAKE_READ_FALLBACK` | `error` | Fail-closed by default; `parquet` is an explicit fallback |
| `ICEBERG_CATALOG_URI` | unset | REST/SQL catalog URI from the runtime secret provider |
| `ICEBERG_WAREHOUSE_URI` | unset | Provider-issued warehouse URI/name |
| `ICEBERG_CATALOG_TOKEN` | unset | Catalog credential; never store it in the repository or API response |

The matching tables must be registered before enabling Iceberg mode:
`bronze.market_exchange_rates` and `bronze.market_exchange_history`.

```bash
npx --yes @infisical/cli@0.43.120 run \
  --projectId=<solo-empire-project-id> --env=dev --path=/cloudflare -- \
  bash -lc 'export SOLO_EMPIRE_DATA_LAKE_URI=s3://<bucket>/<prefix>; \
    export LAKE_READ_MODE=iceberg; export LAKE_READ_FALLBACK=error; \
    PYTHONPATH=src /path/to/solo-empire/.venv/bin/python \
    -m book_fx.api --host 127.0.0.1 --port 8103'
```

Successful `/v1/metadata` reports
`storage_model=lake_first_iceberg_rest_duckdb` and both source kinds as
`iceberg_rest_duckdb`. CSV remains CLI-only and is not a fallback unless the
explicit `LAKE_READ_FALLBACK=parquet` flag is selected.

### Live R2 parity evidence

Verified on 2026-08-07 with bounded prefix
`portfolio-demo/book-fx-iceberg-001`:

| Check | Result |
|---|---|
| Tables | `bronze.market_exchange_rates`, `bronze.market_exchange_history` |
| Rows | 2 rates + 2 history |
| Iceberg vs direct Parquet | parity passed for both datasets |
| Raw bytes and lineage | exact / passed |
| Registration retry | `already_registered` for both |
| Snapshot retry | idempotent for both |
| API storage model | `lake_first_iceberg_rest_duckdb` |
| CSV dependency | none |

The fixture is intentionally old, so `data_status=stale` is expected. The
default `LAKE_READ_MODE=parquet` remains unchanged.

## Silver parity/serving pilot

The shared `product_store` supports `SILVER_READ_MODE=bronze|compare|silver`:

| Value | Behavior |
|---|---|
| `bronze` | Read and serve Bronze; default |
| `compare` | Read Silver and report `silver_parity`, but serve Bronze |
| `silver` | Serve current-state Silver only after parity passes; fail-closed otherwise |

Generate `fx.silver.v1` from `exchange_rates` Bronze with the shared
`infra/scripts/data_lake/silver.py` command documented in the monorepo
[free-cloud runbook](../../../../../../../../../docs/operations/FREE-CLOUD-E2E-RUNBOOK.md),
then run `compare` and inspect `/v1/metadata` for
`silver_parity.status=passed`. `fx.silver.v1` is a deduplicated snapshot;
`/v1/history` remains on `exchange_history` Bronze until a separate Silver
history contract exists. CSV is never used by the HTTP API.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
pip install -r <solo-empire>/infra/requirements-data-lake.txt

python -m book_fx.ingest --fixture
python -m book_fx.ingest --base THB --symbols USD,EUR --data-lake-uri /path/to/data/lake
python -m book_fx.api --host 127.0.0.1 --port 8103
```

Live ingest exits non-zero on lake failure and does **not** update CSV.

## Local read-only API

Default bind: `127.0.0.1:8103`.

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | Bronze data status |
| GET | `/v1/metadata` | Bronze by default; reports `silver_parity` during the pilot |
| GET | `/v1/records` | Latest from Bronze or guarded `exchange_rates` Silver |
| GET | `/v1/history` | Full `exchange_history` Bronze |
| POST | `/v1/refresh` | **403 by default** |

## Free-only defaults

```text
FREE_ONLY=true
ALLOW_PAID_PROVIDERS=false
ALLOW_EXTERNAL_WRITES=false
API_HOST=127.0.0.1
API_PORT=8103
ALLOW_REFRESH=false
```

| Provider | Classification |
|---|---|
| `frankfurter_public` | free (ECB rates, no key) |
| `paid_fx_feed` | blocked |

## Tests

```bash
# Standalone (what CI runs): lake integration tests skip without the adapter
python -m pip install -e . pytest ruff
ruff check .
python -m pytest -q -rs

# Full lake coverage: point at a Solo Empire checkout that has
# infra/scripts/data_lake (sibling clones work; walking parents is the default)
python -m pip install pyarrow duckdb
SOLO_EMPIRE_ROOT=/path/to/solo-empire python -m pytest -q
```

## Safety

- API GET handlers read Bronze via DuckDB by default; Silver is guarded by
  parity and fail-closed serving modes.
- No secrets in source or fixtures.
- Do not enable public bind or `ALLOW_REFRESH` without owner approval.
