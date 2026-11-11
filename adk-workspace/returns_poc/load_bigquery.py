"""Load the synthetic CSVs, policy chunks and embeddings into BigQuery.

Prerequisites (see README):
  gcloud auth application-default login      (or a service account with the roles listed in the README)
  export GCP_PROJECT_ID=<your-project>
  export BQ_DATASET=retail_returns_poc      (optional)

Run from adk-workspace/:  python -m returns_poc.load_bigquery
Not run in the development environment for this POC. It is written against the google-cloud-bigquery API.
"""
from __future__ import annotations

import json
from datetime import date

from google.cloud import bigquery

from returns_poc.config import BQ_DATASET, GCP_PROJECT_ID, INDEX_DIR, SCHEMAS, SYNTHETIC_DIR

_BQ_TYPE_MAP = {"STRING": "STRING", "DATE": "DATE", "FLOAT64": "FLOAT64", "INT64": "INT64"}

EXTRA_TABLES = {
    "case_embeddings": [
        bigquery.SchemaField("case_id", "STRING"),
        bigquery.SchemaField("searchable_text", "STRING"),
        bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
        bigquery.SchemaField("model_version", "STRING"),
    ],
    "document_chunks": [
        bigquery.SchemaField("chunk_id", "STRING"),
        bigquery.SchemaField("doc_id", "STRING"),
        bigquery.SchemaField("source_file", "STRING"),
        bigquery.SchemaField("policy_version", "STRING"),
        bigquery.SchemaField("effective_from", "DATE"),
        bigquery.SchemaField("effective_to", "DATE"),
        bigquery.SchemaField("section", "STRING"),
        bigquery.SchemaField("page", "INT64"),
        bigquery.SchemaField("content", "STRING"),
        bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
        bigquery.SchemaField("embedding_model", "STRING"),
    ],
}


def _table_id(client: bigquery.Client, name: str) -> str:
    return f"{client.project}.{BQ_DATASET}.{name}"


def _csv_job_config(table: str) -> bigquery.LoadJobConfig:
    schema = [bigquery.SchemaField(c, _BQ_TYPE_MAP[t]) for c, t, _ in SCHEMAS[table]]
    return bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        allow_quoted_newlines=True,
    )


def _read_jsonl(path):
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load(project: str | None = None) -> dict[str, int]:
    client = bigquery.Client(project=project or GCP_PROJECT_ID)
    client.create_dataset(bigquery.Dataset(f"{client.project}.{BQ_DATASET}"), exists_ok=True)
    counts: dict[str, int] = {}

    for table in SCHEMAS:
        with (SYNTHETIC_DIR / f"{table}.csv").open("rb") as fh:
            job = client.load_table_from_file(fh, _table_id(client, table), job_config=_csv_job_config(table))
        job.result()
        counts[table] = client.get_table(_table_id(client, table)).num_rows

    cases = _read_jsonl(INDEX_DIR / "case_embeddings.jsonl")
    job = client.load_table_from_json(
        cases, _table_id(client, "case_embeddings"),
        job_config=bigquery.LoadJobConfig(schema=EXTRA_TABLES["case_embeddings"],
                                          write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE))
    job.result()
    counts["case_embeddings"] = len(cases)

    chunks = _read_jsonl(INDEX_DIR / "policy_chunks.jsonl")
    rows = []
    for c in chunks:
        rows.append({
            **{k: c[k] for k in ("chunk_id", "doc_id", "source_file", "policy_version", "section", "page", "content",
                                 "embedding", "embedding_model")},
            "effective_from": c["effective_from"],
            "effective_to": c["effective_to"],
        })
    job = client.load_table_from_json(
        rows, _table_id(client, "document_chunks"),
        job_config=bigquery.LoadJobConfig(schema=EXTRA_TABLES["document_chunks"],
                                          write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE))
    job.result()
    counts["document_chunks"] = len(rows)
    return counts


if __name__ == "__main__":
    for name, n in load().items():
        print(f"{name}: {n} rows")
