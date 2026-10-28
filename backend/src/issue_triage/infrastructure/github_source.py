"""GitHub GraphQL implementation of the issue source."""

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from issue_triage.domain.issues import Comment, DuplicateLink, Issue
from issue_triage.domain.sources import IssuePage

GRAPHQL_URL = "https://api.github.com/graphql"

ISSUES_QUERY = """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    issues(first: $first, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body createdAt closedAt state stateReason authorAssociation
        author { login }
        labels(first: 20) { nodes { name } }
        duplicateOf { number repository { nameWithOwner } }
        comments(first: 50) {
          totalCount
          nodes { body createdAt authorAssociation author { login } }
        }
      }
    }
  }
}
"""

Json = dict[str, Any]
Transport = Callable[[str, Json], Json]


class GitHubError(RuntimeError):
    """The API answered with errors that retrying will not fix."""


class TransientGitHubError(RuntimeError):
    """A temporary failure (server error, secondary rate limit, network)."""


def urllib_transport(token: str, *, timeout: float = 60.0) -> Transport:
    """Send GraphQL requests with the standard library; no extra dependency."""

    def post(query: str, variables: Json) -> Json:
        request = urllib.request.Request(
            GRAPHQL_URL,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload: Json = json.loads(response.read().decode("utf-8"))
                return payload
        except urllib.error.HTTPError as error:
            if error.code >= 500 or error.code in (403, 429):
                raise TransientGitHubError(f"HTTP {error.code}") from error
            raise GitHubError(f"HTTP {error.code}: {error.read()[:200]!r}") from error
        except (OSError, http.client.HTTPException) as error:
            # URLError, timeouts, connection resets mid-read, truncated responses.
            raise TransientGitHubError(f"{type(error).__name__}: {error}") from error

    return post


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _login(node: Json) -> str:
    author = node.get("author")
    return str(author["login"]) if author else ""


def parse_issue(repository: str, node: Json) -> Issue:
    duplicate = node.get("duplicateOf")
    comments = node.get("comments") or {"totalCount": 0, "nodes": []}
    created = _ts(node["createdAt"])
    if created is None:
        raise GitHubError(f"issue {node.get('number')} has no createdAt")
    return Issue(
        repository=repository,
        number=int(node["number"]),
        title=node.get("title") or "",
        body=node.get("body") or "",
        author=_login(node),
        author_association=node.get("authorAssociation") or "NONE",
        state=node["state"],
        state_reason=node.get("stateReason"),
        created_at=created,
        closed_at=_ts(node.get("closedAt")),
        labels=tuple(label["name"] for label in node["labels"]["nodes"]),
        duplicate_of=DuplicateLink(
            repository=duplicate["repository"]["nameWithOwner"], number=int(duplicate["number"])
        )
        if duplicate
        else None,
        comments=tuple(
            Comment(
                author=_login(c),
                author_association=c.get("authorAssociation") or "NONE",
                body=c.get("body") or "",
                created_at=_ts(c["createdAt"]) or created,
            )
            for c in comments["nodes"]
        ),
        comment_count=int(comments["totalCount"]),
    )


class GitHubGraphQLSource:
    """Reads issues oldest first, waiting for the rate-limit reset when the budget runs low."""

    def __init__(
        self,
        transport: Transport,
        *,
        page_size: int = 50,
        min_remaining: int = 100,
        max_retries: int = 8,
        max_backoff: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._transport = transport
        self._page_size = page_size
        self._min_remaining = min_remaining
        self._max_retries = max_retries
        self._max_backoff = max_backoff
        self._sleep = sleep
        self._now = now
        self._wait_until: datetime | None = None

    def fetch_page(self, repository: str, cursor: str | None) -> IssuePage:
        owner, name = repository.split("/", 1)
        if self._wait_until is not None:
            delay = (self._wait_until - self._now()).total_seconds()
            if delay > 0:
                self._sleep(delay + 1)
            self._wait_until = None

        variables: Json = {"owner": owner, "name": name, "first": self._page_size}
        if cursor:
            variables["after"] = cursor
        payload = self._post_with_retries(variables)

        if payload.get("errors"):
            raise GitHubError(json.dumps(payload["errors"])[:500])
        data = payload["data"]
        if data.get("repository") is None:
            raise GitHubError(f"repository {repository} not found")
        limit = data.get("rateLimit") or {}
        if limit and int(limit["remaining"]) < self._min_remaining:
            self._wait_until = _ts(limit["resetAt"])

        connection = data["repository"]["issues"]
        info = connection["pageInfo"]
        return IssuePage(
            issues=tuple(parse_issue(repository, node) for node in connection["nodes"]),
            next_cursor=info["endCursor"],
            has_next_page=bool(info["hasNextPage"]),
        )

    def _post_with_retries(self, variables: Json) -> Json:
        for attempt in range(self._max_retries + 1):
            try:
                return self._transport(ISSUES_QUERY, variables)
            except TransientGitHubError:
                if attempt == self._max_retries:
                    raise
                self._sleep(min(2.0**attempt, self._max_backoff))
        raise AssertionError("unreachable")
