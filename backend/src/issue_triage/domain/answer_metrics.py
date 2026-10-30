"""Answer-quality metrics that run without an LLM (D-010).

They mirror RAGAS's definitions with deterministic stand-ins: faithfulness checks that
each reply sentence is supported by the cited text; relevancy and reference similarity
compare embeddings (the caller passes cosine similarities in).
"""

import re
from collections.abc import Sequence

SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"[a-z0-9][a-z0-9_.\-]*")
FILLER = frozenset(
    (
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "see",
        "so",
        "that",
        "the",
        "this",
        "to",
        "use",
        "was",
        "we",
        "when",
        "which",
        "with",
        "you",
        "your",
    )
)


def content_words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in FILLER and len(w) > 1}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text) if len(content_words(s)) >= 2]


def lexical_faithfulness(reply: str, cited_texts: Sequence[str], threshold: float = 0.6) -> float:
    """Share of reply sentences whose content words mostly appear in the cited text."""
    claims = sentences(reply)
    if not claims:
        return 0.0
    support = content_words(" ".join(cited_texts))
    supported = 0
    for claim in claims:
        words = content_words(claim)
        if words and len(words & support) / len(words) >= threshold:
            supported += 1
    return supported / len(claims)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0
