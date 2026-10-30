"""Composition root helpers shared by the CLI and the HTTP app."""

from collections.abc import Mapping
from contextlib import ExitStack
from threading import Lock

import joblib

from issue_triage.config import Settings
from issue_triage.infrastructure.checkpointer import postgres_checkpointer
from issue_triage.infrastructure.db.chunks import PostgresChunkRepository
from issue_triage.infrastructure.db.engine import make_engine, make_sessions
from issue_triage.infrastructure.db.issue_vectors import PostgresIssueVectorIndex
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.db.runs import PostgresRunStore
from issue_triage.infrastructure.embeddings import SentenceTransformerEmbedder
from issue_triage.infrastructure.keyword_index import Bm25IssueIndex
from issue_triage.infrastructure.llm.fake import FakeHandler, FakeLLMProvider
from issue_triage.infrastructure.llm.fake_handlers import DEFAULT_HANDLERS
from issue_triage.infrastructure.llm.litellm_provider import LiteLLMProvider, RoutingProvider
from issue_triage.services.agent.graph import AgentDeps, Classifier, build_graph
from issue_triage.services.agent.runner import TriageAgent
from issue_triage.services.answers import AnswerDrafter, DocRetriever
from issue_triage.services.decision_model import EscalatingTriager
from issue_triage.services.docs_index import issue_query
from issue_triage.services.duplicates import SimilarIssueFinder
from issue_triage.services.llm import CallLedger, LLMClient, ModelRouter
from issue_triage.services.query_rewrite import QueryRewriter
from issue_triage.services.review_queue import ReviewQueue
from issue_triage.services.triage import Triager


def build_llm_client(
    settings: Settings,
    fake_handlers: Mapping[str, FakeHandler] | None = None,
    ledger: CallLedger | None = None,
) -> LLMClient:
    handlers = {**DEFAULT_HANDLERS, **(fake_handlers or {})}
    provider = RoutingProvider(fake=FakeLLMProvider(handlers), real=LiteLLMProvider())
    router = ModelRouter(settings.llm_models, settings.llm_default_model)
    return LLMClient(provider, router, ledger)


def build_agent_deps(settings: Settings, repository: str) -> AgentDeps:
    """Everything the triage graph needs, on the configured models and databases."""
    sessions = make_sessions(make_engine(settings.database_url))
    issues = PostgresIssueRepository(sessions).list(repository)
    texts = {i.number: issue_query(i.title, i.body) for i in issues}
    embedder = SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dim)
    llm = build_llm_client(settings)
    finder = SimilarIssueFinder(
        repository,
        embedder,
        PostgresIssueVectorIndex(sessions),
        Bm25IssueIndex(issues),
        rewriter=QueryRewriter(llm),
    )
    drafter = AnswerDrafter(
        llm, DocRetriever(embedder, PostgresChunkRepository(sessions), repository)
    )
    # The trained classifier handles confident cases when `eval-decision` has saved one (D-012).
    model_path = settings.data_dir / "models" / f"{repository.replace('/', '__')}-type.joblib"
    triager: Classifier = Triager(llm)
    if model_path.exists():
        triager = EscalatingTriager(joblib.load(model_path), embedder.embed, Triager(llm))
    return AgentDeps(
        triager=triager,
        finder=finder,
        drafter=drafter,
        embed=embedder.embed,
        candidate_text=texts.get,
    )


class LazyReviewQueue:
    """Builds the real review queue on first call and keeps its checkpointer open."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stack = ExitStack()
        self._queue: ReviewQueue | None = None
        self._lock = Lock()

    def __call__(self) -> ReviewQueue:
        with self._lock:
            if self._queue is None:
                settings = self._settings
                saver = self._stack.enter_context(postgres_checkpointer(settings.database_url))
                deps = build_agent_deps(settings, settings.repository)
                sessions = make_sessions(make_engine(settings.database_url))
                self._queue = ReviewQueue(
                    TriageAgent(build_graph(deps, saver)),
                    PostgresRunStore(sessions),
                    PostgresIssueRepository(sessions),
                )
            return self._queue

    def close(self) -> None:
        self._stack.close()
