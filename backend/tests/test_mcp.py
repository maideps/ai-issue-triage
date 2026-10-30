"""The MCP tools, called through a real MCP client connected in memory (feat-019)."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer

from issue_triage.api.mcp_server import create_mcp_server, parse_before
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.infrastructure.keyword_index import Bm25IssueIndex
from issue_triage.services.answers import DocRetriever
from issue_triage.services.docs_index import index_docs
from issue_triage.services.duplicates import SimilarIssueFinder

from .test_answers import PAGES, MemoryIndex
from .test_duplicates import ISSUES, REPO, TEXTS, ListVectorIndex

BASE = "https://docs.astral.sh/uv/"


class Lookup:
    def get(self, repository: str, number: int) -> Any:
        return next((i for i in ISSUES if i.repository == repository and i.number == number), None)


def server() -> MCPServer:
    vectors = ListVectorIndex(ISSUES)
    finder = SimilarIssueFinder(REPO, vectors.embedder, vectors, Bm25IssueIndex(ISSUES))
    embedder = HashingEmbedder()
    index = MemoryIndex()
    index_docs(PAGES, "heading", embedder, index, "abc")
    docs = DocRetriever(embedder, index, "acme/widgets", k=5)
    return create_mcp_server(REPO, finder, Lookup(), docs, BASE)


def call(name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        async with Client(server()) as client:
            result = await client.call_tool(name, arguments)
            assert not result.is_error, result
            structured = result.structured_content
            if structured is not None:
                items: list[dict[str, Any]] = structured.get("result", structured)
                return items
            return [json.loads(block.text) for block in result.content]  # type: ignore[union-attr]

    return asyncio.run(run())


def test_tools_are_listed_with_descriptions() -> None:
    async def run() -> dict[str, str]:
        async with Client(server()) as client:
            tools = await client.list_tools()
            return {t.name: t.description or "" for t in tools.tools}

    tools = asyncio.run(run())

    assert set(tools) == {"search_similar_issues", "search_docs"}
    assert "before" in tools["search_similar_issues"]


def test_similar_issue_search_respects_before_and_exclude() -> None:
    title, body = TEXTS[4]
    as_of = ISSUES[3].created_at.isoformat()

    replay = call(
        "search_similar_issues", {"title": title, "body": body, "before": as_of, "exclude": 4}
    )
    today = call("search_similar_issues", {"title": title, "body": body, "k": 10})

    assert replay[0]["number"] == 1
    assert all(r["number"] < 4 for r in replay)
    assert replay[0]["url"] == f"https://github.com/{REPO}/issues/1"
    assert 4 in {r["number"] for r in today}  # without `before`, newer issues are visible


def test_docs_search_returns_citable_excerpts() -> None:
    results = call("search_docs", {"query": "pin a python version", "k": 1})

    assert len(results) == 1
    assert results[0]["id"] == "concepts/python-versions#0"
    assert results[0]["url"] == f"{BASE}concepts/python-versions/"
    assert "uv python pin" in results[0]["excerpt"]


def test_parse_before_defaults_to_utc() -> None:
    assert parse_before("2025-01-02T03:04:05").tzinfo == UTC
    assert parse_before("2025-01-02T03:04:05Z") == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert (datetime.now(UTC) - parse_before(None)).total_seconds() < 5
