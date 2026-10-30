"""Traces per node and per model call, and the KPIs built on them (feat-017)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.reviews import RunRecord
from issue_triage.domain.tracing import Span
from issue_triage.infrastructure.db.runs import PostgresRunStore
from issue_triage.infrastructure.db.traces import PostgresTraceStore
from issue_triage.main import create_app
from issue_triage.services.agent.graph import build_graph
from issue_triage.services.agent.runner import IssueInput, TriageAgent
from issue_triage.services.kpis import KpiService, live_kpis, offline_kpis
from issue_triage.services.llm import LLMClient, ModelRouter
from issue_triage.services.tracing import MemoryTraceStore, Tracer
from issue_triage.services.triage import Triager
from issue_triage.wiring import build_llm_client

from .helpers import IsolatedSettings
from .test_agent import deps

REPO = "acme/widgets"
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_llm_calls_are_attributed_to_the_active_node() -> None:
    store = MemoryTraceStore()
    tracer = Tracer(store)
    llm = build_llm_client(IsolatedSettings())
    llm.on_call = tracer.llm_call

    llm.complete("triage", [])  # outside any node: ledger only
    with tracer.node(f"{REPO}#1", "triage"):
        llm.complete("triage", [])

    node = next(s for s in store.items if s.kind == "node")
    call = next(s for s in store.items if s.kind == "llm")
    assert len(store.items) == 2
    assert call.parent == "triage" and call.trace_id == f"{REPO}#1"
    assert call.model == "fake/triage-keywords"
    assert node.kind == "node" and node.status == "ok"


def test_node_status_distinguishes_errors_from_the_human_gate_pause() -> None:
    store = MemoryTraceStore()
    tracer = Tracer(store)

    with pytest.raises(ValueError), tracer.node("t", "draft_answer"):
        raise ValueError("boom")
    with pytest.raises(GraphInterrupt), tracer.node("t", "human_gate"):
        raise GraphInterrupt()

    assert [(s.name, s.status) for s in store.items] == [
        ("draft_answer", "error"),
        ("human_gate", "interrupted"),
    ]
    assert store.items[0].error == "ValueError: boom"


def test_agent_runs_produce_one_span_per_node_and_model_spans_inside() -> None:
    store = MemoryTraceStore()
    tracer = Tracer(store)
    llm = LLMClient(
        build_llm_client(IsolatedSettings()).provider,
        ModelRouter({"triage": "fake/triage-keywords"}, "fake/default"),
    )
    llm.on_call = tracer.llm_call
    d = deps("question")
    d.triager = Triager(llm)
    d.tracer = tracer
    agent = TriageAgent(build_graph(d, InMemorySaver()))

    agent.start(
        IssueInput(REPO, 5, "How do I move the cache?", "How do I move the cache folder? " * 5, NOW)
    )

    nodes = [(s.name, s.status) for s in store.spans(f"{REPO}#5") if s.kind == "node"]
    assert nodes[:4] == [("guard", "ok"), ("triage", "ok"), ("retrieve", "ok"), ("decide", "ok")]
    assert nodes[-1] == ("human_gate", "interrupted")
    calls = [s for s in store.items if s.kind == "llm"]
    assert [(c.name, c.parent) for c in calls] == [("triage", "triage")]


def span(
    trace: str, kind: str, name: str, ms: float, cost: float = 0.0, status: str = "ok"
) -> Span:
    return Span(trace, kind, name, NOW, ms, status, cost_usd=cost)  # type: ignore[arg-type]


def run(number: int, status: str) -> RunRecord:
    return RunRecord(REPO, number, "t", status, "answer", 0.5, False, "text")  # type: ignore[arg-type]


def test_live_kpis_aggregate_runs_feedback_and_traces() -> None:
    spans = [
        span("a", "node", "triage", 10),
        span("a", "llm", "triage", 8, cost=0.002),
        span("a", "node", "draft_answer", 30),
        span("a", "node", "human_gate", 0.1, status="interrupted"),
        span("b", "node", "triage", 5),
        span("b", "node", "draft_summary", 15),
        span("c", "node", "triage", 20, status="error"),
    ]
    runs = [run(1, "approved"), run(2, "approved"), run(3, "rejected"), run(4, "pending")]

    kpis = live_kpis(runs, {"approve": 1, "edit": 1, "reject": 1}, spans)

    assert (kpis["pending"], kpis["approved"], kpis["rejected"]) == (1, 2, 1)
    assert kpis["approval_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert kpis["edit_rate"] == 0.5
    assert kpis["escalation_rate"] == pytest.approx(1 / 3, abs=1e-4)  # only run a asked the LLM
    assert kpis["cost_per_issue_usd"] == pytest.approx(0.002 / 3, abs=1e-6)
    assert kpis["latency_ms_p50"] == 20.0  # node time without the human gate: 40, 20, 20
    assert kpis["errors"] == 1 and kpis["llm_calls"] == 1


def test_live_kpis_handle_an_empty_system() -> None:
    kpis = live_kpis([], {}, [])

    assert kpis["approval_rate"] is None and kpis["latency_ms_p50"] is None


def test_offline_kpis_read_committed_summaries(tmp_path: Path) -> None:
    (tmp_path / "decision-model-test.json").write_text(
        json.dumps({"test_escalating": {"macro_f1": 0.59, "escalation_rate": 0.34}})
    )
    (tmp_path / "rerank-all.json").write_text(
        json.dumps(
            {
                "results": [
                    {"mode": "hybrid", "recall_at_10": 0.4},
                    {"mode": "hybrid+rewrite", "recall_at_10": 0.53},
                ]
            }
        )
    )

    kpis = offline_kpis(tmp_path)

    assert kpis["triage_macro_f1"] == 0.59 and kpis["duplicate_recall_at_10"] == 0.53
    assert kpis["answer_faithfulness"] is None  # not run: reported as missing, not zero


def test_kpi_and_trace_endpoints_on_postgres(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    runs, traces = PostgresRunStore(sessions), PostgresTraceStore(sessions)
    runs.upsert(run(1, "approved"))
    traces.add(span(f"{REPO}#1", "node", "triage", 12.5))
    traces.add(
        Span(
            f"{REPO}#1", "llm", "triage", NOW, 9.0, parent="triage", model="fake/x", input_tokens=50
        )
    )
    service = KpiService(runs, traces, tmp_path)
    client = TestClient(
        create_app(IsolatedSettings(environment="test"), kpi_provider=lambda: service)
    )

    kpis = client.get("/api/kpis").json()
    trace = client.get(f"/api/traces/{REPO}/1").json()

    assert kpis["live"]["approved"] == 1 and kpis["live"]["escalation_rate"] == 1.0
    assert [(s["kind"], s["name"], s["parent"]) for s in trace] == [
        ("node", "triage", None),
        ("llm", "triage", "triage"),
    ]
    assert trace[1]["input_tokens"] == 50
    assert (
        TestClient(create_app(IsolatedSettings(environment="test"))).get("/api/kpis").status_code
        == 503
    )


def test_offline_kpis_find_the_committed_results() -> None:
    """The app reads evals/results/ (a wrong folder once made every offline KPI null)."""
    kpis = offline_kpis(IsolatedSettings().evals_dir / "results")

    assert kpis["triage_macro_f1"] is not None
    assert kpis["duplicate_recall_at_10"] is not None
