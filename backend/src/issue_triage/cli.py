"""Command line entry point: ``python -m issue_triage <command>``.

Composition root (like ``main.py``): wires infrastructure into the use cases.
"""

import argparse
import subprocess
import sys
from collections.abc import Sequence

from issue_triage.config import Settings, get_settings
from issue_triage.domain.pseudonymize import Pseudonymizer
from issue_triage.domain.sources import IssueSource
from issue_triage.infrastructure.db.engine import make_engine, make_sessions, upgrade
from issue_triage.infrastructure.db.issues import PostgresIssueRepository
from issue_triage.infrastructure.github_source import GitHubGraphQLSource, urllib_transport
from issue_triage.infrastructure.jsonl_store import JsonlIssueStore
from issue_triage.services.ingestion import ingest_issues
from issue_triage.services.loading import load_issues


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issue_triage")
    commands = parser.add_subparsers(dest="command", required=True)

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
