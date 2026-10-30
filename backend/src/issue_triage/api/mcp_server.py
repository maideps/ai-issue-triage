"""MCP server (feat-019): the search tools as read-only tools any agent can call.

Run it over stdio with ``python -m issue_triage mcp`` and register that command in an MCP
client (Claude Code, Claude Desktop, ...). Issue search keeps the as-of rule (D-004): pass
``before`` to search the tracker as it was at that moment.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from mcp.server.mcpserver import MCPServer

from issue_triage.domain.docs import DocChunk
from issue_triage.domain.issues import Issue
from issue_triage.services.answers import chunk_id

MAX_RESULTS = 20


class IssueFinder(Protocol):
    def find(
        self, title: str, body: str, *, before: datetime, exclude: int | None = None, k: int = 10
    ) -> list[int]: ...


class IssueLookup(Protocol):
    def get(self, repository: str, number: int) -> Issue | None: ...


class DocsSearch(Protocol):
    def retrieve(self, title: str, body: str) -> Sequence[DocChunk]: ...


def parse_before(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def create_mcp_server(
    repository: str,
    finder: IssueFinder,
    issues: IssueLookup,
    docs: DocsSearch,
    docs_base_url: str,
) -> MCPServer:
    server = MCPServer(
        name="ai-issue-triage",
        instructions=(
            f"Read-only search over the issue history and documentation of {repository}. "
            "Use search_similar_issues to find earlier reports of the same problem and "
            "search_docs to find the documentation section that answers a question."
        ),
    )

    @server.tool(
        description=(
            "Find earlier issues similar to the given one, most similar first. Only issues "
            "created before `before` (ISO 8601, default now) are searched; `exclude` skips "
            "an issue number (the issue itself when replaying)."
        )
    )
    def search_similar_issues(
        title: str,
        body: str = "",
        before: str | None = None,
        exclude: int | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        numbers = finder.find(
            title, body, before=parse_before(before), exclude=exclude, k=max(1, min(k, MAX_RESULTS))
        )
        results = []
        for number in numbers:
            issue = issues.get(repository, number)
            if issue is not None:
                results.append(
                    {
                        "number": number,
                        "title": issue.title,
                        "state": issue.state.lower(),
                        "created_at": issue.created_at.isoformat(),
                        "url": f"https://github.com/{repository}/issues/{number}",
                    }
                )
        return results

    @server.tool(
        description=(
            "Search the documentation for sections relevant to a question; returns citable "
            "excerpts with their page URL."
        )
    )
    def search_docs(query: str, k: int = 5) -> list[dict[str, Any]]:
        chunks = list(docs.retrieve(query, ""))[: max(1, min(k, MAX_RESULTS))]
        return [
            {
                "id": chunk_id(chunk),
                "page": chunk.page,
                "heading": chunk.heading,
                "url": f"{docs_base_url.rstrip('/')}/{chunk.page}/"
                if chunk.page
                else docs_base_url,
                "excerpt": chunk.text[:600],
            }
            for chunk in chunks
        ]

    return server
