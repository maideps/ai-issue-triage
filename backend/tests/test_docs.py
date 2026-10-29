import io
import json
import tarfile
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.docs import DocChunk, DocPage, fixed_chunks, heading_chunks, page_key
from issue_triage.domain.evaluation import AnswerCase, build_doc_link_cases, docs_link_pages
from issue_triage.infrastructure.db.chunks import PostgresChunkRepository
from issue_triage.infrastructure.docs_source import GitHubDocsSource
from issue_triage.infrastructure.embeddings import HashingEmbedder
from issue_triage.services.docs_index import evaluate_doc_retrieval, index_docs

BASE = "https://docs.astral.sh/uv/"
SOURCE = "acme/widgets"

CACHE_PAGE = DocPage(
    SOURCE,
    "concepts/cache.md",
    "Caching",
    "# Caching\n\nuv caches wheels.\n\n## Cache directory\n\nSet UV_CACHE_DIR.\n\n"
    "```bash\n# not a heading\nuv cache dir\n```\n\n## Cleaning\n\nRun uv cache clean.",
)
PYTHON_PAGE = DocPage(
    SOURCE,
    "concepts/python-versions/index.md",
    "Python versions",
    "# Python versions\n\nPin with .python-version or uv python pin.",
)


def test_page_keys_match_docs_urls() -> None:
    assert page_key("concepts/cache.md") == "concepts/cache"
    assert page_key("concepts/projects/index.md") == "concepts/projects"
    assert page_key("/reference/settings/#index-url") == "reference/settings"
    assert page_key("index.md") == ""


def test_heading_chunks_follow_sections_and_ignore_headings_in_code() -> None:
    chunks = heading_chunks(CACHE_PAGE)

    assert [c.heading for c in chunks] == [
        "Caching",
        "Caching > Cache directory",
        "Caching > Cleaning",
    ]
    assert "# not a heading" in chunks[1].text
    assert all(c.page == "concepts/cache" and c.chunker == "heading" for c in chunks)
    assert chunks[1].text.startswith("Caching > Cache directory\n\n")


def test_heading_chunks_split_long_sections_by_paragraph() -> None:
    page = DocPage(SOURCE, "long.md", "Long", "# Long\n\n" + "\n\n".join(["word " * 60] * 10))

    chunks = heading_chunks(page, max_chars=700)

    assert len(chunks) > 1
    assert all(len(c.text) <= 700 + len("Long\n\n") + 310 for c in chunks)


def test_fixed_chunks_overlap_and_cover_the_page() -> None:
    page = DocPage(SOURCE, "a.md", "A", "x" * 2500)

    chunks = fixed_chunks(page, size=1000, overlap=200)

    assert len(chunks) == 3
    assert [c.position for c in chunks] == [0, 1, 2]
    with pytest.raises(ValueError):
        fixed_chunks(page, size=100, overlap=100)


def test_docs_links_become_page_keys() -> None:
    text = (
        f"See {BASE}concepts/cache/#cache-directory and {BASE}concepts/cache/ "
        f"plus [settings]({BASE}reference/settings/#index-url) and https://example.com/x"
    )

    assert docs_link_pages(text, BASE) == ("concepts/cache", "reference/settings")


def test_doc_link_cases_keep_only_linked_answers() -> None:
    as_of = datetime(2025, 9, 20, tzinfo=UTC)
    answers = [
        AnswerCase(SOURCE, 1, as_of, "Cache?", "where", "test", f"Read {BASE}concepts/cache/"),
        AnswerCase(SOURCE, 2, as_of, "Other", "q", "test", "No link here"),
    ]

    cases = build_doc_link_cases(answers, BASE)

    assert [(c.number, c.pages) for c in cases] == [(1, ("concepts/cache",))]


