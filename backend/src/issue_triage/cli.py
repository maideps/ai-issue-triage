"""Command line entry point: ``python -m issue_triage <command>``.

Composition root (like ``main.py``): wires infrastructure into the use cases.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from issue_triage.config import Settings, get_settings
from issue_triage.domain.docs import CHUNKERS, Embedder
from issue_triage.domain.evaluation import (
    AnswerCase,
    DuplicateCase,
    TypeCase,
    build_doc_link_cases,
)
from issue_triage.domain.pseudonymize import Pseudonymizer
from issue_triage.domain.sources import IssueSource
from issue_triage.infrastructure.checkpointer import postgres_checkpointer
from issue_triage.infrastructure.dataset_files import read_cases, write_json, write_jsonl
from issue_triage.infrastructure.db.chunks import PostgresChunkRepository
from issue_triage.infrastructure.db.engine import make_engine, make_sessions, upgrade
from issue_triage.infrastructure.db.issue_vectors import PostgresIssueVectorIndex
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.docs_source import GitHubDocsSource
from issue_triage.infrastructure.embeddings import SentenceTransformerEmbedder
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
from issue_triage.services.triage import Triager
from issue_triage.wiring import build_agent_deps, build_llm_client


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


def github_token(settings: Settings) -> str:
    if settings.github_token is not None:
        return settings.github_token.get_secret_value()
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise SystemExit("No GitHub token: set ISSUE_TRIAGE_GITHUB_TOKEN or run `gh auth login`.")
    return token


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
