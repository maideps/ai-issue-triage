"""Command line entry point: ``python -m issue_triage <command>``.

Composition root (like ``main.py``): wires infrastructure into the use cases.
"""

import argparse
import json
import random
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import joblib
from langgraph.checkpoint.memory import InMemorySaver

from issue_triage.config import Settings, get_settings
from issue_triage.domain.docs import CHUNKERS, Embedder
from issue_triage.domain.evaluation import (
    TYPE_CLASSES,
    AnswerCase,
    DuplicateCase,
    NeedsInfoCase,
    TypeCase,
    build_doc_link_cases,
)
from issue_triage.domain.metrics import classification_report
from issue_triage.domain.pseudonymize import Pseudonymizer
from issue_triage.domain.sources import IssueSource
from issue_triage.infrastructure.checkpointer import postgres_checkpointer
from issue_triage.infrastructure.dataset_files import read_cases, write_json, write_jsonl
from issue_triage.infrastructure.db.chunks import PostgresChunkRepository
from issue_triage.infrastructure.db.engine import make_engine, make_sessions, upgrade
from issue_triage.infrastructure.db.issue_vectors import PostgresIssueVectorIndex
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.docs_source import GitHubDocsSource, local_docs
from issue_triage.infrastructure.embeddings import SentenceTransformerEmbedder
from issue_triage.infrastructure.github_rest import GitHubRest
from issue_triage.infrastructure.github_source import GitHubGraphQLSource, urllib_transport
from issue_triage.infrastructure.jsonl_store import JsonlIssueStore
from issue_triage.infrastructure.keyword_index import Bm25IssueIndex
from issue_triage.infrastructure.reranker import CrossEncoderReranker
from issue_triage.services.agent.graph import build_graph
from issue_triage.services.agent.runner import IssueInput, RunStatus, TriageAgent
from issue_triage.services.agent.state import Review
from issue_triage.services.answer_eval import Judge, evaluate_answers
from issue_triage.services.answers import AnswerDrafter, DocRetriever
from issue_triage.services.datasets import build_datasets, select_golden
from issue_triage.services.decision_model import EmbeddingClassifier, escalation_curve
from issue_triage.services.docs_index import evaluate_doc_retrieval, index_docs, issue_query
from issue_triage.services.duplicates import (
    RERANK_VARIANTS,
    SimilarIssueFinder,
    embed_issues,
    evaluate_duplicates,
)
from issue_triage.services.eval_runner import run_type_eval
from issue_triage.services.ingestion import ingest_issues
from issue_triage.services.loading import load_issues
from issue_triage.services.query_rewrite import QUERY_REWRITE_STEP, QueryRewriter
from issue_triage.services.replay_eval import evaluate_replay
from issue_triage.services.triage import Triager
from issue_triage.wiring import (
    LazyReviewQueue,
    build_agent_deps,
    build_llm_client,
    github_token,
)


def dataset_path(settings: Settings, repository: str, name: str) -> Path:
    path = settings.data_dir / "datasets" / repository.replace("/", "__") / f"{name}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} not found: run `datasets {repository}` first")
    return path


def split_start(settings: Settings, repository: str) -> datetime:
    """The test split's first creation time, from the committed dataset stats."""
    stats = settings.evals_dir / "datasets" / f"{repository.replace('/', '__')}-stats.json"
    if not stats.exists():
        raise SystemExit(f"{stats} not found: run `datasets {repository}` first")
    return datetime.fromisoformat(json.loads(stats.read_text(encoding="utf-8"))["test_split_from"])


def make_embedder(settings: Settings) -> Embedder:
    return SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_dim)


def chunk_index(settings: Settings) -> PostgresChunkRepository:
    return PostgresChunkRepository(make_sessions(make_engine(settings.database_url)))


def cmd_docs_index(args: argparse.Namespace, settings: Settings) -> int:
    # Docs as they were when the test split starts: no test issue sees newer docs (D-004).
    before = args.before or split_start(settings, args.repository)
    source = GitHubDocsSource(settings.data_dir / "repos", github_token(settings))
    commit, pages = source.snapshot(args.repository, before)
    embedder, index = make_embedder(settings), chunk_index(settings)
    for chunker in CHUNKERS:
        count = index_docs(pages, chunker, embedder, index, commit)
        print(f"{chunker}: {count} chunks from {len(pages)} pages at {commit[:10]}")
    return 0


