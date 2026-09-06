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
import threading
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
        model_ref = str(model_path)
        if not model_ref.strip():
            raise ValueError("model_path must be non-empty")
        self._model_name = model_name
        self._revision = revision
        self._model_ref = model_ref
        self._device = device
        self._model: Any = None
        self._load_task: asyncio.Task[None] | None = None
        # A cancelled asyncio waiter does not stop its underlying thread.
        # This lock is held by the thread through physical load/encode completion.
        self._encode_queue = asyncio.Lock()
        self._physical_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = "cold"
        self._reason: str | None = None

    def status_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "state": self._state, "is_ready": self._state == "ready",
                "is_mock": False, "model_path": self._model_ref,
                "model_name": self._model_name,
                **({"reason": self._reason} if self._reason else {}),
            }

    def is_ready(self) -> bool:
        return bool(self.status_snapshot()["is_ready"])

    def _set_state(self, state: str, reason: str | None = None) -> None:
        with self._state_lock:
            self._state, self._reason = state, reason

    @staticmethod
    def _observe_completion(task: asyncio.Task[Any]) -> None:
        # A departed waiter must not leave an unobserved exception warning.
        if not task.cancelled():
            task.exception()

    def _load_completed(self, task: asyncio.Task[None]) -> None:
        if not task.done():
            return
        try:
            self._observe_completion(task)
        finally:
            # A completed failure can own a traceback containing the rejected
            # model. Drop only our reference, not the waiters' exception frames,
            # and never clear a newer load task installed by explicit use.
            if self._load_task is task:
                self._load_task = None

    def _load_sync(self) -> None:
        with self._physical_lock:
            try:
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(
                    self._model_ref, device=self._device,
                    local_files_only=True, trust_remote_code=True,
                )
                getter = getattr(model, "get_embedding_dimension", None) or (
                    model.get_sentence_embedding_dimension
                )
                dimension = getter()
                if isinstance(dimension, bool) or dimension != self.dim:
                    raise ValueError("WeMM model dimension must be 2048")
                self._model = model
            except Exception:
                self._set_state("failed", "wemm_load_failed")
                raise
            self._set_state("ready")

    async def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        # No await between inspecting and publishing the shared task. Completed
        # failures retry only on later explicit warmup/embedding, never status.
        if self._load_task is None or self._load_task.done():
            self._set_state("loading")
            self._load_task = asyncio.create_task(asyncio.to_thread(self._load_sync))
            self._load_task.add_done_callback(self._load_completed)
        await asyncio.shield(self._load_task)

    async def warmup(self) -> None:
        """Load this instance for startup without encoding or rebuilding vectors.

        Concurrent startup/query waiters share the existing shielded load task;
        cancelling a waiter neither duplicates nor interrupts physical loading.
        """
        await self._ensure_loaded()

    @property
    def kind(self) -> str:
        return "wemm"

    @property
    def dim(self) -> int:
        return 2048

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
        await self._ensure_loaded()
        cancelled = threading.Event()
        worker = asyncio.create_task(self._encode_owned(text, cancelled))
        worker.add_done_callback(self._observe_completion)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _encode_owned(self, text: str, cancelled: threading.Event) -> list[float]:
        # The owned task, not its cancellable caller, holds this lock until the
        # thread finishes. Queued work consumes no default-executor threads.
        async with self._encode_queue:
            if cancelled.is_set():
                return []
            return await asyncio.to_thread(self._encode, text, cancelled)

    def _encode(self, text: str, cancelled: threading.Event) -> list[float]:
        with self._physical_lock:
            # Requests cancelled while queued must not start a physical encode.
            if cancelled.is_set():
                return []  # Only consumed by the detached completion observer.
            encode_document = getattr(self._model, "encode_document", None)
            if callable(encode_document):
                vector = encode_document([text], normalize_embeddings=True)[0]
            else:
                vector = self._model.encode([text], normalize_embeddings=True)[0]
            if len(vector) != self.dim:
                raise ValueError("WeMM output dimension must be 2048")
            return [float(value) for value in vector]


__all__ = ["WeMMEmbedder"]
