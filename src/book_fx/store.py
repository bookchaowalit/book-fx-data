"""Read-only store for book-fx-data (Bronze via shared product_store)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from . import config
from . import lake


def _load_product_store():
    cur = config.PROJECT_ROOT.resolve()
    for parent in [cur, *cur.parents]:
        scripts = parent / "infra" / "scripts"
        if (scripts / "data_lake" / "product_store.py").is_file():
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            break
    from data_lake import product_store as ps  # type: ignore
    return ps


_ps = None


def _ps_mod():
    global _ps
    if _ps is None:
        _ps = _load_product_store()
    return _ps


def storage_model_for_source_kind(source_kind: Any) -> str:
    return _ps_mod().storage_model_for_source_kind(source_kind, product="fx")


def _contract():
    from data_lake.product_adapter import LakeProductContract  # type: ignore

    return LakeProductContract(
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
    return _ps_mod().utc_now_iso()


def make_record_id(row: dict[str, Any]) -> str:
    return _ps_mod().make_record_id(
        row, id_fields=config.ID_FIELDS, id_sep=config.ID_SEP
    )


def fx_item_from_bronze(
    row: dict[str, Any],
    *,
    history: bool = False,
    history_idx: int = 0,
) -> dict[str, Any]:
    payload = lake.parse_payload_json(row)
    event_time = str(row.get("event_time") or payload.get("event_time") or "")
    item: dict[str, Any] = {
        "base": str(payload.get("base", "")),
        "currency": str(payload.get("currency", "")),
        "rate": str(payload.get("rate", "")),
        "inverse": str(payload.get("inverse", "")),
        "date": str(payload.get("date", "")),
        "trend_7d": str(payload.get("trend_7d", "")),
        "trend_change_pct": str(payload.get("trend_change_pct", "")),
        "updated_at": event_time,
        "event_time": event_time,
        "ingest_run_id": str(row.get("ingest_run_id", "")),
        "source_record_id": str(row.get("source_record_id", "")),
        "raw_object_key": str(row.get("raw_object_key", "")),
    }
    if history:
        item["record_id"] = make_record_id(item) + f"#h{history_idx}"
    else:
        item["record_id"] = make_record_id(item)
    return item


def fx_item_from_silver(
    row: dict[str, Any],
    *,
    history: bool = False,
    history_idx: int = 0,
) -> dict[str, Any]:
    """Project shared Silver FX rows into the existing API item shape."""
    event_time = str(row.get("event_time") or "")
    item: dict[str, Any] = {
        "base": str(row.get("base_currency", "")),
        "currency": str(row.get("quote_currency", "")),
        "rate": str(row.get("rate", "")),
        "inverse": str(row.get("inverse_rate", "")),
        "date": str(row.get("rate_date", "")),
        "trend_7d": str(row.get("trend_7d", "")),
        "trend_change_pct": str(row.get("trend_change_pct", "")),
        "updated_at": event_time,
        "event_time": event_time,
        "ingest_run_id": str(row.get("bronze_ingest_run_id", "")),
        "source_record_id": str(row.get("source_record_id", "")),
        "raw_object_key": str(row.get("raw_object_key", "")),
    }
    if history:
        item["record_id"] = make_record_id(item) + f"#h{history_idx}"
    else:
        item["record_id"] = make_record_id(item)
    return item


def _lake_uri() -> Optional[str]:
    if config.DATA_LAKE_URI.strip():
        return config.DATA_LAKE_URI.strip()
    return lake.default_data_lake_uri()


def load_records(
    data_dir: Optional[Path] = None,
    *,
    data_lake_uri: Optional[str] = None,
) -> dict[str, Any]:
    del data_dir
    return _ps_mod().load_layered_dataset(
        _contract(),
        config.LAKE_DATASET_RATES,
        data_lake_uri=data_lake_uri or _lake_uri(),
        latest_only=True,
        id_fields=config.ID_FIELDS,
        id_sep=config.ID_SEP,
        stale_after_hours=config.STALE_AFTER_HOURS,
        bronze_item_builder=fx_item_from_bronze,
        silver_item_builder=fx_item_from_silver,
        read_mode=config.LAKE_READ_MODE,
        read_fallback=config.LAKE_READ_FALLBACK,
        silver_read_mode=config.SILVER_READ_MODE,
        compare_fields=(
            "base",
            "currency",
            "rate",
            "inverse",
            "date",
            "trend_7d",
            "trend_change_pct",
        ),
    )


def load_history(
    data_dir: Optional[Path] = None,
    *,
    data_lake_uri: Optional[str] = None,
) -> dict[str, Any]:
    del data_dir
    return _ps_mod().load_layered_dataset(
        _contract(),
        config.LAKE_DATASET_HISTORY,
        data_lake_uri=data_lake_uri or _lake_uri(),
        latest_only=False,
        id_fields=config.ID_FIELDS,
        id_sep=config.ID_SEP,
        stale_after_hours=config.STALE_AFTER_HOURS,
        bronze_item_builder=fx_item_from_bronze,
        silver_item_builder=fx_item_from_silver,
        read_mode=config.LAKE_READ_MODE,
        read_fallback=config.LAKE_READ_FALLBACK,
        silver_read_mode=config.SILVER_READ_MODE,
    )


def get_record(
    record_id: str,
    data_dir: Optional[Path] = None,
    *,
    data_lake_uri: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    payload = load_records(data_dir, data_lake_uri=data_lake_uri)
    return _ps_mod().get_record_from_payload(record_id, payload)


def paginate(
    items: list[dict[str, Any]],
    *,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    return _ps_mod().paginate(items, limit=limit, cursor=cursor)


def envelope(
    *,
    items: list[dict[str, Any]],
    data_status: str,
    next_cursor: Optional[str] = None,
    retrieved_at: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return _ps_mod().envelope(
        schema_version=config.SCHEMA_VERSION,
        source=config.REPO_NAME,
        items=items,
        data_status=data_status,
        next_cursor=next_cursor,
        retrieved_at=retrieved_at,
        extra=extra,
    )


def load_csv_projection(
    *,
    kind: str = "records",
    data_dir: Optional[Path] = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir or config.DATA_DIR)
    path = data_dir / (config.HISTORY_FILE if kind == "history" else config.RECORDS_FILE)
    return _ps_mod().load_csv_projection(
        path=path,
        id_fields=config.ID_FIELDS,
        id_sep=config.ID_SEP,
        history=(kind == "history"),
        stale_after_hours=config.STALE_AFTER_HOURS,
    )


def seed_fixtures(data_dir: Optional[Path] = None) -> Path:
    data_dir = Path(data_dir or config.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    src_records = config.FIXTURES_DIR / config.RECORDS_FILE
    src_history = config.FIXTURES_DIR / config.HISTORY_FILE
    if src_records.exists():
        (data_dir / config.RECORDS_FILE).write_text(
            src_records.read_text(encoding="utf-8"), encoding="utf-8"
        )
    if src_history.exists():
        (data_dir / config.HISTORY_FILE).write_text(
            src_history.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return data_dir


def seed_lake_from_rates(
    data: dict[str, Any],
    *,
    data_lake_uri: str,
    data_dir: Optional[Path] = None,
    trends: Optional[dict[str, dict[str, Any]]] = None,
    history_days: Optional[list[dict[str, Any]]] = None,
    raw: Optional[bytes] = None,
) -> dict[str, Any]:
    """Test/dev helper: write exchange_rates + exchange_history Bronze parts."""
    records = lake.rate_records_from_api(data, trends=trends)
    payload = raw or json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    rates = lake.ingest_to_lake(
        raw=payload,
        records=records,
        dataset=config.LAKE_DATASET_RATES,
        data_lake_uri=data_lake_uri,
        metadata={"seed": "sample", "dataset_role": "snapshot"},
    )
    if history_days:
        hist_records = lake.history_records_from_api(
            history_days, base=str(data.get("base", "THB"))
        )
    else:
        # Snapshot also lands as a history point when no timeseries provided.
        hist_records = [
            {
                **{k: v for k, v in r.items() if k != "id"},
                "id": f"{r['base']}:{r['currency']}:{r.get('date', '')}",
            }
            for r in records
        ]
    history_raw = json.dumps(
        {"base": data.get("base"), "rates": history_days or data.get("rates"), "dataset": "exchange_history"},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    history = lake.ingest_to_lake(
        raw=history_raw,
        records=hist_records,
        dataset=config.LAKE_DATASET_HISTORY,
        data_lake_uri=data_lake_uri,
        metadata={"seed": "sample", "dataset_role": "history"},
    )
    if data_dir is not None:
        lake.write_lineage(rates, dataset=config.LAKE_DATASET_RATES, data_dir=data_dir)
        lake.write_lineage(history, dataset=config.LAKE_DATASET_HISTORY, data_dir=data_dir)
    return {"rates": rates, "history": history}
