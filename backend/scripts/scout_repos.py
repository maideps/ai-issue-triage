"""Score candidate GitHub repos as targets for the issue-triage copilot.

Uses the authenticated `gh` CLI (search API: 30 req/min), so run `gh auth login` first.

    python backend/scripts/scout_repos.py                     # default candidates
    python backend/scripts/scout_repos.py owner/repo other/repo
"""

import json
import re
import subprocess
import sys
import time
from datetime import date, timedelta

DEFAULT_CANDIDATES = [
    "pydantic/pydantic",
    "astral-sh/uv",
    "astral-sh/ruff",
    "fastapi/fastapi",
    "encode/httpx",
    "streamlit/streamlit",
    "supabase/supabase",
    "pola-rs/polars",
    "langchain-ai/langgraph",
    "prisma/prisma",
    "microsoft/playwright",
    "vitejs/vite",
]

SEARCH_PAUSE_S = 2.2  # stay under 30 search requests/minute
TYPE_LABEL = re.compile(r"bug|feature|enhancement|question|doc|support|help", re.I)
DOC_PATH = re.compile(r"(^|/)(docs?|documentation|website|guides?)/.+\.(md|mdx|rst)$", re.I)


def gh(path, **params):
    args = ["gh", "api", "-X", "GET", path]
    for key, value in params.items():
        args += ["-f", f"{key}={value}"]
    out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)


def count(query):
    time.sleep(SEARCH_PAUSE_S)
    return gh("search/issues", q=query, per_page=1)["total_count"]


def scout(repo):
    meta = gh(f"repos/{repo}")
    one_year_ago = (date.today() - timedelta(days=365)).isoformat()

    issues = count(f"repo:{repo} is:issue")
    unlabeled = count(f"repo:{repo} is:issue no:label")
    last_year = count(f"repo:{repo} is:issue created:>={one_year_ago}")
    duplicates = max(
        count(f"repo:{repo} is:issue reason:duplicate"),
        count(f"repo:{repo} is:issue label:duplicate"),
    )

    labels = gh(f"repos/{repo}/labels", per_page=100)
    type_labels = [label["name"] for label in labels if TYPE_LABEL.search(label["name"])]

    tree = gh(f"repos/{repo}/git/trees/{meta['default_branch']}", recursive=1)
    doc_files = sum(1 for node in tree.get("tree", []) if DOC_PATH.search(node["path"]))

    return {
        "repo": repo,
        "language": meta.get("language") or "-",
        "issues": issues,
        "labeled_pct": round(100 * (issues - unlabeled) / issues) if issues else 0,
        "last_year": last_year,
        "duplicates": duplicates,
        "doc_files": doc_files,
        "discussions": meta.get("has_discussions", False),
        "type_labels": type_labels[:6],
    }


def fit(r):
    """Rough 0-5 score against the selection criteria."""
    return sum(
        [
            3_000 <= r["issues"] <= 25_000,
            r["labeled_pct"] >= 60,
            r["last_year"] >= 500,
            r["duplicates"] >= 100,
            r["doc_files"] >= 30,
        ]
    )


def main():
    repos = sys.argv[1:] or DEFAULT_CANDIDATES
    rows = []
    for repo in repos:
        print(f"scouting {repo}...", file=sys.stderr)
        try:
            rows.append(scout(repo))
        except RuntimeError as err:
            print(f"  skipped: {err}", file=sys.stderr)

    rows.sort(key=lambda r: (fit(r), r["duplicates"]), reverse=True)
    header = (
        f"{'repo':26} {'lang':11} {'issues':>7} {'lab%':>5} {'12mo':>6} "
        f"{'dups':>5} {'docs':>5} {'disc':>5} {'fit':>4}  type labels"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['repo']:26} {r['language'][:11]:11} {r['issues']:>7} {r['labeled_pct']:>5} "
            f"{r['last_year']:>6} {r['duplicates']:>5} {r['doc_files']:>5} "
            f"{'yes' if r['discussions'] else 'no':>5} {fit(r):>3}/5  {', '.join(r['type_labels'])}"
        )


if __name__ == "__main__":
    main()