def issue_repository(settings: Settings) -> PostgresIssueRepository:
    return PostgresIssueRepository(make_sessions(make_engine(settings.database_url)))


def vector_index(settings: Settings) -> PostgresIssueVectorIndex:
    return PostgresIssueVectorIndex(make_sessions(make_engine(settings.database_url)))


def cmd_issues_embed(args: argparse.Namespace, settings: Settings) -> int:
    issues = issue_repository(settings).list(args.repository)
    done = embed_issues(
        issues,
        make_embedder(settings),
        vector_index(settings),
        on_batch=lambda n: print(f"{n}/{len(issues)} embedded", file=sys.stderr),
    )
    print(f"{args.repository}: {done} issue embeddings stored")
    return 0


def cmd_eval_duplicates(args: argparse.Namespace, settings: Settings) -> int:
    cases = [
        c
        for c in read_cases(dataset_path(settings, args.repository, "duplicates"), DuplicateCase)
        if args.split == "all" or c.split == args.split
    ]
    embedder = make_embedder(settings)
    finder = SimilarIssueFinder(
        args.repository,
        embedder,
        vector_index(settings),
        Bm25IssueIndex(issue_repository(settings).list(args.repository)),
    )
    results = evaluate_duplicates(cases, finder, embedder, k=args.k)
    summary = {
        "task": "duplicates",
        "repository": args.repository,
        "split": args.split,
        "embedding_model": settings.embedding_model,
        "ground_truth": "original linked when maintainers closed an issue as a duplicate",
        "results": results,
    }
    out = settings.evals_dir / "results" / f"duplicates-{args.split}.json"
    write_json(out, summary)
    for r in results:
        print(
            f"{r['mode']:8} n={r['n']} recall@1={r['recall_at_1']:.3f} "
            f"recall@5={r['recall_at_5']:.3f} recall@{args.k}={r[f'recall_at_{args.k}']:.3f} "
            f"mrr@{args.k}={r[f'mrr_at_{args.k}']:.3f}"
        )
    print(f"written to {out}")
    return 0


def cmd_eval_rerank(args: argparse.Namespace, settings: Settings) -> int:
    cases = [
        c
        for c in read_cases(dataset_path(settings, args.repository, "duplicates"), DuplicateCase)
        if args.split == "all" or c.split == args.split
    ]
    issues = issue_repository(settings).list(args.repository)
    texts = {i.number: issue_query(i.title, i.body) for i in issues}
    embedder = make_embedder(settings)
    llm = build_llm_client(settings)
    finder = SimilarIssueFinder(
        args.repository,
        embedder,
        vector_index(settings),
        Bm25IssueIndex(issues),
        rewriter=QueryRewriter(llm),
        reranker=CrossEncoderReranker(settings.reranker_model),
        candidate_text=texts.__getitem__,
    )
    results = evaluate_duplicates(cases, finder, embedder, variants=RERANK_VARIANTS, k=args.k)
    summary = {
        "task": "duplicates-rerank",
        "repository": args.repository,
        "split": args.split,
        "embedding_model": settings.embedding_model,
        "reranker_model": settings.reranker_model,
        "query_rewrite_model": llm.router.model_for(QUERY_REWRITE_STEP),
        "rerank_top": 20,
        "results": results,
        "llm": llm.ledger.summary(),
    }
    out = settings.evals_dir / "results" / f"rerank-{args.split}.json"
    write_json(out, summary)
    for r in results:
        print(
            f"{r['mode']:22} recall@1={r['recall_at_1']:.3f} recall@5={r['recall_at_5']:.3f} "
            f"recall@{args.k}={r[f'recall_at_{args.k}']:.3f} mrr={r[f'mrr_at_{args.k}']:.3f} "
            f"p50={r['latency_ms_p50']}ms"
        )
    print(f"written to {out}")
    return 0


