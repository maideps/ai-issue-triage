"""Deep check of one repo's ground truth: label quality and duplicate links.

Uses the authenticated `gh` CLI (GraphQL API).

    python backend/scripts/inspect_repo.py astral-sh/uv            # 500 most recent closed issues
    python backend/scripts/inspect_repo.py astral-sh/uv --sample 1000
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from statistics import median

# Label buckets tuned for astral-sh/uv; adjust per repo.
TYPE_LABELS = {"bug", "enhancement", "question", "documentation", "performance",
               "compatibility", "security", "wish"}
NEEDS_INFO_LABELS = {"needs-mre", "bot:needs-mre-comment"}
PROCESS_PREFIXES = ("bot:", "internal", "build:", "test:", "project:", "ep-")
MAINTAINER_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}
DUP_COMMENT = re.compile(
    r"(?:duplicate of|dupe of|dup of|closing in favor of|tracked in)\s+"
    r"(?:#|https://github\.com/([\w.-]+/[\w.-]+)/issues/)(\d+)",
    re.I,
)

ISSUE_FIELDS = """
  number title createdAt closedAt stateReason
  labels(first: 20) { nodes { name } }
  duplicateOf { number createdAt repository { nameWithOwner } }
  comments(last: 10) { nodes { body authorAssociation } }
"""

RECENT_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(states: CLOSED, first: 100, after: $after,
           orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { %s }
    }
  }
}""" % ISSUE_FIELDS

