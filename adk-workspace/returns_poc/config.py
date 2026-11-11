"""Central configuration and table schemas for the StyleCart Returns & Refunds POC.

Secrets are never stored here. Read them from environment variables (see .env.example).
"""
from __future__ import annotations

import os
from pathlib import Path

# On corporate networks, Python's bundled CA list may not trust the proxy's root certificate.
# truststore makes TLS use the OS certificate store instead. Verification stays on.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # optional; install with: pip install truststore
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # adk-workspace/
SYNTHETIC_DIR = PROJECT_ROOT / "synthetic_data"
POLICY_DIR = PROJECT_ROOT / "policy_docs"
INDEX_DIR = PROJECT_ROOT / "index"
LOCAL_DB_PATH = PROJECT_ROOT / "local" / "retail_returns_poc.sqlite"
SQL_DIR = PROJECT_ROOT / "sql"

# Backend: "sqlite" (local, offline, default for tests) or "bigquery" (real project).
BACKEND = os.getenv("POC_BACKEND", "sqlite").lower()
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
BQ_DATASET = os.getenv("BQ_DATASET", "retail_returns_poc")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")
MODEL_PROMPT_VERSION = "root-v1|policy-v1|search-v1"

# Limits for agent runs and tools.
MAX_LLM_CALLS = int(os.getenv("MAX_LLM_CALLS", "8"))
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "60"))
MAX_TOP_K = 10

# Table schemas: (column, BigQuery type, SQLite type). Single source for DDL and loading.
SCHEMAS: dict[str, list[tuple[str, str, str]]] = {
    "customers": [
        ("customer_id", "STRING", "TEXT"),
        ("region", "STRING", "TEXT"),
        ("loyalty_tier", "STRING", "TEXT"),
        ("created_at", "DATE", "TEXT"),
    ],
    "orders": [
        ("order_id", "STRING", "TEXT"),
        ("customer_id", "STRING", "TEXT"),
        ("order_date", "DATE", "TEXT"),
        ("delivered_at", "DATE", "TEXT"),
        ("payment_status", "STRING", "TEXT"),
        ("order_total", "FLOAT64", "REAL"),
    ],
    "order_items": [
        ("order_item_id", "STRING", "TEXT"),
        ("order_id", "STRING", "TEXT"),
        ("sku", "STRING", "TEXT"),
        ("item_category", "STRING", "TEXT"),
        ("item_price", "FLOAT64", "REAL"),
        ("qty", "INT64", "INTEGER"),
        ("final_sale", "STRING", "TEXT"),  # 'Y' / 'N'
    ],
    "returns": [
        ("return_id", "STRING", "TEXT"),
        ("order_item_id", "STRING", "TEXT"),
        ("request_date", "DATE", "TEXT"),
        ("reason_code", "STRING", "TEXT"),
        ("status", "STRING", "TEXT"),
        ("requested_refund", "FLOAT64", "REAL"),
        ("evidence_attached", "STRING", "TEXT"),  # 'Y' / 'N'
    ],
    "refunds": [
        ("refund_id", "STRING", "TEXT"),
        ("return_id", "STRING", "TEXT"),
        ("approved_amount", "FLOAT64", "REAL"),
        ("disbursed_amount", "FLOAT64", "REAL"),
        ("refund_status", "STRING", "TEXT"),
        ("updated_at", "DATE", "TEXT"),
    ],
    "support_cases": [
        ("case_id", "STRING", "TEXT"),
        ("return_id", "STRING", "TEXT"),
        ("narrative", "STRING", "TEXT"),
        ("resolution", "STRING", "TEXT"),
        ("case_status", "STRING", "TEXT"),
        ("created_at", "DATE", "TEXT"),
    ],
}


def bigquery_ddl(dataset: str = "retail_returns_poc") -> str:
    """Return CREATE TABLE statements for BigQuery (Standard SQL)."""
    parts = [f"CREATE SCHEMA IF NOT EXISTS `{dataset}`;"]
    for table, cols in SCHEMAS.items():
        body = ",\n  ".join(f"{name} {bq}" for name, bq, _ in cols)
        parts.append(
            f"CREATE OR REPLACE TABLE `{dataset}.{table}` (\n  {body}\n);"
        )
    return "\n\n".join(parts) + "\n"