class FakeIndex:
    """In-memory ChunkIndex with exact cosine search."""

    def __init__(self) -> None:
        self.rows: list[tuple[DocChunk, list[float]]] = []

    def replace(
        self,
        source: str,
        chunker: str,
        commit: str,
        chunks: Sequence[DocChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        self.rows = [(c, list(e)) for c, e in zip(chunks, embeddings, strict=True)]
        return len(chunks)

    def search(
        self, source: str, chunker: str, embedding: Sequence[float], k: int = 5
    ) -> list[tuple[DocChunk, float]]:
        scored = [(c, sum(a * b for a, b in zip(e, embedding, strict=True))) for c, e in self.rows]
        return sorted(scored, key=lambda item: -item[1])[:k]

    def count(self, source: str, chunker: str) -> int:
        return len(self.rows)


def test_retrieval_metrics_rank_the_linked_page() -> None:
    index = FakeIndex()
    embedder = HashingEmbedder()
    index_docs([CACHE_PAGE, PYTHON_PAGE], "heading", embedder, index, "abc")
    as_of = datetime(2025, 9, 20, tzinfo=UTC)
    answers = [
        AnswerCase(
            SOURCE, 1, as_of, "uv cache clean", "cache dir", "test", f"{BASE}concepts/cache/"
        ),
        AnswerCase(
            SOURCE,
            2,
            as_of,
            "python pin",
            "python version",
            "test",
            f"{BASE}concepts/python-versions/",
        ),
        AnswerCase(SOURCE, 3, as_of, "unrelated", "zzz", "test", f"{BASE}reference/cli/"),
    ]

    result = evaluate_doc_retrieval(
        build_doc_link_cases(answers, BASE),
        embedder,
        index,
        SOURCE,
        "heading",
        k=2,
    )

    assert result["n"] == 3 and result["chunks"] == 4
    assert result["hit_at_1"] == pytest.approx(2 / 3, abs=1e-3)
    assert result["hit_at_2"] == pytest.approx(2 / 3, abs=1e-3)
    assert result["mrr"] == pytest.approx(2 / 3, abs=1e-3)


def test_hashing_embedder_is_normalised_and_deterministic() -> None:
    embedder = HashingEmbedder(dimension=64)
    first, second = embedder.embed(["uv cache clean", "uv cache clean"])

    assert first == second
    assert sum(v * v for v in first) == pytest.approx(1.0)


def test_pgvector_index_round_trip_and_search(sessions: sessionmaker[Session]) -> None:
    repository = PostgresChunkRepository(sessions)
    embedder = HashingEmbedder()

    count = index_docs([CACHE_PAGE, PYTHON_PAGE], "heading", embedder, repository, "abc123")
    results = repository.search(SOURCE, "heading", embedder.embed(["python pin version"])[0], k=2)

    assert count == repository.count(SOURCE, "heading") == 4
    assert results[0][0].page == "concepts/python-versions"
    assert -1.0 <= results[1][1] <= results[0][1] <= 1.0
    # Re-indexing replaces instead of duplicating.
    index_docs([PYTHON_PAGE], "heading", embedder, repository, "def456")
    assert repository.count(SOURCE, "heading") == 1


def _tarball(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_docs_source_extracts_only_markdown_under_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _tarball(
        {
            "acme-widgets-abc/docs/concepts/cache.md": "# Caching\n\ntext",
            "acme-widgets-abc/docs/index.md": "# Home\n\nwelcome",
            "acme-widgets-abc/docs/assets/logo.svg": "<svg/>",
            "acme-widgets-abc/src/main.rs": "fn main() {}",
            "acme-widgets-abc/docs/../../escape.md": "# nope",
        }
    )
    requested: list[str] = []

    def urlopen(request: urllib.request.Request, timeout: float = 0) -> FakeResponse:
        requested.append(request.full_url)
        if "/commits?" in request.full_url:
            return FakeResponse(json.dumps([{"sha": "abc123def4567890"}]).encode())
        return FakeResponse(archive)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    source = GitHubDocsSource(tmp_path, "token", sleep=lambda s: None)

    sha, pages = source.snapshot(SOURCE, datetime(2025, 9, 12, tzinfo=UTC))
    again_sha, _ = source.snapshot(SOURCE, datetime(2025, 9, 12, tzinfo=UTC))

    assert sha == again_sha == "abc123def4567890"
    assert sorted(p.path for p in pages) == ["concepts/cache.md", "index.md"]
    assert {p.title for p in pages} == {"Caching", "Home"}
    assert not (tmp_path / "escape.md").exists()
    assert sum("/tarball/" in url for url in requested) == 1  # cached after the first call


def test_docs_source_retries_transient_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    def flaky(request: urllib.request.Request, timeout: float = 0) -> Any:
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionResetError(10054, "reset")
        return FakeResponse(json.dumps([{"sha": "f" * 40}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    sleeps: list[float] = []

    sha = GitHubDocsSource(tmp_path, "t", sleep=sleeps.append).commit_before(
        SOURCE, datetime(2025, 1, 1, tzinfo=UTC)
    )

    assert sha == "f" * 40 and sleeps == [1.0, 2.0]
