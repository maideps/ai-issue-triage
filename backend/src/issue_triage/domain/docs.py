"""Documentation pages and the two chunking strategies compared in feat-008."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Chunker = Literal["heading", "fixed"]
CHUNKERS: tuple[Chunker, ...] = ("heading", "fixed")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class DocPage:
    source: str  # e.g. "astral-sh/uv"
    path: str  # relative to the docs root, e.g. "concepts/cache.md"
    title: str
    text: str

    @property
    def key(self) -> str:
        """Stable page id matching docs URLs: "concepts/cache", "concepts/projects"."""
        return page_key(self.path)


@dataclass(frozen=True)
class DocChunk:
    source: str
    page: str  # DocPage.key
    chunker: Chunker
    position: int
    heading: str
    text: str


class ChunkIndex(Protocol):
    def replace(
        self,
        source: str,
        chunker: Chunker,
        commit: str,
        chunks: Sequence[DocChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int: ...

    def search(
        self, source: str, chunker: Chunker, embedding: Sequence[float], k: int = 5
    ) -> list[tuple[DocChunk, float]]: ...

    def count(self, source: str, chunker: Chunker) -> int: ...


class Embedder(Protocol):
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def page_key(path: str) -> str:
    key = path.split("#", 1)[0].strip("/")
    key = re.sub(r"\.md$", "", key)
    key = re.sub(r"(^|/)index$", "", key)
    return key.strip("/")


def page_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = HEADING.match(line)
        if match and len(match.group(1)) == 1:
            return match.group(2)
    return fallback


def _sections(text: str) -> list[tuple[list[str], str]]:
    """Split markdown at headings outside code fences: [(heading path, body)]."""
    sections: list[tuple[list[str], str]] = []
    path: list[str] = []
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match:
            if "".join(lines).strip():
                sections.append((list(path), "\n".join(lines).strip()))
            level = len(match.group(1))
            path = [*path[: level - 1], match.group(2)]
            lines = []
        else:
            lines.append(line)
    if "".join(lines).strip():
        sections.append((list(path), "\n".join(lines).strip()))
    return sections


def _split_paragraphs(text: str, max_chars: int) -> list[str]:
    """Group paragraphs up to max_chars; a single oversized paragraph stays whole."""
    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if current and len(candidate) > max_chars:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return parts


def heading_chunks(page: DocPage, max_chars: int = 1500) -> list[DocChunk]:
    """One chunk per section, prefixed with its breadcrumb so it stands on its own."""
    chunks: list[DocChunk] = []
    for path, body in _sections(page.text):
        breadcrumb = " > ".join([page.title, *[p for p in path if p != page.title]])
        for part in _split_paragraphs(body, max_chars):
            chunks.append(
                DocChunk(
                    page.source,
                    page.key,
                    "heading",
                    len(chunks),
                    breadcrumb,
                    f"{breadcrumb}\n\n{part}",
                )
            )
    return chunks


def fixed_chunks(page: DocPage, size: int = 1000, overlap: int = 200) -> list[DocChunk]:
    """Sliding character windows over the whole page, ignoring its structure."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = page.text.strip()
    chunks: list[DocChunk] = []
    start = 0
    while start < len(text):
        window = text[start : start + size]
        chunks.append(
            DocChunk(
                page.source, page.key, "fixed", len(chunks), page.title, f"{page.title}\n\n{window}"
            )
        )
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


def chunk_page(page: DocPage, chunker: Chunker) -> list[DocChunk]:
    return heading_chunks(page) if chunker == "heading" else fixed_chunks(page)
