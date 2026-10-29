"""Text embedders: a local sentence-transformers model (D-007) and a hashing fake for tests."""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any

TOKEN = re.compile(r"[a-z0-9]+")


class SentenceTransformerEmbedder:
    """Normalised embeddings from a local model; loaded lazily on first use."""

    def __init__(self, model_name: str, dimension: int, batch_size: int = 64) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self._batch_size = batch_size
        self._model: Any = None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        vectors = self._model.encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, vector)) for vector in vectors]


class HashingEmbedder:
    """Deterministic bag-of-words vectors (hashing trick): fast, offline, good for tests."""

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in TOKEN.findall(text.lower()):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimension
                vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors
