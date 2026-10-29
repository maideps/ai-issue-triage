"""pgvector-backed store and search for documentation chunks."""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from issue_triage.domain.docs import Chunker, DocChunk
from issue_triage.infrastructure.db.models import DocChunkRow


class PostgresChunkRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def replace(
        self,
        source: str,
        chunker: Chunker,
        commit: str,
        chunks: Sequence[DocChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        """Swap the whole index for (source, chunker) in one transaction."""
        if len(chunks) != len(embeddings):
            raise ValueError("one embedding per chunk")
        with self._sessions.begin() as session:
            session.execute(
                delete(DocChunkRow).where(
                    DocChunkRow.source == source, DocChunkRow.chunker == chunker
                )
            )
            session.add_all(
                DocChunkRow(
                    source=source,
                    commit=commit,
                    chunker=chunker,
                    page=chunk.page,
                    position=chunk.position,
                    heading=chunk.heading,
                    text=chunk.text,
                    embedding=list(vector),
                )
                for chunk, vector in zip(chunks, embeddings, strict=True)
            )
        return len(chunks)

    def search(
        self, source: str, chunker: Chunker, embedding: Sequence[float], k: int = 5
    ) -> list[tuple[DocChunk, float]]:
        """Top-k chunks by cosine similarity (HNSW index)."""
        distance = DocChunkRow.embedding.cosine_distance(list(embedding))
        query = (
            select(DocChunkRow, distance.label("distance"))
            .where(DocChunkRow.source == source, DocChunkRow.chunker == chunker)
            .order_by(distance)
            .limit(k)
        )
        with self._sessions() as session:
            return [
                (
                    DocChunk(row.source, row.page, chunker, row.position, row.heading, row.text),
                    round(1.0 - float(dist), 6),
                )
                for row, dist in session.execute(query)
            ]

    def count(self, source: str, chunker: Chunker) -> int:
        with self._sessions() as session:
            rows = session.scalars(
                select(DocChunkRow.id).where(
                    DocChunkRow.source == source, DocChunkRow.chunker == chunker
                )
            ).all()
            return len(rows)
