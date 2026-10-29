"""Use cases: index documentation chunks and measure how well they are retrieved."""

import time
from collections.abc import Sequence
from typing import Any

from issue_triage.domain.docs import Chunker, ChunkIndex, DocPage, Embedder, chunk_page
from issue_triage.domain.evaluation import DocLinkCase
from issue_triage.services.guardrails import guard_input

QUERY_CHARS = 2000


def index_docs(
    pages: Sequence[DocPage],
    chunker: Chunker,
    embedder: Embedder,
    index: ChunkIndex,
    commit: str,
) -> int:
    if not pages:
        return 0
    chunks = [chunk for page in pages for chunk in chunk_page(page, chunker)]
    vectors = embedder.embed([chunk.text for chunk in chunks])
    return index.replace(pages[0].source, chunker, commit, chunks, vectors)


def issue_query(title: str, body: str) -> str:
    """What retrieval searches with: the redacted issue, capped in length."""
    guarded = guard_input(title, body)
    return f"{guarded.title}\n\n{guarded.body}"[:QUERY_CHARS]


def evaluate_doc_retrieval(
    cases: Sequence[DocLinkCase],
    embedder: Embedder,
    index: ChunkIndex,
    source: str,
    chunker: Chunker,
    k: int = 5,
) -> dict[str, Any]:
    """Page-level hit@1, hit@k and MRR against the pages maintainers linked."""
    vectors = embedder.embed([issue_query(c.title, c.body) for c in cases])
    hits_1 = hits_k = 0
    reciprocal = 0.0
    latencies: list[float] = []
    for case, vector in zip(cases, vectors, strict=True):
        start = time.perf_counter()
        results = index.search(source, chunker, vector, k)
        latencies.append((time.perf_counter() - start) * 1000)
        ranked_pages: list[str] = []
        for chunk, _ in results:
            if chunk.page not in ranked_pages:
                ranked_pages.append(chunk.page)
        rank = next((i for i, page in enumerate(ranked_pages, 1) if page in case.pages), None)
        if rank is not None:
            hits_k += 1
            hits_1 += rank == 1
            reciprocal += 1 / rank
    n = len(cases)
    latencies.sort()
    return {
        "chunker": chunker,
        "n": n,
        "k": k,
        "chunks": index.count(source, chunker),
        "hit_at_1": round(hits_1 / n, 4) if n else 0.0,
        f"hit_at_{k}": round(hits_k / n, 4) if n else 0.0,
        "mrr": round(reciprocal / n, 4) if n else 0.0,
        "search_ms_p50": round(latencies[n // 2], 2) if n else 0.0,
    }
