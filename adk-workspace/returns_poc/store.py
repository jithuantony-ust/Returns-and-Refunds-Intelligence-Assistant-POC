"""Data access layer. Same SQL templates run on BigQuery (production target) or SQLite (local/offline).

Rules for query templates:
  * Parameters are written as @name and bound as STRING values. Never format user input into SQL.
  * Table names come from `{orders}` style placeholders filled in by this module (fixed names only).
  * Month filters use `{ym}`, which expands to the dialect's year-month expression.
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import re
import sqlite3
import threading
from typing import Any

from returns_poc.config import (
    BACKEND,
    BQ_DATASET,
    GCP_PROJECT_ID,
    LOCAL_DB_PATH,
    SCHEMAS,
    SYNTHETIC_DIR,
)

log = logging.getLogger("returns_poc.store")
_PARAM_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")


class Store:
    def __init__(self, backend: str | None = None):
        self.backend = (backend or BACKEND).lower()
        if self.backend not in ("sqlite", "bigquery"):
            raise ValueError(f"Unknown POC_BACKEND: {self.backend}")
        self._lock = threading.Lock()
        if self.backend == "sqlite":
            self._conn = sqlite3.connect(LOCAL_DB_PATH, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self.tables = {t: t for t in SCHEMAS}
            self.ym = lambda col: f"strftime('%Y-%m', {col})"
            self._client = None
        else:
            from google.cloud import bigquery  # imported lazily

            if not GCP_PROJECT_ID:
                raise RuntimeError("GCP_PROJECT_ID must be set for the bigquery backend.")
            self._client = bigquery.Client(project=GCP_PROJECT_ID)
            self._bq = bigquery
            self.tables = {t: f"`{GCP_PROJECT_ID}.{BQ_DATASET}.{t}`" for t in SCHEMAS}
            self.ym = lambda col: f"FORMAT_DATE('%Y-%m', {col})"
            self._conn = None

    def query(self, template: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        params = {k: str(v) for k, v in (params or {}).items()}
        sql = _replace_ym(template, self.ym).format(**self.tables)
        if self.backend == "sqlite":
            return self._query_sqlite(sql, params)
        return self._query_bigquery(sql, params)

    def _query_sqlite(self, sql, params):
        named = _PARAM_RE.sub(lambda m: ":" + m.group(1), sql)
        used = {m.group(1) for m in _PARAM_RE.finditer(sql)}
        bound = {k: params[k] for k in used}
        with self._lock:
            cur = self._conn.execute(named, bound)
            return [dict(r) for r in cur.fetchall()]

    def _query_bigquery(self, sql, params):
        bq = self._bq
        used = {m.group(1) for m in _PARAM_RE.finditer(sql)}
        cfg = bq.QueryJobConfig(
            query_parameters=[bq.ScalarQueryParameter(k, "STRING", params[k]) for k in used],
            maximum_bytes_billed=10 * 1024 * 1024 * 1024,  # 10 GB cap per query
        )
        job = self._client.query(sql, job_config=cfg, timeout=30)
        # BigQuery returns DATE columns as date objects; SQLite returns ISO strings. Normalise to strings
        # so the rule engine and the tools see the same types on both backends.
        return [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in r.items()}
                for r in job.result()]


def _replace_ym(sql: str, ym) -> str:
    """Expand {ym(column)} placeholders into the dialect's year-month expression."""
    return re.sub(r"\{ym\(([A-Za-z_.]+)\)\}", lambda m: ym(m.group(1)), sql)


def build_sqlite(csv_dir=SYNTHETIC_DIR, db_path=LOCAL_DB_PATH) -> dict[str, int]:
    """Load the CSV files into a local SQLite database (rebuilt from scratch)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    counts = {}
    for table, cols in SCHEMAS.items():
        names = [c for c, _, _ in cols]
        conn.execute(f"CREATE TABLE {table} ({', '.join(f'{c} {s}' for c, _, s in cols)})")
        with (csv_dir / f"{table}.csv").open(newline="", encoding="utf-8") as fh:
            rows = []
            for r in csv.DictReader(fh):
                rows.append([None if r[c] == "" else _coerce(r[c], t) for c, t in zip(names, [s for _, _, s in cols])])
        conn.executemany(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", rows)
        counts[table] = len(rows)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_oi_order ON order_items(order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ret_item ON returns(order_item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ref_ret ON refunds(return_id)")
    conn.commit()
    conn.close()
    return counts


def _coerce(value: str, sqlite_type: str):
    if sqlite_type == "REAL":
        return float(value)
    if sqlite_type == "INTEGER":
        return int(value)
    return value
