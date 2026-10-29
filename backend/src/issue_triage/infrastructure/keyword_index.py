"""BM25 keyword search over issues, in memory (rank-bm25).

Error messages, flag names and package specifiers are where keyword search beats
embeddings, so the tokenizer keeps dashes, dots and underscores inside tokens.
"""

import re
from collections.abc import Sequence
from datetime import datetime

from rank_bm25 import BM25Okapi

from issue_triage.domain.issues import Issue

TOKEN = re.compile(r"[a-z0-9][a-z0-9_.\-]*[a-z0-9]|[a-z0-9]")
STOPWORDS = frozenset(
    (
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "when",
        "with",
        "you",
        "uv",
    )
)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


class Bm25IssueIndex:
    def __init__(self, issues: Sequence[Issue], *, body_chars: int = 4000) -> None:
        self._numbers = [issue.number for issue in issues]
        self._created = [issue.created_at for issue in issues]
        corpus = [tokenize(f"{i.title} {i.title} {i.body[:body_chars]}") for i in issues]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(
        self, query: str, *, before: datetime, exclude: int | None = None, k: int = 10
    ) -> list[tuple[int, float]]:
        tokens = tokenize(query)
        if self._bm25 is None or not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        candidates = [
            (number, float(score))
            for number, created, score in zip(self._numbers, self._created, scores, strict=True)
            if created < before and number != exclude and score > 0
        ]
        candidates.sort(key=lambda item: (-item[1], item[0]))
        return candidates[:k]