def cmd_eval_answers(args: argparse.Namespace, settings: Settings) -> int:
    cases = [
        a
        for a in read_cases(dataset_path(settings, args.repository, "answers"), AnswerCase)
        if args.split == "all" or a.split == args.split
    ]
    if args.limit:
        cases = cases[: args.limit]
    embedder = make_embedder(settings)
    llm = build_llm_client(settings)
    drafter = AnswerDrafter(llm, DocRetriever(embedder, chunk_index(settings), args.repository))
    summary = evaluate_answers(
        cases,
        drafter,
        Judge(llm),
        embedder,
        llm.ledger,
        docs_base_url=settings.docs_base_url,
        on_progress=lambda n: print(f"{n}/{len(cases)} drafted", file=sys.stderr),
    )
    summary.update(repository=args.repository, split=args.split, prompt="answer@v1")
    out = settings.evals_dir / "results" / f"answers-{args.split}.json"
    write_json(out, summary)
    print(f"{summary['drafted']}/{summary['n']} drafted")
    for key in (
        "faithfulness_lexical",
        "answer_relevancy_cosine",
        "reference_similarity_cosine",
        "judge_score_mean",
        "cited_page_matches_maintainer_link",
    ):
        print(f"  {key:36} {summary[key]}")
    print(f"written to {out}")
    return 0


THRESHOLDS = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)


def macro_f1(expected: Sequence[str], predicted: Sequence[str]) -> float:
    return classification_report(expected, predicted, TYPE_CLASSES).macro_f1


def cmd_eval_decision(args: argparse.Namespace, settings: Settings) -> int:
    """Train on older issues, tune the escalation threshold on the newest train slice,
    report on the test split (D-004)."""
    cases = read_cases(dataset_path(settings, args.repository, "type"), TypeCase)
    vectors = vector_index(settings).vectors(args.repository, [c.number for c in cases])
    cases = sorted((c for c in cases if c.number in vectors), key=lambda c: c.as_of)
    train = [c for c in cases if c.split == "train"]
    test = [c for c in cases if c.split == "test"]
    cut = int(len(train) * 0.85)
    fit_set, validation = train[:cut], train[cut:]

    classifier = EmbeddingClassifier().fit(
        [vectors[c.number] for c in fit_set], [c.label for c in fit_set]
    )
    llm = build_llm_client(settings)
    triager = Triager(llm)

    def llm_labels(subset: Sequence[TypeCase]) -> list[str]:
        return [triager.classify(c.title, c.body).label for c in subset]

    val_curve = escalation_curve(
        [c.label for c in validation],
        classifier.predict([vectors[c.number] for c in validation]),
        llm_labels(validation),
        THRESHOLDS,
        macro_f1,
    )
    best = max(val_curve, key=lambda r: (r["macro_f1"], -r["escalation_rate"]))

    start = time.perf_counter()
    test_predictions = classifier.predict([vectors[c.number] for c in test])
    classifier_ms = (time.perf_counter() - start) * 1000 / max(1, len(test))
    llm_before = len(llm.ledger.records)
    test_llm = llm_labels(test)
    test_records = llm.ledger.records[llm_before:]
    test_curve = escalation_curve(
        [c.label for c in test], test_predictions, test_llm, THRESHOLDS, macro_f1
    )
    at_best = next(r for r in test_curve if r["threshold"] == best["threshold"])
    llm_cost = sum(r.cost_usd for r in test_records)
    summary = {
        "task": "type-decision-model",
        "repository": args.repository,
        "model": "logistic regression on issue embeddings (class_weight=balanced)",
        "embedding_model": settings.embedding_model,
        "llm_model": llm.router.model_for("triage"),
        "train": len(fit_set),
        "validation": len(validation),
        "test": len(test),
        "chosen_threshold": best["threshold"],
        "test_classifier_only": test_curve[0],
        "test_llm_only": test_curve[-1],
        "test_escalating": at_best,
        "classifier_ms_per_issue": round(classifier_ms, 4),
        "llm_cost_usd_all_test": round(llm_cost, 6),
        "llm_cost_usd_escalating": round(llm_cost * at_best["escalation_rate"], 6),
        "validation_curve": val_curve,
        "test_curve": test_curve,
    }
    out = settings.evals_dir / "results" / "decision-model-test.json"
    write_json(out, summary)
    model_path = settings.data_dir / "models" / f"{args.repository.replace('/', '__')}-type.joblib"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        EmbeddingClassifier().fit([vectors[c.number] for c in train], [c.label for c in train]),
        model_path,
    )
    for name in ("test_classifier_only", "test_llm_only", "test_escalating"):
        row = summary[name]
        print(f"{name:22} macro-F1 {row['macro_f1']:.3f}  escalated {row['escalation_rate']:.1%}")
    print(f"threshold {best['threshold']} (chosen on validation); written to {out}")
    return 0


