# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WeMM-Embedding-2B production embedder (user-selected vector model).

Loads ``tencent/WeMM-Embedding-2B`` strictly from local resources (no runtime
download) through sentence-transformers, mirroring the Memory SDK's
``BGEM3Embedder`` contract: ``kind``/``dim``/``lineage`` plus async
``embed``.  Output vectors are 2048-dim L2-normalized; the repo ships remote
code, so loading pins ``trust_remote_code`` to the locally vendored snapshot
only (``local_files_only=True``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from simple_harness_memory.embedders.base import Embedder, EmbeddingLineage


class WeMMEmbedder(Embedder):
    """WeMM-Embedding-2B loaded from a local snapshot; never downloads."""

    def __init__(
        self,
        model_path: str | Path,
        device: Any = None,
        *,
        revision: str = "local-cache",
        model_name: str = "tencent/WeMM-Embedding-2B",
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ImportError("WeMMEmbedder requires sentence-transformers") from exc
        model_ref = str(model_path)
        if not model_ref.strip():
            raise ValueError("model_path must be non-empty")
        self._model_name = model_name
        self._revision = revision
        self._model = SentenceTransformer(
            model_ref,
            device=device,
            local_files_only=True,
            trust_remote_code=True,
        )
        self._lock = asyncio.Lock()

    @property
    def kind(self) -> str:
        return "wemm"

    @property
    def dim(self) -> int:
        getter = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        return int(getter() or 2048)

    @property
    def lineage(self) -> EmbeddingLineage:
        return EmbeddingLineage(
            kind=self.kind,
            provider="local",
            model=self._model_name,
            revision=self._revision,
            dimension=self.dim,
            normalization="l2",
            format_fingerprint=f"wemm-embedding-2b:{self._revision}:{self.dim}",
        )

    async def embed(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        async with self._lock:
            vector = await asyncio.to_thread(self._encode, text)
        return [float(value) for value in vector]

    def _encode(self, text: str):
        encode_document = getattr(self._model, "encode_document", None)
        if callable(encode_document):
            return encode_document([text], normalize_embeddings=True)[0]
        return self._model.encode([text], normalize_embeddings=True)[0]


__all__ = ["WeMMEmbedder"]
