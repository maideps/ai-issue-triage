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


DEFAULT_HANDLERS: dict[str, FakeHandler] = {"triage-keywords": triage_keywords}
