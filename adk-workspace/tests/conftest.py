"""Shared test setup. Builds the synthetic data, policy PDFs, SQLite DB and retrieval index if they are missing."""
import os

import pytest

from returns_poc.config import INDEX_DIR, LOCAL_DB_PATH, POLICY_DIR, SYNTHETIC_DIR

os.environ.setdefault("POC_BACKEND", "sqlite")
os.environ.pop("GOOGLE_API_KEY", None)  # tests never call Gemini


@pytest.fixture(scope="session", autouse=True)
def built_artifacts():
    from returns_poc.indexing import build
    from returns_poc.policy.build_pdfs import build as build_pdfs
    from returns_poc.store import build_sqlite
    from returns_poc.synthetic.generate_data import generate

    if not (SYNTHETIC_DIR / "returns.csv").exists():
        generate()
    if not all((POLICY_DIR / f).exists() for f in ["SC_Returns_Policy_v1.pdf", "SC_Returns_Policy_v2.pdf",
                                                  "SC_Damaged_Item_SOP.pdf", "SC_Refund_Processing_SOP.pdf"]):
        build_pdfs()
    if not LOCAL_DB_PATH.exists():
        build_sqlite()
    if not (INDEX_DIR / "manifest.json").exists():
        build()
    yield