def cmd_eval_replay(args: argparse.Namespace, settings: Settings) -> int:
    """Replay a stratified test-split sample through the whole agent (in memory)."""
    repository = issue_repository(settings)
    duplicates = [
        c.number
        for c in read_cases(dataset_path(settings, args.repository, "duplicates"), DuplicateCase)
        if c.split == "test"
    ]
    needs = [
        c.number
        for c in read_cases(dataset_path(settings, args.repository, "needs_info"), NeedsInfoCase)
        if c.split == "test" and c.needs_info
    ]
    answers = [
        c.number
        for c in read_cases(dataset_path(settings, args.repository, "answers"), AnswerCase)
        if c.split == "test"
    ]
    others = [
        c.number
        for c in read_cases(dataset_path(settings, args.repository, "type"), TypeCase)
        if c.split == "test" and c.label in ("bug", "enhancement", "other")
    ]
    rng = random.Random(7)
    chosen: list[int] = []
    for pool, quota in (
        (duplicates, args.per_group),
        (needs, args.per_group),
        (answers, args.per_group),
        (others, args.per_group),
    ):
        candidates = sorted(set(pool) - set(chosen))
        chosen += rng.sample(candidates, min(quota, len(candidates)))
    issues = [i for n in sorted(chosen) if (i := repository.get(args.repository, n))]
    agent = TriageAgent(build_graph(build_agent_deps(settings, args.repository), InMemorySaver()))
    summary = evaluate_replay(
        issues, agent, on_progress=lambda n: print(f"{n}/{len(issues)} replayed", file=sys.stderr)
    )
    summary.update(repository=args.repository, split="test", sample_seed=7)
    out = settings.evals_dir / "results" / "replay-test.json"
    write_json(out, summary)
    print(json.dumps({k: summary[k] for k in ("n", "actions", "action_agreement")}, indent=2))
    print(f"written to {out}")
    return 0


def cmd_sandbox_poll(args: argparse.Namespace, settings: Settings) -> int:
    """Queue new issues from an allowlisted sandbox (no public webhook URL needed)."""
    if args.repository.lower() not in {s.lower() for s in settings.sandbox_repositories}:
        raise SystemExit(f"{args.repository} is not in ISSUE_TRIAGE_SANDBOX_REPOSITORIES")
    services = LazyReviewQueue(settings)
    try:
        details = services.live().poll(GitHubRest(github_token(settings)), args.repository)
        for detail in details:
            print(f"queued #{detail.run.number}: {detail.run.action} - {detail.run.title}")
        print(f"{len(details)} new issue(s) queued from {args.repository}")
    finally:
        services.close()
    return 0


def print_status(status: RunStatus) -> None:
    print(f"{status.thread_id}: next={list(status.next_nodes) or 'done'}")
    if status.decision:
        print(f"  decision: {status.decision.action} ({status.decision.reason})")
    if status.proposal:
        attention = " [needs attention]" if status.proposal.needs_attention else ""
        print(f"  proposal ({status.proposal.confidence:.2f}){attention}: {status.proposal.text}")
        print(f"  citations: {status.proposal.citations}")
    if status.outcome:
        print(f"  outcome: {status.outcome.status}; final text: {status.outcome.final_text}")


def cmd_agent_run(args: argparse.Namespace, settings: Settings) -> int:
    """Replay a stored issue through the graph as of its creation time (D-004)."""
    issue = issue_repository(settings).get(args.repository, args.number)
    if issue is None:
        raise SystemExit(f"{args.repository}#{args.number} is not loaded")
    with postgres_checkpointer(settings.database_url) as saver:
        agent = TriageAgent(build_graph(build_agent_deps(settings, args.repository), saver))
        existing = agent.status(args.repository, args.number)
        if existing.next_nodes or existing.outcome:
            status = (
                agent.resume(args.repository, args.number)
                if existing.next_nodes != ("human_gate",)
                else existing
            )
        else:
            status = agent.start(
                IssueInput(
                    issue.repository, issue.number, issue.title, issue.body, issue.created_at
                )
            )
        print_status(status)
    return 0


def cmd_agent_review(args: argparse.Namespace, settings: Settings) -> int:
    if args.edit:
        review = Review(decision="edit", text=args.edit, reviewer=args.reviewer)
    else:
        review = Review(decision="approve" if args.approve else "reject", reviewer=args.reviewer)
    with postgres_checkpointer(settings.database_url) as saver:
        agent = TriageAgent(build_graph(build_agent_deps(settings, args.repository), saver))
        print_status(agent.review(args.repository, args.number, review))
    return 0


