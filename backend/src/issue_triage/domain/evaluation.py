"""Evaluation cases derived from what maintainers actually did (D-001, D-004).

Every case carries ``as_of``, the issue's creation time. The model only ever sees the
issue's title and body; anything newer than ``as_of`` (comments, other issues, labels
added later) is either the expected answer or must stay out of reach of retrieval.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from issue_triage.domain.docs import page_key
from issue_triage.domain.issues import Issue

TypeClass = Literal["bug", "question", "enhancement", "other"]
Split = Literal["train", "test"]

TYPE_CLASSES: tuple[TypeClass, ...] = ("bug", "question", "enhancement", "other")
LABEL_TO_CLASS: dict[str, TypeClass] = {
    "bug": "bug",
    "question": "question",
    "enhancement": "enhancement",
    "wish": "enhancement",
    "documentation": "other",
    "performance": "other",
    "compatibility": "other",
    "security": "other",
}
NEEDS_INFO_LABELS = frozenset({"needs-mre", "bot:needs-mre-comment"})
DUPLICATE_COMMENT = re.compile(
    r"(?:duplicate of|dupe of|dup of|closing in favor of|tracked in)\s+"
    r"(?:#|https://github\.com/([\w.-]+/[\w.-]+)/issues/)(\d+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CaseBase:
    repository: str
    number: int
    as_of: datetime
    title: str
    body: str
    split: Split


@dataclass(frozen=True)
class TypeCase(CaseBase):
    label: TypeClass


@dataclass(frozen=True)
class DuplicateCase(CaseBase):
    original_number: int
    link_source: Literal["duplicateOf", "comment"]


@dataclass(frozen=True)
class AnswerCase(CaseBase):
    reference_answer: str


@dataclass(frozen=True)
class NeedsInfoCase(CaseBase):
    needs_info: bool


def type_class(issue: Issue) -> TypeClass | None:
    """The issue's single type class, or None when it has none or conflicting ones."""
    classes = {LABEL_TO_CLASS[label] for label in issue.labels if label in LABEL_TO_CLASS}
    return classes.pop() if len(classes) == 1 else None


def duplicate_target(issue: Issue) -> tuple[str, int, Literal["duplicateOf", "comment"]] | None:
    """(repository, number, source) of the original this issue duplicates, if recorded."""
    if issue.duplicate_of is not None:
        return issue.duplicate_of.repository, issue.duplicate_of.number, "duplicateOf"
    if issue.state_reason != "DUPLICATE" and "duplicate" not in issue.labels:
        return None
    for comment in reversed(issue.comments):
        match = DUPLICATE_COMMENT.search(comment.body)
        if match:
            return match.group(1) or issue.repository, int(match.group(2)), "comment"
    return None


def first_maintainer_reply(issue: Issue) -> str | None:
    for comment in issue.comments:
        if comment.by_maintainer and comment.author != issue.author and comment.body.strip():
            return comment.body
    return None


def split_boundary(issues: Sequence[Issue], test_fraction: float = 0.2) -> datetime:
    """Creation time from which the newest ``test_fraction`` of issues form the test split."""
    if not issues:
        raise ValueError("no issues to split")
    ordered = sorted(issue.created_at for issue in issues)
    return ordered[min(len(ordered) - 1, int(len(ordered) * (1 - test_fraction)))]


def _base(issue: Issue, boundary: datetime) -> tuple[str, int, datetime, str, str, Split]:
    """CaseBase fields in declaration order: what the model may see, plus the split."""
    split: Split = "test" if issue.created_at >= boundary else "train"
    return issue.repository, issue.number, issue.created_at, issue.title, issue.body, split


def build_type_cases(issues: Iterable[Issue], boundary: datetime) -> list[TypeCase]:
    cases: list[TypeCase] = []
    for issue in issues:
        label = type_class(issue)
        if label is not None:
            cases.append(TypeCase(*_base(issue, boundary), label=label))
    return cases


def build_duplicate_cases(issues: Sequence[Issue], boundary: datetime) -> list[DuplicateCase]:
    """Pairs whose original lives in the same repository and existed before the duplicate."""
    by_number = {(issue.repository, issue.number): issue for issue in issues}
    cases: list[DuplicateCase] = []
    for issue in issues:
        target = duplicate_target(issue)
        if target is None:
            continue
        repository, number, source = target
        original = by_number.get((repository, number))
        if original is None or original.number == issue.number:
            continue
        if original.created_at >= issue.created_at:
            continue  # merged into a newer tracking issue: unknowable at as_of
        cases.append(
            DuplicateCase(*_base(issue, boundary), original_number=number, link_source=source)
        )
    return cases


def build_answer_cases(issues: Iterable[Issue], boundary: datetime) -> list[AnswerCase]:
    cases: list[AnswerCase] = []
    for issue in issues:
        if type_class(issue) != "question":
            continue
        reply = first_maintainer_reply(issue)
        if reply is not None:
            cases.append(AnswerCase(*_base(issue, boundary), reference_answer=reply))
    return cases


def build_needs_info_cases(issues: Iterable[Issue], boundary: datetime) -> list[NeedsInfoCase]:
    """Positives carry a needs-info label; negatives are typed issues without one."""
    cases: list[NeedsInfoCase] = []
    for issue in issues:
        needs_info = bool(NEEDS_INFO_LABELS.intersection(issue.labels))
        if needs_info or type_class(issue) is not None:
            cases.append(NeedsInfoCase(*_base(issue, boundary), needs_info=needs_info))
    return cases


@dataclass(frozen=True)
class DocLinkCase(CaseBase):
    """A question whose maintainer answer linked documentation pages (feat-008)."""

    pages: tuple[str, ...]


def docs_link_pages(text: str, base_url: str) -> tuple[str, ...]:
    """Page keys of every link under ``base_url`` in ``text``, in order, deduplicated."""
    pattern = re.compile(re.escape(base_url.rstrip("/")) + r"/([\w\-/.]*)")
    pages: list[str] = []
    for path in pattern.findall(text):
        key = page_key(path)
        if key and key not in pages:
            pages.append(key)
    return tuple(pages)


def build_doc_link_cases(answers: Iterable[AnswerCase], base_url: str) -> list[DocLinkCase]:
    cases: list[DocLinkCase] = []
    for answer in answers:
        pages = docs_link_pages(answer.reference_answer, base_url)
        if pages:
            cases.append(
                DocLinkCase(
                    answer.repository,
                    answer.number,
                    answer.as_of,
                    answer.title,
                    answer.body,
                    answer.split,
                    pages,
                )
            )
    return cases