SEARCH_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on Issue { %s } }
  }
}""" % ISSUE_FIELDS


def graphql(query, **variables):
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            args += ["-f", f"{key}={value}"]
    out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)["data"]


def fetch_recent(owner, name, limit):
    issues, after = [], None
    while len(issues) < limit:
        page = graphql(RECENT_QUERY, owner=owner, name=name, after=after)["repository"]["issues"]
        issues += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    return issues[:limit]


def fetch_search(q):
    issues, after = [], None
    while True:
        page = graphql(SEARCH_QUERY, q=q, after=after)["search"]
        issues += [n for n in page["nodes"] if n]
        if not page["pageInfo"]["hasNextPage"]:
            return issues
        after = page["pageInfo"]["endCursor"]


def labels(issue):
    return {l["name"] for l in issue["labels"]["nodes"]}


def is_process(label):
    return label.startswith(PROCESS_PREFIXES)


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def pct(part, whole):
    return f"{part:>5} ({100 * part / whole:4.1f}%)" if whole else f"{part:>5}"


def resolve_original(issue, repo):
    """Return (source, original_repo, original_number) or None."""
    dup = issue["duplicateOf"]
    if dup:
        return "duplicateOf field", dup["repository"]["nameWithOwner"], dup["number"]
    for comment in reversed(issue["comments"]["nodes"]):
        match = DUP_COMMENT.search(comment["body"] or "")
        if match:
            return "comment text", match.group(1) or repo, int(match.group(2))
    return None


def report_labels(sample):
    n = len(sample)
    counts = Counter(l for i in sample for l in labels(i))
    typed = sum(1 for i in sample if labels(i) & TYPE_LABELS)
    area = sum(1 for i in sample if any(l.startswith("area:") for l in labels(i)))
    needs_info = sum(1 for i in sample if labels(i) & NEEDS_INFO_LABELS)
    unlabeled = sum(1 for i in sample if not labels(i))
    process_only = sum(1 for i in sample if labels(i) and all(is_process(l) for l in labels(i)))
    multi_type = sum(1 for i in sample if len(labels(i) & TYPE_LABELS) > 1)

    print("\nLABEL COVERAGE")
    print(f"  has a type label           {pct(typed, n)}   <- triage ground truth")
    print(f"    ...with >1 type label    {pct(multi_type, n)}")
    print(f"  has an area: label         {pct(area, n)}   <- routing ground truth")
    print(f"  needs-info (needs-mre)     {pct(needs_info, n)}")
    print(f"  only bot/process labels    {pct(process_only, n)}")
    print(f"  no labels at all           {pct(unlabeled, n)}")

    print("\nTYPE LABELS")
    for label in sorted(TYPE_LABELS, key=lambda l: -counts[l]):
        if counts[label]:
            print(f"  {label:28} {pct(counts[label], n)}")

    print("\nTOP AREA LABELS")
    areas = [(l, c) for l, c in counts.most_common() if l.startswith("area:")]
    for label, c in areas[:12]:
        print(f"  {label:28} {pct(c, n)}")
    print(f"  ({len(areas)} area labels used in sample)")

    other = [(l, c) for l, c in counts.most_common()
             if l not in TYPE_LABELS and not l.startswith("area:")]
    print("\nOTHER LABELS")
    for label, c in other[:10]:
        print(f"  {label:28} {pct(c, n)}")


def report_answers(sample):
    completed = [i for i in sample if i["stateReason"] == "COMPLETED"]
    answered = [i for i in completed
                if any(c["authorAssociation"] in MAINTAINER_ROLES for c in i["comments"]["nodes"])]
    questions = [i for i in sample if "question" in labels(i)]
    q_answered = [i for i in questions
                  if any(c["authorAssociation"] in MAINTAINER_ROLES for c in i["comments"]["nodes"])]
    print("\nANSWER GROUND TRUTH")
    print(f"  closed as completed        {pct(len(completed), len(sample))}")
    print(f"    ...with maintainer reply {pct(len(answered), len(completed))}")
    print(f"  'question' issues          {pct(len(questions), len(sample))}")
    print(f"    ...with maintainer reply {pct(len(q_answered), len(questions))}")


def report_duplicates(repo, duplicates):
    n = len(duplicates)
    by_source = Counter()
    same_repo = earlier = 0
    gaps = []
    for issue in duplicates:
        resolved = resolve_original(issue, repo)
        if not resolved:
            by_source["no link found"] += 1
            continue
        source, orig_repo, _ = resolved
        by_source[source] += 1
        if orig_repo.lower() != repo.lower():
            continue
        same_repo += 1
        dup = issue["duplicateOf"]
        if dup:  # creation date only available via the field
            gap = (parse_ts(issue["createdAt"]) - parse_ts(dup["createdAt"])).days
            gaps.append(gap)
            earlier += gap >= 0

    linked = n - by_source["no link found"]
    print(f"\nDUPLICATES (all {n} closed as duplicate or labelled 'duplicate')")
    for source, c in by_source.most_common():
        print(f"  {source:28} {pct(c, n)}")
    print(f"  linked to an issue in {repo.split('/')[1]:6} {pct(same_repo, linked)} of linked")
    if gaps:
        print(f"  original opened earlier    {pct(earlier, len(gaps))} of those with dates  <- valid for time-split eval")
        print(f"  median gap original->dup   {median(gaps):>5} days")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--sample", type=int, default=500)
    args = parser.parse_args()
    owner, name = args.repo.split("/")

    print(f"fetching {args.sample} recent closed issues...", file=sys.stderr)
    sample = fetch_recent(owner, name, args.sample)
    print("fetching all duplicates...", file=sys.stderr)
    dups = {i["number"]: i for q in ("reason:duplicate", "label:duplicate")
            for i in fetch_search(f"repo:{args.repo} is:issue {q}")}

    first, last = sample[-1]["createdAt"][:10], sample[0]["createdAt"][:10]
    print(f"SAMPLE  {len(sample)} closed issues of {args.repo}, created {first} -> {last}")
    for reason, c in Counter(i["stateReason"] for i in sample).most_common():
        print(f"  closed as {str(reason):17}{pct(c, len(sample))}")

    report_labels(sample)
    report_answers(sample)
    report_duplicates(args.repo, list(dups.values()))


if __name__ == "__main__":
    main()
