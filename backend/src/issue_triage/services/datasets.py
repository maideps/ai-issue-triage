"""Use case: build the four evaluation sets and a small golden sample."""

import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from issue_triage.domain.evaluation import (
    TYPE_CLASSES,
    AnswerCase,
    DuplicateCase,
    NeedsInfoCase,
    TypeCase,
    build_answer_cases,
    build_duplicate_cases,
    build_needs_info_cases,
    build_type_cases,
    split_boundary,
)
from issue_triage.domain.sources import IssueRepository

GOLDEN_BODY_CHARS = 1500


@dataclass(frozen=True)
class Datasets:
    repository: str
    boundary: datetime
    issues: int
    types: list[TypeCase]
    duplicates: list[DuplicateCase]
    answers: list[AnswerCase]
    needs_info: list[NeedsInfoCase]

    def stats(self) -> dict[str, Any]:
        def splits(cases: list[Any]) -> dict[str, int]:
            return dict(Counter(case.split for case in cases))

        return {
            "repository": self.repository,
            "issues": self.issues,
            "test_split_from": self.boundary.isoformat(),
            "type": {
                "cases": len(self.types),
                "classes": {c: sum(1 for t in self.types if t.label == c) for c in TYPE_CLASSES},
                "splits": splits(self.types),
            },
            "duplicates": {
                "cases": len(self.duplicates),
                "link_sources": dict(Counter(d.link_source for d in self.duplicates)),
                "splits": splits(self.duplicates),
            },
            "answers": {"cases": len(self.answers), "splits": splits(self.answers)},
            "needs_info": {
                "cases": len(self.needs_info),
                "positives": sum(1 for n in self.needs_info if n.needs_info),
                "splits": splits(self.needs_info),
            },
        }


def build_datasets(issues: IssueRepository, repository: str) -> Datasets:
    all_issues = issues.list(repository)
    boundary = split_boundary(all_issues)
    return Datasets(
        repository=repository,
        boundary=boundary,
        issues=len(all_issues),
        types=build_type_cases(all_issues, boundary),
        duplicates=build_duplicate_cases(all_issues, boundary),
        answers=build_answer_cases(all_issues, boundary),
        needs_info=build_needs_info_cases(all_issues, boundary),
    )


def select_golden(datasets: Datasets, size: int = 50, seed: int = 7) -> list[dict[str, Any]]:
    """A stratified, reproducible sample from the test split covering every task.

    Quotas: 10 per main type class, 5 "other", 10 duplicates, 5 needs-info positives;
    an issue picked for one task keeps its ground truth for the others.
    """
    rng = random.Random(seed)
    test_types = [t for t in datasets.types if t.split == "test"]
    pools: list[tuple[list[int], int]] = [
        ([t.number for t in test_types if t.label == "bug"], 10),
        ([t.number for t in test_types if t.label == "question"], 10),
        ([t.number for t in test_types if t.label == "enhancement"], 10),
        ([t.number for t in test_types if t.label == "other"], 5),
        ([d.number for d in datasets.duplicates if d.split == "test"], 10),
        ([n.number for n in datasets.needs_info if n.split == "test" and n.needs_info], 5),
    ]
    chosen: list[int] = []
    for pool, quota in pools:
        candidates = sorted(set(pool) - set(chosen))
        chosen.extend(rng.sample(candidates, min(quota, len(candidates))))
    chosen = chosen[:size]

    types = {t.number: t for t in datasets.types}
    duplicates = {d.number: d for d in datasets.duplicates}
    answers = {a.number: a for a in datasets.answers}
    needs = {n.number: n for n in datasets.needs_info}
    base: dict[int, Any] = {**needs, **answers, **duplicates, **types}
    records = []
    for number in sorted(chosen):
        case = base[number]
        records.append(
            {
                "repository": datasets.repository,
                "number": number,
                "as_of": case.as_of.isoformat(),
                "title": case.title,
                "body": case.body[:GOLDEN_BODY_CHARS],
                "type_label": types[number].label if number in types else None,
                "duplicate_of": duplicates[number].original_number
                if number in duplicates
                else None,
                "has_reference_answer": number in answers,
                "needs_info": needs[number].needs_info if number in needs else None,
                "reviewed_by": None,
            }
        )
    return records
