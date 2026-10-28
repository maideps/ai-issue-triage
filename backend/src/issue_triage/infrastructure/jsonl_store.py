"""Issue store as JSON Lines files under the data directory (git-ignored, D-003)."""

import json
import shutil
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from issue_triage.domain.issues import Comment, DuplicateLink, Issue


def issue_to_dict(issue: Issue) -> dict[str, Any]:
    data = asdict(issue)
    data["created_at"] = issue.created_at.isoformat()
    data["closed_at"] = issue.closed_at.isoformat() if issue.closed_at else None
    data["comments"] = [
        {**asdict(c), "created_at": c.created_at.isoformat()} for c in issue.comments
    ]
    return data


def issue_from_dict(data: dict[str, Any]) -> Issue:
    duplicate = data.get("duplicate_of")
    return Issue(
        repository=data["repository"],
        number=data["number"],
        title=data["title"],
        body=data["body"],
        author=data["author"],
        author_association=data["author_association"],
        state=data["state"],
        state_reason=data["state_reason"],
        created_at=datetime.fromisoformat(data["created_at"]),
        closed_at=datetime.fromisoformat(data["closed_at"]) if data["closed_at"] else None,
        labels=tuple(data["labels"]),
        duplicate_of=DuplicateLink(**duplicate) if duplicate else None,
        comments=tuple(
            Comment(
                author=c["author"],
                author_association=c["author_association"],
                body=c["body"],
                created_at=datetime.fromisoformat(c["created_at"]),
            )
            for c in data["comments"]
        ),
        comment_count=data["comment_count"],
    )


class JsonlIssueStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _dir(self, repository: str) -> Path:
        owner, name = repository.split("/", 1)
        return self._root / "github" / f"{owner}__{name}"

    def _state(self, repository: str) -> dict[str, Any]:
        path = self._dir(repository) / "state.json"
        if not path.exists():
            return {}
        state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return state

    def load_cursor(self, repository: str) -> str | None:
        cursor = self._state(repository).get("cursor")
        return str(cursor) if cursor else None

    def is_complete(self, repository: str) -> bool:
        return bool(self._state(repository).get("complete", False))

    def append(self, repository: str, issues: Sequence[Issue]) -> None:
        directory = self._dir(repository)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "issues.jsonl").open("a", encoding="utf-8") as handle:
            for issue in issues:
                handle.write(json.dumps(issue_to_dict(issue), ensure_ascii=False) + "\n")

    def save_cursor(self, repository: str, cursor: str | None, *, complete: bool) -> None:
        directory = self._dir(repository)
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "state.json.tmp"
        tmp.write_text(json.dumps({"cursor": cursor, "complete": complete}), encoding="utf-8")
        tmp.replace(directory / "state.json")

    def reset(self, repository: str) -> None:
        shutil.rmtree(self._dir(repository), ignore_errors=True)

    def read_all(self, repository: str) -> list[Issue]:
        path = self._dir(repository) / "issues.jsonl"
        if not path.exists():
            return []
        latest: dict[int, Issue] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    issue = issue_from_dict(json.loads(line))
                    latest[issue.number] = issue
        return sorted(latest.values(), key=lambda i: i.number)
