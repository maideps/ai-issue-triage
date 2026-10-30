from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from issue_triage.domain.docs import DocChunk
from issue_triage.infrastructure.checkpointer import libpq_url, postgres_checkpointer
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.services.agent.graph import (
    AgentDeps,
    ContextViolation,
    NodeSpec,
    build_graph,
    build_nodes,
    missing_info,
    scoped,
)
from issue_triage.services.agent.runner import IssueInput, TriageAgent
from issue_triage.services.agent.state import Review
from issue_triage.services.answers import DraftResult
from issue_triage.services.triage import TriageDecision

REPO = "acme/widgets"
AS_OF = datetime(2025, 9, 20, tzinfo=UTC)
EARLIER = {
    1: "uv lock panics on windows with torch 2.5.1 error failed to resolve",
    2: "how to set the cache directory UV_CACHE_DIR",
}


class FakeTriager:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls = 0

    def classify(self, title: str, body: str) -> TriageDecision:
        self.calls += 1
        return TriageDecision(self.label, 0.8, "keywords", False, "triage@v1", "fake/x")


class FakeFinder:
    def __init__(self, numbers: list[int]) -> None:
        self.numbers = numbers
        self.calls: list[dict[str, Any]] = []

    def find(
        self,
        title: str,
        body: str,
        *,
        before: datetime,
        exclude: int | None = None,
        k: int = 10,
        rewrite: bool = False,
    ) -> list[int]:
        self.calls.append({"before": before, "exclude": exclude, "rewrite": rewrite})
        return self.numbers[:k]


class FakeDrafter:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def draft(self, title: str, body: str) -> DraftResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TimeoutError("provider timed out")
        chunk = DocChunk(REPO, "concepts/cache", "heading", 0, "Caching", "Set UV_CACHE_DIR.")
        return DraftResult("Set UV_CACHE_DIR [concepts/cache#0]", [chunk], 0.7, False, [chunk])


def deps(
    label: str, similar: list[int] | None = None, drafter: FakeDrafter | None = None
) -> AgentDeps:
    embedder = HashingEmbedder()
    return AgentDeps(
        triager=FakeTriager(label),
        finder=FakeFinder(similar or []),
        drafter=drafter or FakeDrafter(),
        embed=embedder.embed,
        candidate_text=EARLIER.get,
        duplicate_threshold=0.8,
    )


def issue(title: str, body: str, number: int = 10) -> IssueInput:
    return IssueInput(REPO, number, title, body, AS_OF)


def agent(d: AgentDeps) -> TriageAgent:
    return TriageAgent(build_graph(d, InMemorySaver()))


def test_scoped_nodes_see_only_declared_keys_and_write_only_declared_keys() -> None:
    seen: list[set[str]] = []

    def run(view: Mapping[str, Any]) -> dict[str, Any]:
        seen.append(set(view))
        return {"triage": {}}

    node = scoped(NodeSpec("t", ("title",), ("triage",), run))
    node({"title": "x", "body": "secret", "guard": {}})

    assert seen == [{"title"}]
    bad = scoped(NodeSpec("t", ("title",), ("triage",), lambda view: {"outcome": {}}))
    with pytest.raises(ContextViolation):
        bad({"title": "x"})


def test_every_node_declares_a_narrow_context() -> None:
    specs = {spec.name: spec for spec in build_nodes(deps("bug"))}

    assert specs["decide"].reads == ("triage", "retrieval", "body")
    assert "title" not in specs["draft_duplicate"].reads
    assert specs["human_gate"].reads == ("proposal",)
    assert all(spec.writes and len(spec.writes) == 1 for spec in specs.values())


def test_duplicate_branch_cites_the_earlier_issue_and_waits_for_review() -> None:
    d = deps("bug", similar=[1, 2])
    status = agent(d).start(issue("uv lock panics on windows", EARLIER[1]))

    assert status.awaiting_review
    assert status.decision is not None and status.decision.action == "duplicate"
    assert status.proposal is not None and status.proposal.citations == ["#1"]
    finder_call = d.finder.calls[0]  # type: ignore[attr-defined]
    assert finder_call == {"before": AS_OF, "exclude": 10, "rewrite": True}


@pytest.mark.parametrize(
    ("label", "body", "action"),
    [
        ("question", "How do I move the cache folder somewhere else on my machine?", "answer"),
        ("bug", "it crashes", "needs_info"),
        (
            "bug",
            "uv 0.9.1 on Linux: `uv sync` fails with exit code 2 after adding a git source " * 3,
            "summary",
        ),
        ("enhancement", "please add an option to skip dev dependencies when exporting", "summary"),
    ],
)
def test_decision_branches(label: str, body: str, action: str) -> None:
    status = agent(deps(label)).start(issue("Something", body))

    assert status.decision is not None and status.decision.action == action
    assert status.proposal is not None and status.proposal.action == action
    assert status.awaiting_review


def test_approve_edit_and_reject_outcomes() -> None:
    for review, expected in (
        (Review(decision="approve"), ("approved", False)),
        (Review(decision="edit", text="Edited reply"), ("approved", True)),
        (Review(decision="reject"), ("rejected", False)),
    ):
        a = agent(deps("question"))
        a.start(issue("Cache?", "How do I move the cache folder? " * 6))
        status = a.review(REPO, 10, review)
        assert status.outcome is not None
        assert (status.outcome.status, status.outcome.edited) == expected
        assert not status.next_nodes
    assert status.outcome is not None and status.outcome.final_text is None


def test_review_requires_a_pending_proposal() -> None:
    a = agent(deps("question"))
    a.start(issue("Cache?", "How do I move the cache folder? " * 6))
    a.review(REPO, 10, Review(decision="approve"))

    with pytest.raises(RuntimeError, match="not waiting"):
        a.review(REPO, 10, Review(decision="approve"))


def test_failed_run_resumes_from_the_last_completed_node() -> None:
    drafter = FakeDrafter(fail_times=1)
    d = deps("question", drafter=drafter)
    a = agent(d)

    with pytest.raises(TimeoutError):
        a.start(issue("Cache?", "How do I move the cache folder? " * 6))
    status = a.resume(REPO, 10)

    assert status.awaiting_review and status.proposal is not None
    assert d.triager.calls == 1  # type: ignore[attr-defined]
    assert drafter.calls == 2


def test_missing_info_heuristic() -> None:
    assert missing_info("broken")
    assert not missing_info(
        "uv 0.9.1 fails: `uv sync` exits with code 2 when a git source has a subdirectory " * 2
    )


def test_libpq_url_drops_the_driver() -> None:
    assert libpq_url("postgresql+psycopg://u:p@h:5433/db") == "postgresql://u:p@h:5433/db"


def test_postgres_checkpointer_survives_a_restart(database_url: str) -> None:
    body = "How do I move the cache folder? " * 6
    with postgres_checkpointer(database_url) as saver:
        TriageAgent(build_graph(deps("question"), saver)).start(issue("Cache?", body, number=77))

    # A new process: new graph, new connection, same database.
    with postgres_checkpointer(database_url) as saver:
        reopened = TriageAgent(build_graph(deps("question"), saver))
        waiting = reopened.status(REPO, 77)
        assert waiting.awaiting_review and waiting.proposal is not None
        done = reopened.review(REPO, 77, Review(decision="edit", text="Use UV_CACHE_DIR."))

    assert done.outcome is not None and done.outcome.final_text == "Use UV_CACHE_DIR."


def test_issue_input_is_immutable() -> None:
    original = issue("t", "b")
    assert replace(original, number=11).number == 11 and original.number == 10
