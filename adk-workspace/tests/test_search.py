"""Keyword, vector and hybrid search over support cases, plus the ranking primitives."""
import pytest

from returns_poc.embeddings import LocalHashEmbedder, cosine
from returns_poc.retrieval import build_case_index
from returns_poc.search import BM25, Ranked, rrf_fuse, vector_rank
from returns_poc.store import Store


@pytest.fixture(scope="module")
def idx():
    return build_case_index(Store("sqlite"))


def test_bm25_ranks_documents_with_query_terms_first():
    bm = BM25({"a": "zipper broke after first wear", "b": "shoe sole separated", "c": "zipper zipper zipper"})
    ranked = bm.rank("zipper broke", 5)
    keys = [r.key for r in ranked]
    assert "b" not in keys
    assert keys[0] in {"a", "c"}
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))


def test_rrf_fusion_scores_and_dedupes():
    lex = [Ranked("x", 9, 1), Ranked("y", 5, 2)]
    vec = [Ranked("y", 0.9, 1), Ranked("z", 0.5, 2)]
    fused = rrf_fuse({"keyword": lex, "vector": vec}, 10)
    keys = [f["key"] for f in fused]
    assert len(keys) == len(set(keys)) == 3          # duplicates removed
    assert keys[0] == "y"                             # found by both methods wins
    assert fused[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61, rel=1e-4)
    assert fused[0]["ranks"] == {"keyword": 2, "vector": 1}


def test_vector_rank_and_cosine():
    emb = LocalHashEmbedder()
    v = emb.embed(["zipper broke after first wear", "shoe sole separated"])
    assert cosine(v[0], v[0]) == pytest.approx(1.0, rel=1e-6)
    assert cosine(v[0], v[1]) < cosine(v[0], v[0])
    ranked = vector_rank(v[0], {"a": v[0], "b": v[1]}, 2)
    assert ranked[0].key == "a" and ranked[0].rank == 1


def test_keyword_zipper_query_returns_zipper_cases(idx):
    res = idx.lexical("zipper broke after first wear", 5)
    assert res and [r["rank"] for r in res] == list(range(1, len(res) + 1))
    narr = {m["case_id"]: m["narrative"].lower()
            for m in Store("sqlite").query("SELECT case_id, narrative FROM {support_cases}")}
    assert all("zip" in narr[r["case_id"]] for r in res)


def test_damaged_keyword_finds_damage_cases_but_not_the_broken_zipper_case(idx):
    res = [r["case_id"] for r in idx.lexical("damaged", 10)]
    assert "CASE-0004" in res and "CASE-0002" in res
    assert "CASE-0001" not in res  # the broken-zipper complaint never uses the word "damaged"


def test_vector_and_hybrid_return_ranked_lists_for_same_query(idx):
    q = "zipper broke after first wear"
    vec = idx.vector(q, 5)
    assert [r["rank"] for r in vec] == [1, 2, 3, 4, 5]
    hyb = idx.hybrid(q, 5)["fused"]
    ids = [r["case_id"] for r in hyb]
    assert len(ids) == len(set(ids)) == 5
    assert all(set(r["ranks"]) == {"keyword", "vector"} for r in hyb)
    assert [r["rank"] for r in hyb] == [1, 2, 3, 4, 5]


def test_case_narratives_are_marked_as_data(idx):
    hit = idx.by_id("CASE-0003")
    assert hit["narrative_is_data"] is True
    assert "SYSTEM OVERRIDE" in hit["narrative_excerpt"]
