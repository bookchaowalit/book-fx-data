"""Lake-first adapter for book-fx-data (shared product_adapter contract)."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config
from . import quality


def _load_shared():
    cur = config.PROJECT_ROOT.resolve()
    candidates = [cur, *cur.parents]
    if config.SOLO_EMPIRE_ROOT:
        # Explicit parent checkout (e.g. sibling clone) wins over the walk.
        candidates.insert(0, Path(config.SOLO_EMPIRE_ROOT).expanduser().resolve())
    for parent in candidates:
        scripts = parent / "infra" / "scripts"
        if (scripts / "data_lake" / "product_adapter.py").is_file():
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            break
    from data_lake import product_adapter as pa  # type: ignore
    return pa


_pa = None


def _pa_mod():
    global _pa
    if _pa is None:
        _pa = _load_shared()
    return _pa


class LakeUnavailable(RuntimeError):
    pass


class LakeIngestError(RuntimeError):
    pass


def _sync_exc():
    pa = _pa_mod()
    global LakeUnavailable, LakeIngestError
    LakeUnavailable = pa.LakeUnavailable  # type: ignore
    LakeIngestError = pa.LakeIngestError  # type: ignore


def _contract():
    pa = _pa_mod()
    _sync_exc()
    return pa.LakeProductContract(
        source=config.LAKE_SOURCE,
        domain=config.LAKE_DOMAIN,
        product_schema_version=config.SCHEMA_VERSION,
        privacy_class=config.LAKE_PRIVACY_CLASS,
        retention_class=config.LAKE_RETENTION_CLASS,
        bronze_schema_version=config.LAKE_BRONZE_SCHEMA_VERSION,
        project_root=config.PROJECT_ROOT,
        data_lake_uri=config.DATA_LAKE_URI,
        solo_empire_root=config.SOLO_EMPIRE_ROOT,
        lineage_filename=config.LINEAGE_FILE,
        datasets=(config.LAKE_DATASET_RATES, config.LAKE_DATASET_HISTORY),
    )


def utc_now_iso() -> str:
    return _pa_mod().utc_now_iso()


def find_solo_empire_root(start: Optional[Path] = None) -> Optional[Path]:
    """Return the Solo Empire root, or ``None`` when the shared adapter is absent."""
    try:
        pa = _pa_mod()
    except ImportError:
        return None
    return pa.find_solo_empire_root(
        start or config.PROJECT_ROOT,
        solo_empire_root=config.SOLO_EMPIRE_ROOT,
    )


def shared_runtime_available() -> bool:
    """Return True when the shared ``data_lake`` runtime can be imported.

    Covers the installed ``[lake]`` extra (``solo-empire-data-lake``) and the
    ``SOLO_EMPIRE_ROOT`` / parent-checkout fallback used inside Solo Empire.
    """
    if importlib.util.find_spec("data_lake") is not None:
        return True
    try:
        _pa_mod()
    except ImportError:
        return False
    return True


def default_data_lake_uri(solo_root: Optional[Path] = None) -> str:
    del solo_root
    return _pa_mod().default_data_lake_uri(
        data_lake_uri=config.DATA_LAKE_URI,
        project_root=config.PROJECT_ROOT,
        solo_empire_root=config.SOLO_EMPIRE_ROOT,
    )


def rate_records_from_api(
    data: dict[str, Any],
    *,
    trends: Optional[dict[str, dict[str, Any]]] = None,
    event_time: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Normalize Frankfurter latest payload into Bronze-ready records."""
    received = event_time or utc_now_iso()
    base = str(data.get("base", "")).strip().upper()
    rate_date = str(data.get("date", "")).strip()
    # Prefer ECB date as event_time when available.
    row_event = received
    if rate_date:
        try:
            dt = datetime.strptime(rate_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            row_event = dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass

    # Non-finite, non-positive or non-numeric rates and duplicate currency
    # codes never reach Bronze (quality.clean_rates).
    rates, _rejected = quality.clean_rates(data.get("rates") or {})
    records: list[dict[str, Any]] = []
    trends = trends or {}
    for curr, rate_f in rates.items():
        if not base:
            continue
        inverse = round(1 / rate_f, 6)
        trend = trends.get(curr) or {}
        records.append(
            {
                "id": f"{base}:{curr}",
                "base": base,
                "currency": curr,
                "rate": rate_f,
                "inverse": inverse,
                "date": rate_date,
                "trend_7d": trend.get("direction", ""),
                "trend_change_pct": trend.get("change_pct", ""),
                "event_time": row_event,
            }
        )
    return records


def history_records_from_api(
    history: list[dict[str, Any]],
    *,
    base: str,
) -> list[dict[str, Any]]:
    """Normalize Frankfurter timeseries into Bronze history records."""
    base = str(base).strip().upper()
    records: list[dict[str, Any]] = []
    for day in history:
        rate_date = str(day.get("date", "")).strip()
        rates = day.get("rates") or {}
        row_event = utc_now_iso()
        if rate_date:
            try:
                dt = datetime.strptime(rate_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                row_event = dt.isoformat().replace("+00:00", "Z")
            except ValueError:
                pass
        clean, _rejected = quality.clean_rates(rates)
        for curr, rate in clean.items():
            records.append(
                {
                    "id": f"{base}:{curr}:{rate_date}",
                    "base": base,
                    "currency": curr,
                    "rate": rate,
                    "date": rate_date,
                    "event_time": row_event,
                }
            )
    # Repeated dates in a timeseries would otherwise duplicate ids.
    records, _duplicates = quality.dedupe_by_key(records)
    return records


def ingest_to_lake(
    *,
    raw: bytes,
    records: list[dict[str, Any]],
    dataset: str,
    data_lake_uri: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    content_type: str = "application/json",
    input_format: str = "json",
) -> dict[str, Any]:
    _sync_exc()
    return _pa_mod().ingest_to_lake(
        _contract(),
        raw=raw,
        records=records,
        dataset=dataset,
        data_lake_uri=data_lake_uri,
        metadata=metadata,
        content_type=content_type,
        input_format=input_format,
        provider="frankfurter_public",
    )


def write_lineage(
    result: dict[str, Any],
    *,
    dataset: str,
    data_dir: Optional[Path] = None,
) -> Path:
    return _pa_mod().write_lineage(
        _contract(),
        result,
        dataset=dataset,
        data_dir=Path(data_dir or config.DATA_DIR),
    )


def load_lineage(data_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    return _pa_mod().load_lineage(
        Path(data_dir or config.DATA_DIR),
        filename=config.LINEAGE_FILE,
    )


def bronze_dataset_dir(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
) -> Path:
    return _pa_mod().bronze_dataset_dir(
        _contract(), dataset, data_lake_uri=data_lake_uri
    )


def read_bronze_rows(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
    sql: str | None = None,
) -> list[dict[str, Any]]:
    _sync_exc()
    return _pa_mod().read_bronze_rows(
        _contract(), dataset, data_lake_uri=data_lake_uri, sql=sql
    )


def read_silver_rows(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
    sql: str | None = None,
) -> list[dict[str, Any]]:
    """Read the shared Silver Parquet projection for a product dataset."""
    _sync_exc()
    try:
        from data_lake.silver import read_silver_rows as _read_shared_silver  # type: ignore

        return _read_shared_silver(
            data_lake_uri=data_lake_uri or default_data_lake_uri(),
            domain=config.LAKE_DOMAIN,
            dataset=dataset,
            silver_schema_version="1",
            sql=sql,
        )
    except (LakeUnavailable, LakeIngestError):
        raise
    except Exception as exc:  # noqa: BLE001 - normalize shared runtime errors
        raise LakeIngestError(
            f"Silver DuckDB query failed for {dataset}: {exc}"
        ) from exc


def read_iceberg_rows(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
    sql: str | None = None,
) -> list[dict[str, Any]]:
    _sync_exc()
    return _pa_mod().read_iceberg_rows(
        _contract(), dataset, data_lake_uri=data_lake_uri, sql=sql
    )


def parse_payload_json(row: dict[str, Any]) -> dict[str, Any]:
    return _pa_mod().parse_payload_json(row)


def select_latest_bronze_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _pa_mod().select_latest_bronze_rows(rows)


def landing_object_bytes(raw_key: str, *, data_lake_uri: Optional[str] = None) -> bytes:
    _sync_exc()
    uri = data_lake_uri or default_data_lake_uri()
    return _pa_mod().landing_object_bytes(uri, raw_key)
