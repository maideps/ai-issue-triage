"""GitHub REST calls for live mode: list a sandbox's open issues, post a comment."""

import json
import urllib.request
from datetime import datetime
from typing import Any

from issue_triage.domain.issues import Issue

API = "https://api.github.com"


def issue_from_payload(repository: str, data: dict[str, Any]) -> Issue:
    """An issue as GitHub's REST API and webhooks describe it (comments not included)."""
    user = data.get("user") or {}
    closed = data.get("closed_at")
    return Issue(
        repository=repository,
        number=int(data["number"]),
        title=data.get("title") or "",
        body=data.get("body") or "",
        author=str(user.get("login") or ""),
        author_association=data.get("author_association") or "NONE",
        state=str(data.get("state", "open")).upper(),
        state_reason=(data.get("state_reason") or None) and str(data["state_reason"]).upper(),
        created_at=datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00")),
        closed_at=datetime.fromisoformat(closed.replace("Z", "+00:00")) if closed else None,
        labels=tuple(label["name"] for label in data.get("labels", [])),
        comment_count=int(data.get("comments", 0)),
    )


class GitHubRest:
    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._token = token
        self._timeout = timeout

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"{API}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Authorization": f"bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def open_issues(self, repository: str) -> list[Issue]:
        items = self._call("GET", f"/repos/{repository}/issues?state=open&per_page=50")
        return [issue_from_payload(repository, i) for i in items if "pull_request" not in i]

    def post_comment(self, repository: str, number: int, body: str) -> str:
        created = self._call(
            "POST", f"/repos/{repository}/issues/{number}/comments", {"body": body}
        )
        return str(created["html_url"])
