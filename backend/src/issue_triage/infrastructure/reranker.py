"""Rerankers: score (query, candidate) pairs jointly instead of comparing embeddings."""

import re
from collections.abc import Sequence
from typing import Any

TOKEN = re.compile(r"[a-z0-9][a-z0-9_.\-]*")


class CrossEncoderReranker:
    """A local cross-encoder (D-007); slower than embeddings, so it only sees the top few."""

    def __init__(self, model_name: str, max_chars: int = 1500) -> None:
        self.model_name = model_name
        self._max_chars = max_chars
        self._model: Any = None

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        pairs = [(query[: self._max_chars], text[: self._max_chars]) for text in texts]
        return [float(s) for s in self._model.predict(pairs, show_progress_bar=False)]


class OverlapReranker:
    """Token-overlap scores: deterministic and offline, for tests."""

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        terms = set(TOKEN.findall(query.lower()))
        scores = []
        for text in texts:
            words = set(TOKEN.findall(text.lower()))
            scores.append(len(terms & words) / (len(terms | words) or 1))
        return scores
