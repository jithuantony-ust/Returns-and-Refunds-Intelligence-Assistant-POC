"""Build the retrieval index: policy chunks (from PDFs) and case embeddings (from BigQuery/SQLite).

Run from adk-workspace/:  python -m returns_poc.indexing
Outputs (index/):
  policy_chunks.jsonl     one row per (document, section, page) chunk, with embedding
  case_embeddings.jsonl   one row per support case, with embedding
  manifest.json           embedding model version, counts, and PDF hashes
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader

from returns_poc.config import INDEX_DIR, POLICY_DIR
from returns_poc.embeddings import get_embedder

HEADING_RE = re.compile(r"^(\d+)\.\s+([A-Z][^.]{2,60})$")
FOOTER_PREFIX = "StyleCart (fictional) |"
HEADER_KEYS = {
    "document": re.compile(r"^Document:\s*(\S+)"),
    "version": re.compile(r"^Version:\s*(\S+)"),
    "effective_from": re.compile(r"^Effective from:\s*(\S+)"),
    "effective_to": re.compile(r"^Effective to:\s*(\S+)"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_policy_pdf(path: Path) -> list[dict]:
    """Extract chunks from one policy PDF. Each chunk is one section's text on one page."""
    reader = PdfReader(str(path))
    meta: dict[str, str] = {}
    chunks: list[dict] = []
    section_no, section_title = 0, "Front matter"
    for page_no, page in enumerate(reader.pages, start=1):
        lines = [ln.strip() for ln in (page.extract_text() or "").splitlines()]
        buf: list[str] = []
        cur_key = (section_no, page_no)

        def flush():
            if buf:
                chunks.append({"section_no": cur_key[0], "section": section_title, "page": cur_key[1],
                               "content": " ".join(buf).strip()})
            buf.clear()

        for ln in lines:
            if not ln or ln.startswith(FOOTER_PREFIX) or re.fullmatch(r"Page \d+", ln):
                continue
            for key, rx in HEADER_KEYS.items():
                m = rx.match(ln)
                if m and key not in meta:
                    meta[key] = m.group(1)
            h = HEADING_RE.match(ln)
            if h:
                flush()
                section_no, section_title = int(h.group(1)), ln
                cur_key = (section_no, page_no)
                buf.append(ln)
                continue
            buf.append(ln)
        flush()

    stem = path.stem
    to_value = meta.get("effective_to")
    out = []
    for c in chunks:
        out.append({
            "chunk_id": f"{stem}:s{c['section_no']}:p{c['page']}",
            "doc_id": stem,
            "source_file": path.name,
            "title": meta.get("document", stem),
            "policy_version": meta.get("version"),
            "effective_from": meta.get("effective_from"),
            "effective_to": None if to_value in (None, "No") else to_value,
            "section": c["section"],
            "page": c["page"],
            "content": c["content"],
        })
    return out


def build(policy_dir: Path = POLICY_DIR, index_dir: Path = INDEX_DIR) -> dict:
    from returns_poc.store import Store

    index_dir.mkdir(parents=True, exist_ok=True)
    embedder = get_embedder()

    pdfs = sorted(policy_dir.glob("*.pdf"))
    chunks: list[dict] = []
    hashes = {}
    for pdf in pdfs:
        hashes[pdf.name] = _sha256(pdf)
        chunks.extend(parse_policy_pdf(pdf))
    vecs = embedder.embed([c["content"] for c in chunks])
    with (index_dir / "policy_chunks.jsonl").open("w", encoding="utf-8") as fh:
        for c, v in zip(chunks, vecs):
            fh.write(json.dumps({**c, "embedding": v, "embedding_model": embedder.model_version}) + "\n")

    store = Store()
    cases = store.query("SELECT case_id, return_id, narrative, resolution, case_status, created_at FROM {support_cases} ORDER BY case_id")
    texts = [f"{c['narrative']} Resolution: {c['resolution']}" for c in cases]
    cvecs = embedder.embed(texts)
    with (index_dir / "case_embeddings.jsonl").open("w", encoding="utf-8") as fh:
        for c, t, v in zip(cases, texts, cvecs):
            fh.write(json.dumps({"case_id": c["case_id"], "searchable_text": t, "embedding": v,
                                 "model_version": embedder.model_version}) + "\n")

    manifest = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "embedding_model_version": embedder.model_version,
        "prompt_version": "root-v1|policy-v1|search-v1",
        "policy_chunks": len(chunks),
        "case_embeddings": len(cases),
        "source_sha256": hashes,
        "backend": store.backend,
    }
    (index_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