GOLDEN_TOLERANCE = 0.02


def cmd_eval_golden(args: argparse.Namespace, settings: Settings) -> int:
    """Triage on the committed golden sample: no database, safe for CI."""
    slug = args.repository.replace("/", "__")
    golden = settings.evals_dir / "datasets" / "golden" / f"{slug}-golden.jsonl"
    records = [
        json.loads(line) for line in golden.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    cases = [
        TypeCase(
            args.repository,
            r["number"],
            datetime.fromisoformat(r["as_of"]),
            r["title"],
            r["body"],
            "test",
            r["type_label"],
        )
        for r in records
        if r["type_label"]
    ]
    llm = build_llm_client(settings)
    summary = run_type_eval(cases, Triager(llm), llm.ledger)
    baseline_path = settings.evals_dir / "results" / f"golden-baseline-{slug}.json"
    n, f1 = summary["n"], summary["macro_f1"]
    print(f"golden triage: macro-F1 {f1:.3f} on {n} issues ({summary['models']})")
    if args.update or not baseline_path.exists():
        write_json(
            baseline_path,
            {"macro_f1": summary["macro_f1"], "n": summary["n"], "models": summary["models"]},
        )
        print(f"baseline written to {baseline_path}")
        return 0
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if args.check and summary["macro_f1"] < baseline["macro_f1"] - GOLDEN_TOLERANCE:
        print(f"REGRESSION: below baseline {baseline['macro_f1']:.3f} - {GOLDEN_TOLERANCE}")
        return 1
    print(f"baseline {baseline['macro_f1']:.3f}: ok")
    return 0


def cmd_demo_seed(args: argparse.Namespace, settings: Settings) -> int:
    """Make a fresh database demo-ready from the committed sample; safe to rerun."""
    repository = args.repository
    upgrade(settings.database_url)
    issues = issue_repository(settings)
    demo = settings.samples_dir / "demo"
    if issues.count(repository) == 0:
        print(f"loaded {load_issues(JsonlIssueStore(demo), issues, repository)} demo issues")
    vectors = vector_index(settings)
    if vectors.count(repository) < issues.count(repository):
        embedded = embed_issues(issues.list(repository), make_embedder(settings), vectors)
        print(f"embedded {embedded} issues")
    chunks = chunk_index(settings)
    if chunks.count(repository, "heading") == 0:
        pages = local_docs(demo / "docs", repository)
        indexed = index_docs(pages, "heading", make_embedder(settings), chunks, "demo")
        print(f"indexed {indexed} doc chunks")
    services = LazyReviewQueue(settings)
    try:
        queue = services()
        if not queue.list(limit=1):
            golden = json.loads((demo / "golden.json").read_text(encoding="utf-8"))
            for number in golden[: args.queue]:
                queue.enqueue(repository, number)
            print(f"queued {min(args.queue, len(golden))} golden issues for review")
    finally:
        services.close()
    print("demo ready")
    return 0


def cmd_eval_docs(args: argparse.Namespace, settings: Settings) -> int:
    answers = [
        a
        for a in read_cases(dataset_path(settings, args.repository, "answers"), AnswerCase)
        if args.split == "all" or a.split == args.split
    ]
    cases = build_doc_link_cases(answers, settings.docs_base_url)
    embedder, index = make_embedder(settings), chunk_index(settings)
    results = [
        evaluate_doc_retrieval(cases, embedder, index, args.repository, chunker, k=args.k)
        for chunker in CHUNKERS
    ]
    summary = {
        "task": "doc-retrieval",
        "repository": args.repository,
        "split": args.split,
        "embedding_model": settings.embedding_model,
        "ground_truth": "doc pages linked in the first maintainer answer to a question",
        "results": results,
    }
    out = settings.evals_dir / "results" / f"chunking-{args.split}.json"
    write_json(out, summary)
    for r in results:
        print(
            f"{r['chunker']:8} n={r['n']} chunks={r['chunks']} hit@1={r['hit_at_1']:.3f} "
            f"hit@{args.k}={r[f'hit_at_{args.k}']:.3f} mrr={r['mrr']:.3f}"
        )
    print(f"written to {out}")
    return 0


def cmd_eval_triage(args: argparse.Namespace, settings: Settings) -> int:
    cases = [
        c
        for c in read_cases(dataset_path(settings, args.repository, "type"), TypeCase)
        if args.split == "all" or c.split == args.split
    ]
    if args.limit:
        cases = cases[: args.limit]
    llm = build_llm_client(settings)
    summary = run_type_eval(
        cases,
        Triager(llm),
        llm.ledger,
        on_progress=lambda n: print(f"{n}/{len(cases)} classified", file=sys.stderr),
    )
    summary.update(repository=args.repository, split=args.split)
    model = summary["models"][0].replace("/", "-") if summary["models"] else "none"
    out = args.out or settings.evals_dir / "results" / f"type-{model}-{args.split}.json"
    write_json(out, summary)
    print(f"type triage on {summary['n']} {args.split} cases with {summary['models']}:")
    print(f"  accuracy {summary['accuracy']:.3f}  macro-F1 {summary['macro_f1']:.3f}")
    print(f"  cost ${summary['llm']['total']['cost_usd']:.4f}  written to {out}")
    return 0


def make_source(settings: Settings, page_size: int) -> IssueSource:
    return GitHubGraphQLSource(urllib_transport(github_token(settings)), page_size=page_size)


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    store = JsonlIssueStore(settings.data_dir)
    result = ingest_issues(
        make_source(settings, args.page_size),
        store,
        Pseudonymizer(settings.pseudonym_salt.get_secret_value()),
        args.repository,
        max_pages=args.max_pages,
        restart=args.restart,
        on_page=lambda page, stored: print(f"page {page}: {stored} issues", file=sys.stderr),
    )
    total = len(store.read_all(args.repository))
    status = "complete" if result.complete else "partial (run again to resume)"
    print(f"{args.repository}: +{result.issues} issues this run, {total} stored, {status}")
    return 0


def cmd_db_upgrade(args: argparse.Namespace, settings: Settings) -> int:
    upgrade(settings.database_url)
    print("database schema is up to date")
    return 0


def cmd_load(args: argparse.Namespace, settings: Settings) -> int:
    repository = PostgresIssueRepository(make_sessions(make_engine(settings.database_url)))
    loaded = load_issues(
        JsonlIssueStore(settings.data_dir),
        repository,
        args.repository,
        on_batch=lambda n: print(f"{n} issues loaded", file=sys.stderr),
    )
    print(f"{args.repository}: {loaded} issues loaded, {repository.count(args.repository)} in db")
    return 0


def cmd_datasets(args: argparse.Namespace, settings: Settings) -> int:
    repository = PostgresIssueRepository(make_sessions(make_engine(settings.database_url)))
    datasets = build_datasets(repository, args.repository)
    slug = args.repository.replace("/", "__")
    # Full cases contain issue text: they stay in data/ (D-003). Stats carry no text.
    out = settings.data_dir / "datasets" / slug
    write_jsonl(out / "type.jsonl", datasets.types)
    write_jsonl(out / "duplicates.jsonl", datasets.duplicates)
    write_jsonl(out / "answers.jsonl", datasets.answers)
    write_jsonl(out / "needs_info.jsonl", datasets.needs_info)
    stats = datasets.stats()
    write_json(settings.evals_dir / "datasets" / f"{slug}-stats.json", stats)
    if args.golden:
        golden = select_golden(datasets, size=args.golden)
        write_jsonl(settings.evals_dir / "datasets" / "golden" / f"{slug}-golden.jsonl", golden)
        print(f"golden sample: {len(golden)} issues")
    print(json.dumps(stats, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issue_triage")
    commands = parser.add_subparsers(dest="command", required=True)

    docs_index = commands.add_parser("docs-index", help="chunk and embed the repo's docs")
    docs_index.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    docs_index.add_argument(
        "--before",
        type=datetime.fromisoformat,
        default=None,
        help="docs as of this date (default: start of the test split)",
    )
    docs_index.set_defaults(handler=cmd_docs_index)

    issues_embed = commands.add_parser("issues-embed", help="embed every loaded issue")
    issues_embed.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    issues_embed.set_defaults(handler=cmd_issues_embed)

    eval_dups = commands.add_parser("eval-duplicates", help="score duplicate search modes")
    eval_dups.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    eval_dups.add_argument("--split", choices=["test", "train", "all"], default="test")
    eval_dups.add_argument("--k", type=int, default=10)
    eval_dups.set_defaults(handler=cmd_eval_duplicates)

    eval_rerank = commands.add_parser("eval-rerank", help="measure query rewriting and reranking")
    eval_rerank.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    eval_rerank.add_argument("--split", choices=["test", "train", "all"], default="all")
    eval_rerank.add_argument("--k", type=int, default=10)
    eval_rerank.set_defaults(handler=cmd_eval_rerank)

    eval_answers = commands.add_parser("eval-answers", help="score drafted doc-grounded answers")
    eval_answers.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    eval_answers.add_argument("--split", choices=["test", "train", "all"], default="test")
    eval_answers.add_argument("--limit", type=int, default=0)
    eval_answers.set_defaults(handler=cmd_eval_answers)

    eval_decision = commands.add_parser("eval-decision", help="classifier vs LLM type triage")
    eval_decision.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    eval_decision.set_defaults(handler=cmd_eval_decision)

    eval_replay = commands.add_parser("eval-replay", help="replay test issues end to end")
    eval_replay.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    eval_replay.add_argument("--per-group", type=int, default=25)
    eval_replay.set_defaults(handler=cmd_eval_replay)

    sandbox_poll = commands.add_parser("sandbox-poll", help="queue new sandbox issues")
    sandbox_poll.add_argument("repository", help="an allowlisted sandbox, owner/name")
    sandbox_poll.set_defaults(handler=cmd_sandbox_poll)

    agent_run = commands.add_parser("agent-run", help="replay an issue through the triage graph")
    agent_run.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    agent_run.add_argument("number", type=int)
    agent_run.set_defaults(handler=cmd_agent_run)

    agent_review = commands.add_parser("agent-review", help="approve, edit or reject a proposal")
    agent_review.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    agent_review.add_argument("number", type=int)
    verdict = agent_review.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--approve", action="store_true")
    verdict.add_argument("--reject", action="store_true")
    verdict.add_argument("--edit", metavar="TEXT", help="approve with this reply instead")
    agent_review.add_argument("--reviewer", default="maintainer")
    agent_review.set_defaults(handler=cmd_agent_review)

    eval_golden = commands.add_parser("eval-golden", help="triage on the golden sample (CI gate)")
    eval_golden.add_argument("repository", nargs="?", default="astral-sh/uv")
    eval_golden.add_argument("--check", action="store_true", help="fail on a regression")
    eval_golden.add_argument("--update", action="store_true", help="rewrite the baseline")
    eval_golden.set_defaults(handler=cmd_eval_golden)

    demo_seed = commands.add_parser("demo-seed", help="prepare a fresh database for the demo")
    demo_seed.add_argument("repository", nargs="?", default="astral-sh/uv")
    demo_seed.add_argument("--queue", type=int, default=8, help="golden issues to queue")
    demo_seed.set_defaults(handler=cmd_demo_seed)

    eval_docs = commands.add_parser("eval-docs", help="compare chunkers on doc retrieval")
    eval_docs.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    eval_docs.add_argument("--split", choices=["test", "train", "all"], default="test")
    eval_docs.add_argument("--k", type=int, default=5)
    eval_docs.set_defaults(handler=cmd_eval_docs)

    eval_triage = commands.add_parser("eval-triage", help="score type triage on a dataset split")
    eval_triage.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    eval_triage.add_argument("--split", choices=["test", "train", "all"], default="test")
    eval_triage.add_argument("--limit", type=int, default=0, help="first N cases only")
    eval_triage.add_argument("--out", type=Path, default=None)
    eval_triage.set_defaults(handler=cmd_eval_triage)

    datasets = commands.add_parser("datasets", help="build the time-split evaluation sets")
    datasets.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    datasets.add_argument("--golden", type=int, default=0, help="also write a golden sample")
    datasets.set_defaults(handler=cmd_datasets)

    db_upgrade = commands.add_parser("db-upgrade", help="apply database migrations")
    db_upgrade.set_defaults(handler=cmd_db_upgrade)

    load = commands.add_parser("load", help="load ingested issues into the database")
    load.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    load.set_defaults(handler=cmd_load)

    ingest = commands.add_parser("ingest", help="copy a GitHub repository's issues locally")
    ingest.add_argument("repository", help="owner/name, e.g. astral-sh/uv")
    ingest.add_argument("--max-pages", type=int, default=None)
    ingest.add_argument("--page-size", type=int, default=50)
    ingest.add_argument("--restart", action="store_true", help="discard stored issues first")
    ingest.set_defaults(handler=cmd_ingest)
    return parser


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.handler(args, settings or get_settings())
    return code
