"""Issue text is untrusted input: redact what must not reach a model or a reply, and flag
attempts to steer the model. Pure functions, no external dependencies.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

# Order matters: specific token formats before the generic assignment pattern.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
    ),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url_credentials", re.compile(r"(?<=://)[^\s/:@]+:[^\s/@]+(?=@)")),
    # NAME=value / NAME: value for secret-looking names (UV_INDEX_PASSWORD, api_key, ...).
    # The groups before the last are kept; only the value is replaced.
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b((?:[a-z0-9]+_)*(?:password|passwd|token|secret|api_key|apikey))"
            r"(\s*[=:]\s*['\"]?)(?!\[REDACTED)([^\s'\"&]{6,})"
        ),
    ),
)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # "previous/prior/above" is required: "ignore all custom config rules" is a real
        # uv issue title, not an attack.
        r"\b(?:ignore|disregard|forget)\b.{0,30}\b(?:previous|prior|above|earlier)\b.{0,20}"
        r"\b(?:instructions?|prompts?|rules?)\b",
        r"\byou are now\b",
        r"\b(?:reveal|print|show|repeat)\b.{0,20}\b(?:system prompt|your instructions)\b",
        r"\bnew instructions?\s*:",
        r"<\|im_start\|>|<\|im_end\|>|\[INST\]|<</?SYS>>",
        r"\bact as (?:an?|the)\b.{0,30}\b(?:assistant|ai|model|maintainer)\b",
        r"</?issue>",
    )
)

ISSUE_OPEN, ISSUE_CLOSE = "<issue>", "</issue>"


@dataclass(frozen=True)
class Redaction:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def redacted(self) -> bool:
        return bool(self.counts)


def _replacer(marker: str, *, keep_groups: bool) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        kept = match.groups()[:-1] if keep_groups else ()
        return "".join(group or "" for group in kept) + marker

    return replace


def redact(text: str, *, emails: bool = True) -> Redaction:
    """Replace secrets (and emails) with ``[REDACTED:<kind>]``."""
    counts: Counter[str] = Counter()
    for kind, pattern in SECRET_PATTERNS:
        text, n = pattern.subn(
            _replacer(f"[REDACTED:{kind}]", keep_groups=pattern.groups > 0), text
        )
        counts[kind] += n
    if emails:
        text, n = EMAIL.subn("[REDACTED:email]", text)
        counts["email"] += n
    return Redaction(text, {kind: n for kind, n in counts.items() if n})


def injection_signals(text: str) -> list[str]:
    """The suspicious fragments found, verbatim (empty when the text looks benign)."""
    return [match.group(0) for pattern in INJECTION_PATTERNS for match in pattern.finditer(text)]


def fence_untrusted(text: str) -> str:
    """Wrap issue text in delimiters the prompt treats as data; neutralise fake delimiters."""
    neutral = text.replace(ISSUE_OPEN, "<issue_>").replace(ISSUE_CLOSE, "</issue_>")
    return f"{ISSUE_OPEN}\n{neutral}\n{ISSUE_CLOSE}"
