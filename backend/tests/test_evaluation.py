from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from issue_triage.domain.evaluation import (
    AnswerCase,
    CaseBase,
    DuplicateCase,
    NeedsInfoCase,
    TypeCase,
    build_answer_cases,
    build_duplicate_cases,
    build_needs_info_cases,
    build_type_cases,
    duplicate_target,
    split_boundary,
    type_class,
)
from issue_triage.domain.issues import Comment, DuplicateLink, Issue
from issue_triage.infrastructure.dataset_files import read_cases, write_jsonl
from issue_triage.services.datasets import Datasets, select_golden

REPO = "acme/widgets"
T0 = datetime(2025, 1, 1, tzinfo=UTC)


def make(
    number: int,
    *,
    day: int | None = None,
    labels: tuple[str, ...] = (),
    state_reason: str | None = "COMPLETED",
    duplicate_of: int | None = None,
    comments: tuple[Comment, ...] = (),
) -> Issue:
    return Issue(
        repository=REPO,
        number=number,
        title=f"Issue {number}",
        body=f"body {number}",
        author="user-author",
        author_association="NONE",
        state="CLOSED",
        state_reason=state_reason,
        created_at=T0 + timedelta(days=day if day is not None else number),
        closed_at=None,
        labels=labels,
        duplicate_of=DuplicateLink(REPO, duplicate_of) if duplicate_of else None,
        comments=comments,
        comment_count=len(comments),
    )


def reply(body: str, association: str = "MEMBER", author: str = "user-maint") -> Comment:
    return Comment(author, association, body, T0 + timedelta(days=400))


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (("bug",), "bug"),
        (("question", "area:windows"), "question"),
        (("wish",), "enhancement"),
        (("documentation",), "other"),
        (("bug", "enhancement"), None),  # conflicting
        (("area:cli",), None),
        ((), None),
    ],
)
def test_type_class(labels: tuple[str, ...], expected: str | None) -> None:
    assert type_class(make(1, labels=labels)) == expected


def test_split_boundary_puts_the_newest_fifth_in_test() -> None:
    issues = [make(n) for n in range(1, 11)]

    boundary = split_boundary(issues)
    cases = build_type_cases([replace(i, labels=("bug",)) for i in issues], boundary)

    assert [c.number for c in cases if c.split == "test"] == [9, 10]


def test_duplicate_from_field_and_from_comment() -> None:
    by_field = make(5, state_reason="DUPLICATE", duplicate_of=2)
    by_comment = make(6, state_reason="DUPLICATE", comments=(reply("Duplicate of #3"),))
    url_comment = make(
        7,
        labels=("duplicate",),
        comments=(reply("dup of https://github.com/acme/widgets/issues/4"),),
    )
    unrelated = make(8, comments=(reply("duplicate of #3"),))  # not closed as duplicate

    assert duplicate_target(by_field) == (REPO, 2, "duplicateOf")
    assert duplicate_target(by_comment) == (REPO, 3, "comment")
    assert duplicate_target(url_comment) == (REPO, 4, "comment")
    assert duplicate_target(unrelated) is None


def test_duplicate_pairs_exclude_newer_originals_and_missing_ones() -> None:
    issues = [
        make(1),
        make(2, day=20),
        make(3, state_reason="DUPLICATE", duplicate_of=1),  # valid: 1 is older
        make(4, state_reason="DUPLICATE", duplicate_of=2),  # original created later
        make(5, state_reason="DUPLICATE", duplicate_of=99),  # original not ingested
        make(6, state_reason="DUPLICATE", duplicate_of=6),  # self link
    ]

    cases = build_duplicate_cases(issues, split_boundary(issues))

    assert [(c.number, c.original_number) for c in cases] == [(3, 1)]


def test_answer_cases_take_the_first_maintainer_reply_to_a_question() -> None:
    issues = [
        make(
            1, labels=("question",), comments=(reply("me too", "NONE"), reply("Use --python 3.12"))
        ),
        make(2, labels=("question",), comments=(reply("ok", author="user-author"),)),  # self reply
        make(3, labels=("bug",), comments=(reply("fixed"),)),  # not a question
        make(4, labels=("question",)),  # unanswered
    ]

    cases = build_answer_cases(issues, split_boundary(issues))

    assert [(c.number, c.reference_answer) for c in cases] == [(1, "Use --python 3.12")]


def test_needs_info_positives_and_negatives() -> None:
    issues = [
        make(1, labels=("bug", "needs-mre")),
        make(2, labels=("needs-mre",)),  # positive even without a type label
        make(3, labels=("question",)),  # negative
        make(4, labels=("area:cli",)),  # neither: excluded
    ]

    cases = build_needs_info_cases(issues, split_boundary(issues))

    assert [(c.number, c.needs_info) for c in cases] == [(1, True), (2, True), (3, False)]


def test_no_case_exposes_data_newer_than_its_as_of() -> None:
    """Inputs are title and body only; as_of is the creation time; no later field leaks."""
    issues = [
        make(1, labels=("question",), comments=(reply("answer"),)),
        make(2, labels=("bug", "needs-mre"), state_reason="DUPLICATE", duplicate_of=1),
    ]
    boundary = split_boundary(issues)
    cases: list[CaseBase] = [
        *build_type_cases(issues, boundary),
        *build_duplicate_cases(issues, boundary),
        *build_answer_cases(issues, boundary),
        *build_needs_info_cases(issues, boundary),
    ]
    by_number = {i.number: i for i in issues}

    input_fields = {f.name for f in fields(CaseBase)}
    assert input_fields == {"repository", "number", "as_of", "title", "body", "split"}
    for case in cases:
        assert case.as_of == by_number[case.number].created_at
        if isinstance(case, DuplicateCase):
            assert by_number[case.original_number].created_at < case.as_of


def test_cases_round_trip_through_jsonl(tmp_path: Path) -> None:
    issues = [make(1, labels=("question",), comments=(reply("answer"),))]
    boundary = split_boundary(issues)
    for builder, case_type in (
        (build_type_cases, TypeCase),
        (build_answer_cases, AnswerCase),
        (build_needs_info_cases, NeedsInfoCase),
    ):
        cases = builder(issues, boundary)
        path = tmp_path / f"{case_type.__name__}.jsonl"
        write_jsonl(path, cases)
        assert read_cases(path, case_type) == cases


def test_golden_sample_is_stratified_reproducible_and_from_the_test_split() -> None:
    issues = [
        make(n, labels=(("bug", "question", "enhancement", "documentation")[n % 4],))
        for n in range(1, 401)
    ]
    issues += [
        make(500 + n, day=390, state_reason="DUPLICATE", duplicate_of=n) for n in range(1, 30)
    ]
    boundary = split_boundary(issues)
    datasets = Datasets(
        repository=REPO,
        boundary=boundary,
        issues=len(issues),
        types=build_type_cases(issues, boundary),
        duplicates=build_duplicate_cases(issues, boundary),
        answers=[],
        needs_info=build_needs_info_cases(issues, boundary),
    )

    golden = select_golden(datasets, size=50)

    assert golden == select_golden(datasets, size=50)
    assert len(golden) == len({g["number"] for g in golden}) == 45  # no needs-info positives
    assert all(datetime.fromisoformat(g["as_of"]) >= boundary for g in golden)
    labels = [g["type_label"] for g in golden if g["duplicate_of"] is None]
    assert labels.count("bug") == labels.count("question") == labels.count("enhancement") == 10
    assert sum(1 for g in golden if g["duplicate_of"] is not None) == 10
