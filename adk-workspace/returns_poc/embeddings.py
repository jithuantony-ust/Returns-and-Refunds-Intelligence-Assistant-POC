"""Embedding providers.

* local (default): deterministic hashed uni/bi-gram vectors. Works offline and in tests.
  It matches shared words and word stems, but NOT synonyms. It is a baseline, not a semantic model.
* gemini: Gemini text-embedding model through google-genai. Set EMBEDDING_PROVIDER=gemini and
  configure credentials (GOOGLE_API_KEY, or Vertex AI settings). Not run in this environment.

Every stored vector records model_version, so the index can be checked for mixed models.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

from returns_poc.config import EMBEDDING_MODEL

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "of", "to", "in", "on", "and", "or", "is", "was", "it", "for", "with", "after", "at", "by", "be"}


def tokens(text: str) -> list[str]:
    """Lowercase tokens with light suffix stripping. Used by BM25 and the local embedder."""
    out = []
    for t in _TOKEN_RE.findall(text.lower()):
        if t in _STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(t) > len(suf) + 2 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


class Embedder(Protocol):
    model_version: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalHashEmbedder:
    dim = 512

    def __init__(self):
        self.model_version = f"local-hash-{self.dim}-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        toks = tokens(text)
        feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
        for f in feats:
            h = int.from_bytes(hashlib.md5(f.encode("utf-8")).digest()[:4], "big")
            vec[h % self.dim] += 1.0 if (h >> 31) & 1 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class GeminiEmbedder:
    def __init__(self, model: str = EMBEDDING_MODEL):
        from google import genai  # imported lazily

        self._client = genai.Client()
        self._model = model
        self.model_version = f"gemini:{model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):  # batch to keep requests small
            resp = self._client.models.embed_content(model=self._model, contents=texts[i:i + 100])
            out.extend(list(e.values) for e in resp.embeddings)
        return out


def get_embedder() -> Embedder:
    provider = os.getenv("EMBEDDING_PROVIDER", "local").lower()
    if provider == "gemini":
        return GeminiEmbedder()
    return LocalHashEmbedder()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
