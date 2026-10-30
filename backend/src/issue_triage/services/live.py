"""Live mode (feat-016): new issues from an allowlisted sandbox repository enter the queue.

Intake comes from a GitHub webhook (``issues`` / ``opened``) or, without a public URL, from
polling the sandbox's open issues. Posting approved replies happens in the review queue
through ``SafePublisher``; nothing here can post.
"""

from collections.abc import Callable, Collection, Sequence
from typing import Any, Protocol

from issue_triage.domain.issues import Issue
from issue_triage.domain.pseudonymize import Pseudonymizer
from issue_triage.domain.sources import IssueRepository
from issue_triage.services.ingestion import pseudonymize_issue
from issue_triage.services.review_queue import ReviewQueue, RunDetail

PayloadParser = Callable[[str, dict[str, Any]], Issue]


class OpenIssueSource(Protocol):
    def open_issues(self, repository: str) -> list[Issue]: ...


class LiveIntake:
    def __init__(
        self,
        queue: ReviewQueue,
        issues: IssueRepository,
        pseudonymizer: Pseudonymizer,
        sandboxes: Collection[str],
        parse: PayloadParser,
    ) -> None:
        self._queue = queue
        self._issues = issues
        self._pseudonymizer = pseudonymizer
        self._sandboxes = {s.lower() for s in sandboxes}
        self._parse = parse

    def accepts(self, repository: str) -> bool:
        return repository.lower() in self._sandboxes

    def accept(self, issue: Issue) -> RunDetail | None:
        """Store (pseudonymized) and triage an issue from an allowlisted sandbox."""
        if not self.accepts(issue.repository):
            return None
        self._issues.upsert([pseudonymize_issue(issue, self._pseudonymizer)])
        return self._queue.enqueue(issue.repository, issue.number)

    def handle_webhook(self, event: str, payload: dict[str, Any]) -> str:
        if event == "ping":
            return "pong"
        if event != "issues" or payload.get("action") != "opened":
            return "ignored"
        repository = str(payload["repository"]["full_name"])
        if not self.accepts(repository):
            return "ignored: repository not allowlisted"
        detail = self.accept(self._parse(repository, payload["issue"]))
        return f"queued {repository}#{detail.run.number}" if detail else "ignored"

    def poll(self, source: OpenIssueSource, repository: str) -> Sequence[RunDetail]:
        """Queue every open sandbox issue that is not queued yet."""
        if not self.accepts(repository):
            return []
        queued = {(r.repository, r.number) for r in self._queue.list(limit=500)}
        details = []
        for issue in source.open_issues(repository):
            if (issue.repository, issue.number) not in queued:
                detail = self.accept(issue)
                if detail is not None:
                    details.append(detail)
        return details
