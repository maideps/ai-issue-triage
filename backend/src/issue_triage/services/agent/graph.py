"""The triage graph.

    guard -> triage -> retrieve -> decide -+-> draft_duplicate  --+
                                           +-> draft_answer     --+-> human_gate -> finalize
                                           +-> draft_needs_info --+
                                           +-> draft_summary    --+

Each node declares the state keys it reads and writes; ``scoped`` hands it only those keys
and rejects anything else it returns, so "what context does this node see?" is answered in
code, not by convention. ``decide`` uses no LLM: choosing among four actions from typed
signals is a rule, not a reasoning task. ``human_gate`` interrupts the run until a
maintainer approves, edits or rejects; the Postgres checkpointer keeps the run alive in the
meantime and lets a failed run resume from its last completed node.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from issue_triage.domain.answer_metrics import cosine
from issue_triage.services.agent.state import (
    AgentState,
    Decision,
    GuardOut,
    Outcome,
    Proposal,
    RetrievalOut,
    Review,
    SimilarIssue,
    TriageOut,
)
from issue_triage.services.answers import DraftResult, chunk_id
from issue_triage.services.docs_index import issue_query
from issue_triage.services.guardrails import guard_input
from issue_triage.services.routing import LEGACY, RoutingPolicy, RoutingSignals
from issue_triage.services.routing import route as route_action
from issue_triage.services.tracing import Tracer
from issue_triage.services.triage import TriageDecision

VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")
REPRO = re.compile(r"```|`[^`]+`|^\s*\$ ", re.MULTILINE)


class ContextViolation(RuntimeError):
    """A node returned a key it did not declare."""


class Classifier(Protocol):
    def classify(self, title: str, body: str) -> TriageDecision: ...


class Finder(Protocol):
    def find(
        self,
        title: str,
        body: str,
        *,
        before: datetime,
        exclude: int | None = None,
        k: int = 10,
        rewrite: bool = False,
    ) -> list[int]: ...


class Drafter(Protocol):
    def draft(self, title: str, body: str) -> DraftResult: ...


@dataclass
class AgentDeps:
    triager: Classifier
    finder: Finder
    drafter: Drafter
    embed: Callable[[Sequence[str]], list[list[float]]]
    candidate_text: Callable[[int], str | None]
    duplicate_threshold: float = 0.8
    similar_k: int = 5
    tracer: Tracer | None = None
    # Calibrated routing (D-015); None keeps the legacy rules with duplicate_threshold.
    routing: RoutingPolicy | None = None
    needs_info: Callable[[str, str], float] | None = None


Node = Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class NodeSpec:
    name: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    run: Node


def scoped(spec: NodeSpec, tracer: Tracer | None = None) -> Callable[[AgentState], dict[str, Any]]:
    def node(state: AgentState) -> dict[str, Any]:
        if tracer is not None and "repository" in state and "number" in state:
            with tracer.node(f"{state['repository']}#{state['number']}", spec.name):
                return run(state)
        return run(state)

    def run(state: AgentState) -> dict[str, Any]:
        view = {key: state[key] for key in spec.reads if key in state}  # type: ignore[literal-required]
        update = spec.run(view)
        extra = set(update) - set(spec.writes)
        if extra:
            raise ContextViolation(f"{spec.name} wrote undeclared keys {sorted(extra)}")
        return update

    node.__name__ = spec.name
    return node


def missing_info(body: str) -> bool:
    """A bug report with no version and no command or code, or almost no text."""
    return len(body.strip()) < 150 or not (VERSION.search(body) or REPRO.search(body))


def build_nodes(deps: AgentDeps) -> list[NodeSpec]:
    def guard(view: Mapping[str, Any]) -> dict[str, Any]:
        g = guard_input(view["title"], view["body"])
        out = GuardOut(flagged=g.flagged, signals=g.injection, redactions=g.redactions)
        return {"guard": out.model_dump()}

    def triage(view: Mapping[str, Any]) -> dict[str, Any]:
        d = deps.triager.classify(view["title"], view["body"])
        return {
            "triage": TriageOut(
                label=d.label, confidence=d.confidence, rationale=d.rationale
            ).model_dump()
        }

    def retrieve(view: Mapping[str, Any]) -> dict[str, Any]:
        numbers = deps.finder.find(
            view["title"],
            view["body"],
            before=datetime.fromisoformat(view["as_of"]),
            exclude=view["number"],
            k=deps.similar_k,
            rewrite=True,
        )
        texts = [(n, t) for n in numbers if (t := deps.candidate_text(n))]
        similar: list[SimilarIssue] = []
        if texts:
            vectors = deps.embed([issue_query(view["title"], view["body"]), *[t for _, t in texts]])
            similar = [
                SimilarIssue(number=n, similarity=round(cosine(vectors[0], v), 4))
                for (n, _), v in zip(texts, vectors[1:], strict=True)
            ]
        return {"retrieval": RetrievalOut(similar=similar).model_dump()}

    def decide(view: Mapping[str, Any]) -> dict[str, Any]:
        triage = TriageOut.model_validate(view["triage"])
        similar = RetrievalOut.model_validate(view["retrieval"]).similar
        best = max(similar, key=lambda s: s.similarity, default=None)
        policy = deps.routing or replace(LEGACY, duplicate_threshold=deps.duplicate_threshold)
        signals = RoutingSignals(
            triage_label=triage.label,
            best_similarity=best.similarity if best else None,
            best_number=best.number if best else None,
            needs_info_probability=deps.needs_info(view["title"], view["body"])
            if deps.needs_info is not None
            else None,
            missing_info=missing_info(view["body"]),
        )
        action, reason = route_action(signals, policy)
        decision = Decision(action=action, reason=reason)
        return {"decision": decision.model_dump()}

    def flagged(view: Mapping[str, Any]) -> bool:
        return bool(GuardOut.model_validate(view["guard"]).flagged)

    def draft_duplicate(view: Mapping[str, Any]) -> dict[str, Any]:
        best = max(
            RetrievalOut.model_validate(view["retrieval"]).similar, key=lambda s: s.similarity
        )
        proposal = Proposal(
            action="duplicate",
            text=(
                f"Thanks for the report! This looks like a duplicate of #{best.number}, which "
                "covers the same problem. If yours is different, please add the details and "
                "we will take another look."
            ),
            citations=[f"#{best.number}"],
            confidence=best.similarity,
            needs_attention=flagged(view),
        )
        return {"proposal": proposal.model_dump()}

    def draft_answer(view: Mapping[str, Any]) -> dict[str, Any]:
        result = deps.drafter.draft(view["title"], view["body"])
        proposal = Proposal(
            action="answer",
            text=result.reply,
            citations=[chunk_id(c) for c in result.citations],
            confidence=result.confidence,
            needs_attention=result.needs_human or flagged(view),
        )
        return {"proposal": proposal.model_dump()}

    def draft_needs_info(view: Mapping[str, Any]) -> dict[str, Any]:
        proposal = Proposal(
            action="needs_info",
            text=(
                "Thanks for the report! To look into this we need a bit more information:\n\n"
                "- the output of `uv --version` and your operating system\n"
                "- the exact command you ran and its full output\n"
                "- a minimal reproducible example (a small `pyproject.toml` helps)"
            ),
            confidence=0.6,
            needs_attention=flagged(view),
        )
        return {"proposal": proposal.model_dump()}

    def draft_summary(view: Mapping[str, Any]) -> dict[str, Any]:
        triage = TriageOut.model_validate(view["triage"])
        similar = RetrievalOut.model_validate(view["retrieval"]).similar[:3]
        related = ", ".join(f"#{s.number}" for s in similar) or "none found"
        proposal = Proposal(
            action="summary",
            text=(
                f"Triage note (not a public reply): looks like a {triage.label} "
                f"(confidence {triage.confidence:.2f}). {triage.rationale}. "
                f"Related earlier issues: {related}."
            ),
            citations=[f"#{s.number}" for s in similar],
            confidence=triage.confidence,
            needs_attention=flagged(view),
        )
        return {"proposal": proposal.model_dump()}

    def human_gate(view: Mapping[str, Any]) -> dict[str, Any]:
        review = Review.model_validate(interrupt({"proposal": view["proposal"]}))
        if review.decision == "edit" and not (review.text and review.text.strip()):
            raise ValueError("an edit needs the edited text")
        return {"review": review.model_dump()}

    def finalize(view: Mapping[str, Any]) -> dict[str, Any]:
        proposal = Proposal.model_validate(view["proposal"])
        review = Review.model_validate(view["review"])
        if review.decision == "reject":
            outcome = Outcome(status="rejected", final_text=None, edited=False)
        else:
            edited = review.decision == "edit"
            outcome = Outcome(
                status="approved",
                final_text=review.text if edited else proposal.text,
                edited=edited,
            )
        return {"outcome": outcome.model_dump()}

    issue = ("title", "body")
    return [
        NodeSpec("guard", issue, ("guard",), guard),
        NodeSpec("triage", issue, ("triage",), triage),
        NodeSpec("retrieve", (*issue, "number", "as_of"), ("retrieval",), retrieve),
        NodeSpec("decide", ("triage", "retrieval", "title", "body"), ("decision",), decide),
        NodeSpec("draft_duplicate", ("retrieval", "guard"), ("proposal",), draft_duplicate),
        NodeSpec("draft_answer", (*issue, "guard"), ("proposal",), draft_answer),
        NodeSpec("draft_needs_info", ("guard",), ("proposal",), draft_needs_info),
        NodeSpec("draft_summary", ("triage", "retrieval", "guard"), ("proposal",), draft_summary),
        NodeSpec("human_gate", ("proposal",), ("review",), human_gate),
        NodeSpec("finalize", ("proposal", "review"), ("outcome",), finalize),
    ]


def route(state: AgentState) -> str:
    return f"draft_{Decision.model_validate(state['decision']).action}"


def build_graph(
    deps: AgentDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    for spec in build_nodes(deps):
        graph.add_node(spec.name, scoped(spec, deps.tracer))  # type: ignore[call-overload]
    graph.add_edge(START, "guard")
    graph.add_edge("guard", "triage")
    graph.add_edge("triage", "retrieve")
    graph.add_edge("retrieve", "decide")
    drafts = ["draft_duplicate", "draft_answer", "draft_needs_info", "draft_summary"]
    graph.add_conditional_edges("decide", route, drafts)
    for name in drafts:
        graph.add_edge(name, "human_gate")
    graph.add_edge("human_gate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
