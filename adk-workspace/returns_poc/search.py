"""Ranking primitives shared by case search and policy retrieval.

keyword  -> BM25 implemented in Python (k1=1.5, b=0.75), over tokens from embeddings.tokens().
vector   -> cosine similarity between query and document embeddings.
hybrid   -> Reciprocal Rank Fusion (RRF): score(d) = sum_i 1 / (k + rank_i(d)), k = 60.
            Duplicates are removed by document key before fusion.

BigQuery SEARCH() is not used for ranking. It does not provide BM25 scores, so this module
computes the lexical ranking itself.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from returns_poc.embeddings import cosine, tokens

RRF_K = 60


@dataclass
class Ranked:
    key: str
    score: float
    rank: int


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = {k: tokens(v) for k, v in docs.items()}
        self.n = len(self.docs)
        self.avgdl = (sum(len(t) for t in self.docs.values()) / self.n) if self.n else 0.0
        df: Counter[str] = Counter()
        for toks in self.docs.values():
            df.update(set(toks))
        self.idf = {t: math.log((self.n - c + 0.5) / (c + 0.5) + 1.0) for t, c in df.items()}
        self.tf = {k: Counter(v) for k, v in self.docs.items()}

    def rank(self, query: str, top_k: int) -> list[Ranked]:
        q = set(tokens(query))
        scores = {}
        for key, tf in self.tf.items():
            dl = len(self.docs[key])
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / denom
            if s > 0:
                scores[key] = s
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        return [Ranked(k, round(s, 4), i + 1) for i, (k, s) in enumerate(ordered)]


def vector_rank(query_vec: list[float], doc_vecs: dict[str, list[float]], top_k: int) -> list[Ranked]:
    scored = [(k, cosine(query_vec, v)) for k, v in doc_vecs.items()]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return [Ranked(k, round(s, 4), i + 1) for i, (k, s) in enumerate(scored[:top_k])]


def rrf_fuse(lists: dict[str, list[Ranked]], top_k: int) -> list[dict]:
    """Fuse ranked lists. Returns dicts with key, rrf score, and each list's rank (None if absent)."""
    acc: dict[str, dict] = {}
    for name, ranked in lists.items():
        for r in ranked:
            entry = acc.setdefault(r.key, {"key": r.key, "rrf_score": 0.0, "ranks": {n: None for n in lists}})
            entry["rrf_score"] += 1.0 / (RRF_K + r.rank)
            entry["ranks"][name] = r.rank
    fused = sorted(acc.values(), key=lambda e: (-e["rrf_score"], e["key"]))[:top_k]
    for i, e in enumerate(fused, 1):
        e["rrf_score"] = round(e["rrf_score"], 6)
        e["rank"] = i
    return fused
