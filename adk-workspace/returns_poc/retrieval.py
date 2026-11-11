"""Query-time retrieval over the index built by returns_poc.indexing.

PolicyIndex: retrieve policy chunks by keyword, vector, or hybrid (RRF), with optional version filter.
CaseIndex:   find support cases by ID, keyword (BM25), vector, or hybrid (RRF). Duplicates are removed.

Narratives from support cases are returned as data. They are never treated as instructions.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from functools import lru_cache

from returns_poc.config import INDEX_DIR, MAX_TOP_K
from returns_poc.embeddings import get_embedder
from returns_poc.search import BM25, Ranked, rrf_fuse, vector_rank

_lock = threading.Lock()


def _read_jsonl(path):
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing. Run: python -m returns_poc.indexing")
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _check_manifest(model_version: str):
    manifest_path = INDEX_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("index/manifest.json is missing. Run: python -m returns_poc.indexing")
    built_with = json.loads(manifest_path.read_text(encoding="utf-8"))["embedding_model_version"]
    if built_with != model_version:
        raise RuntimeError(f"Index was built with {built_with}, but the query embedder is {model_version}. Rebuild the index.")


class PolicyIndex:
    def __init__(self):
        self.embedder = get_embedder()
        rows = _read_jsonl(INDEX_DIR / "policy_chunks.jsonl")
        _check_manifest(self.embedder.model_version)
        self.rows = {r["chunk_id"]: r for r in rows}
        self.vecs = {r["chunk_id"]: r["embedding"] for r in rows}
        self.bm25 = BM25({k: r["content"] for k, r in self.rows.items()})

    def versions(self) -> list[dict]:
        seen = {}
        for r in self.rows.values():
            seen.setdefault(r["doc_id"], {"source_file": r["source_file"], "policy_version": r["policy_version"],
                                          "effective_from": r["effective_from"], "effective_to": r["effective_to"]})
        return list(seen.values())

    def retrieve(self, query: str, top_k: int = 5, policy_doc: str | None = None) -> list[dict]:
        top_k = max(1, min(top_k, MAX_TOP_K))
        allowed = {k for k, r in self.rows.items() if policy_doc is None or r["source_file"] == policy_doc}
        keyword = [r for r in self.bm25.rank(query, top_k=len(self.rows)) if r.key in allowed]
        keyword = [Ranked(r.key, r.score, i + 1) for i, r in enumerate(keyword)]
        qv = self.embedder.embed([query])[0]
        vec = vector_rank(qv, {k: v for k, v in self.vecs.items() if k in allowed}, top_k=len(allowed))
        fused = rrf_fuse({"keyword": keyword, "vector": vec}, top_k)
        out = []
        for f in fused:
            r = self.rows[f["key"]]
            out.append({
                "rank": f["rank"], "rrf_score": f["rrf_score"], "ranks": f["ranks"],
                "chunk_id": r["chunk_id"], "source_file": r["source_file"], "title": r["title"],
                "policy_version": r["policy_version"], "effective_from": r["effective_from"],
                "effective_to": r["effective_to"], "section": r["section"], "page": r["page"],
                "excerpt": r["content"][:400],
            })
        return out

    def chunks_for_doc(self, source_file: str) -> list[dict]:
        return sorted((r for r in self.rows.values() if r["source_file"] == source_file),
                      key=lambda r: (r["page"], r["chunk_id"]))


class CaseIndex:
    def __init__(self, store):
        self.store = store
        self.embedder = get_embedder()
        emb = _read_jsonl(INDEX_DIR / "case_embeddings.jsonl")
        _check_manifest(self.embedder.model_version)
        self.vecs = {r["case_id"]: r["embedding"] for r in emb}
        self.text = {r["case_id"]: r["searchable_text"] for r in emb}
        self.bm25 = BM25(self.text)
        self._meta: dict[str, dict] | None = None

    def _metadata(self) -> dict[str, dict]:
        if self._meta is None:
            rows = self.store.query("SELECT case_id, return_id, case_status, created_at, resolution, narrative FROM {support_cases}")
            self._meta = {r["case_id"]: r for r in rows}
        return self._meta

    def _enrich(self, key: str, rank: int, score: float, ranks: dict | None = None) -> dict:
        m = self._metadata().get(key, {})
        out = {"case_id": key, "rank": rank, "score": score,
               "return_id": m.get("return_id"), "case_status": m.get("case_status"),
               "created_at": m.get("created_at"),
               "narrative_excerpt": (m.get("narrative") or "")[:300],
               "narrative_is_data": True}
        if ranks is not None:
            out["ranks"] = ranks
        return out

    def by_id(self, case_id: str) -> dict | None:
        if case_id not in self._metadata():
            return None
        return self._enrich(case_id, 1, 1.0)

    def lexical(self, query: str, top_k: int = 5) -> list[dict]:
        top_k = max(1, min(top_k, MAX_TOP_K))
        return [self._enrich(r.key, r.rank, r.score) for r in self.bm25.rank(query, top_k)]

    def vector(self, query: str, top_k: int = 5) -> list[dict]:
        top_k = max(1, min(top_k, MAX_TOP_K))
        qv = self.embedder.embed([query])[0]
        return [self._enrich(r.key, r.rank, r.score) for r in vector_rank(qv, self.vecs, top_k)]

    def hybrid(self, query: str, top_k: int = 5, pool: int = 20) -> dict:
        """Fuse keyword and vector lists with RRF. Returns the fused list and each method's list."""
        top_k = max(1, min(top_k, MAX_TOP_K))
        lex = self.bm25.rank(query, pool)
        qv = self.embedder.embed([query])[0]
        vec = vector_rank(qv, self.vecs, pool)
        fused = rrf_fuse({"keyword": lex, "vector": vec}, top_k)
        rows = []
        for f in fused:
            row = self._enrich(f["key"], f["rank"], f["rrf_score"], f["ranks"])
            row["rrf_score"] = row.pop("score")
            rows.append(row)
        return {
            "fused": rows,
            "dedup_note": "Each case appears once. Cases found by both methods get the sum of their RRF scores.",
        }


@lru_cache(maxsize=1)
def policy_index() -> PolicyIndex:
    with _lock:
        return PolicyIndex()


def build_case_index(store) -> CaseIndex:
    with _lock:
        return CaseIndex(store)


def index_manifest() -> dict:
    p = INDEX_DIR / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
