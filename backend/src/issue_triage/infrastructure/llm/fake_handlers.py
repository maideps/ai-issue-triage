"""Deterministic behaviours for the fake provider, one per pipeline step.

``triage_keywords`` stands in for a model in the triage step: a transparent keyword
heuristic. It gives the eval runner a real, reproducible baseline to beat once a real
model is configured, and it costs nothing (D-007).
"""

import json
import re
from collections.abc import Sequence
from typing import Any

from issue_triage.domain.llm import Message
from issue_triage.infrastructure.llm.fake import FakeHandler

ISSUE_BLOCK = re.compile(r"<issue>\s*(.*?)\s*</issue>", re.DOTALL)
TITLE = re.compile(r"^Title:\s*(.*)$", re.MULTILINE)

KEYWORDS: dict[str, tuple[str, ...]] = {
    "bug": (
        "error",
        "fail",
        "panic",
        "crash",
        "broken",
        "regression",
        "bug",
        "traceback",
        "doesn't work",
        "does not work",
        "not working",
        "incorrect",
        "wrong",
        "unexpected",
        "exception",
        "cannot",
        "can't",
    ),
    "question": (
        "how do i",
        "how to",
        "how can",
        "is it possible",
        "is there a way",
        "what is",
        "why does",
        "why is",
        "question",
        "help",
        "confused",
    ),
    "enhancement": (
        "support",
        "add ",
        "allow",
        "option",
        "feature",
        "request",
        "would be nice",
        "proposal",
        "enable",
        "consider",
        "provide",
    ),
    "other": ("docs", "documentation", "typo", "slow", "performance", "faster", "security"),
}
TIE_ORDER = ("bug", "question", "enhancement", "other")


def _issue_text(messages: Sequence[Message]) -> tuple[str, str]:
    user = messages[-1].content if messages else ""
    title_match = TITLE.search(user)
    body_match = ISSUE_BLOCK.search(user)
    return (
        (title_match.group(1) if title_match else "").lower(),
        (body_match.group(1) if body_match else user).lower(),
    )


def triage_keywords(messages: Sequence[Message], schema: dict[str, Any] | None) -> str:
    title, body = _issue_text(messages)
    scores = {
        label: sum(2 * title.count(word) + body.count(word) for word in words)
        for label, words in KEYWORDS.items()
    }
    if title.rstrip().endswith("?"):
        scores["question"] += 2
    ranked = sorted(TIE_ORDER, key=lambda label: (-scores[label], TIE_ORDER.index(label)))
    best, runner_up = ranked[0], ranked[1]
    if scores[best] == 0:
        return json.dumps({"label": "bug", "confidence": 0.3, "rationale": "no keyword matched"})
    margin = scores[best] - scores[runner_up]
    return json.dumps(
        {
            "label": best,
            "confidence": round(min(0.9, 0.5 + 0.1 * margin), 2),
            "rationale": f"keyword score {scores[best]} vs {scores[runner_up]}",
        }
    )


ERROR_LINE = re.compile(
    r"^.*\b(?:error|failed|failure|panic|panicked|exception|caused by|traceback)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
INLINE_CODE = re.compile(r"`([^`\n]{2,80})`")


def query_keyphrases(messages: Sequence[Message], schema: dict[str, Any] | None) -> str:
    """Stand-in for query rewriting: the title, the first error line, the inline code."""
    user = messages[-1].content if messages else ""
    title_match = TITLE.search(user)
    body_match = ISSUE_BLOCK.search(user)
    body = body_match.group(1) if body_match else user
    error_line = ERROR_LINE.search(body)
    candidates = [
        title_match.group(1).strip() if title_match else "",
        error_line.group(0).strip()[:200] if error_line else "",
        " ".join(dict.fromkeys(INLINE_CODE.findall(body)))[:150],
    ]
    queries = [q for q in dict.fromkeys(candidates) if q]
    return json.dumps({"queries": queries[:3]})


CONTEXT_BLOCK = re.compile(r"^\[([^\]\s]+#\d+)\] (.+?)(?=^\[[^\]\s]+#\d+\] |\Z)", re.M | re.S)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def answer_extractive(messages: Sequence[Message], schema: dict[str, Any] | None) -> str:
    """Stand-in for answer drafting: quote the top excerpt and cite it."""
    user = messages[-1].content if messages else ""
    blocks = CONTEXT_BLOCK.findall(user)
    if not blocks:
        return json.dumps(
            {
                "reply": "The documentation excerpts do not cover this.",
                "citations": [],
                "confidence": 0.1,
            }
        )
    ref, text = blocks[0]
    lines = [line for line in text.strip().splitlines()[1:] if line.strip()]  # drop breadcrumb
    excerpt = " ".join(SENTENCE_END.split(" ".join(lines))[:2]).strip()
    reply = f"This is covered in the documentation: {excerpt} [{ref}]"
    return json.dumps({"reply": reply, "citations": [ref], "confidence": 0.55})


REFERENCE = re.compile(r"<reference>\s*(.*?)\s*</reference>", re.S)
DRAFT = re.compile(r"<draft>\s*(.*?)\s*</draft>", re.S)
JUDGE_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-]{2,}")


def judge_overlap(messages: Sequence[Message], schema: dict[str, Any] | None) -> str:
    """Stand-in for the LLM judge: word overlap with the maintainer reply, scaled to 1-5.

    It is not a judgment of substance; real scores need a real model (D-010).
    """
    user = messages[-1].content if messages else ""
    reference = REFERENCE.search(user)
    draft = DRAFT.search(user)
    ref_words = set(JUDGE_WORD.findall((reference.group(1) if reference else "").lower()))
    draft_words = set(JUDGE_WORD.findall((draft.group(1) if draft else "").lower()))
    overlap = len(ref_words & draft_words) / (len(ref_words) or 1)
    score = 1 + min(4, int(overlap * 8))
    return json.dumps({"score": score, "reason": f"word overlap {overlap:.2f} (fake judge)"})


DEFAULT_HANDLERS: dict[str, FakeHandler] = {
    "triage-keywords": triage_keywords,
    "query-keyphrases": query_keyphrases,
    "answer-extractive": answer_extractive,
    "judge-overlap": judge_overlap,
}
