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
import logging
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from simple_harness_memory.embedders.base import Embedder, EmbeddingLineage

log = logging.getLogger(__name__)
# Fixed startup input: no conversation, query, identity, or memory content.
_PRIMING_TEXT = "这是一条用于初始化文本编码器的固定测试句子。"
# One physical ``encode`` pads every input in the call to the longest one, so its
# cost tracks ``longest * count``, not the sum of the lengths.  Measured on this
# machine (WeMM-Embedding-2B / mps, both paths warm):
#   6 texts of 20-58 chars   batched 322 ms vs serial 1 430 ms  (4.4x faster)
#   8 texts of 600 chars     batched 2 014 ms vs serial 2 192 ms (0.92x)
#   54/108/1682 in one call  batched 2 084 ms vs serial 1 151 ms (1.8x SLOWER)
# i.e. the win is the shared per-call fixed cost (~230 ms), and it is gone once
# the longest member is big enough for compute to dominate — at which point the
# padding of its shorter neighbours is pure loss.  So the padded work of one call
# is capped in that fixed-cost-dominated region; anything longer is encoded alone
# and therefore never costs more than the serial loop it replaces.
_BATCH_MAX_PADDED_CHARS = 1_024
_BATCH_MAX_ITEMS = 32


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
        self._warmup_task: asyncio.Task[None] | None = None
        self._warmup_state = "not_started"
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
                "warmup_state": self._warmup_state, "is_primed": self._warmup_state == "ready",
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
        """Load and prime this instance once, without writing business vectors.

        Waiter cancellation leaves the owned load/encode task running. Priming
        uses the ordinary physical encode queue; no second model or query call.
        """
        if self.status_snapshot()["is_primed"]:
            return
        if self._warmup_task is None or self._warmup_task.done():
            self._warmup_task = asyncio.create_task(self._warmup_owned())
            self._warmup_task.add_done_callback(self._warmup_completed)
        await asyncio.shield(self._warmup_task)

    def _warmup_completed(self, task: asyncio.Task[None]) -> None:
        try:
            self._observe_completion(task)
        finally:
            if self._warmup_task is task:
                self._warmup_task = None

    async def _warmup_owned(self) -> None:
        with self._state_lock:
            self._warmup_state = "loading"
        phase, started = "load", time.monotonic()
        try:
            await self._ensure_loaded()
            log.info("wemm_warmup_phase_completed phase=load elapsed_ms=%.3f",
                     (time.monotonic() - started) * 1000)
            with self._state_lock:
                self._warmup_state = "priming"
            phase, started = "prime", time.monotonic()
            # Never goes through MemoryManager, generation, or a query audit.
            # The owned task retains the same queue through physical completion.
            await self._encode_owned(_PRIMING_TEXT, threading.Event())
            with self._state_lock:
                self._warmup_state = "ready"
            log.info("wemm_warmup_phase_completed phase=prime elapsed_ms=%.3f",
                     (time.monotonic() - started) * 1000)
        except BaseException:
            with self._state_lock:
                self._warmup_state = "failed"
            log.warning("wemm_warmup_phase_failed phase=%s elapsed_ms=%.3f",
                        phase, (time.monotonic() - started) * 1000)
            raise

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

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """One physical model call per group instead of the base class's N calls.

        The base ``Embedder.embed_batch`` is ``[await self.embed(t) for t in
        texts]`` — N sequential encodes, each paying the model's fixed
        per-invocation cost (~230 ms warm on this machine, and it dominates
        anything short).  Generation rebuilds call this with every short-horizon
        chunk at once, so that fixed cost was multiplied by the chunk count while
        the SDK held its write lock.

        Vectors are the model's own output for the same text, returned in input
        order.  Grouping is only a call boundary chosen from measured cost (see
        ``_encode_groups``); it never changes, truncates or reorders any input.
        """
        # The SDK contract passes a list; accept any concrete sequence but never
        # a bare string, which would silently embed each character.
        if (isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence)
                or any(not isinstance(text, str) for text in texts)):
            raise TypeError("texts must be a sequence of strings")
        if not texts:
            return []
        await self._ensure_loaded()
        cancelled = threading.Event()
        worker = asyncio.create_task(self._encode_many_owned(list(texts), cancelled))
        worker.add_done_callback(self._observe_completion)
        try:
            vectors = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        self.validate_vectors(vectors, expected_count=len(texts))
        return vectors

    @staticmethod
    def _encode_groups(texts: list[str]) -> list[tuple[int, ...]]:
        """Group input positions so one call's padded work stays bounded.

        A naive "pack consecutive texts by total length" grouping measured 1.39x
        *slower* than the serial loop on the observed field distribution
        (54/108/1682/7798/19655/29778 chars): the short chunks were padded up to
        the 7 798-char one and paid for four times over.

        So the ceiling is on ``longest * count`` — the work the model actually
        does — and positions are visited shortest-first so short chunks pack with
        short chunks.  The sort is stable on ``(length, position)``, and results
        are scattered back to input positions, so both grouping and output order
        are deterministic and independent of the input's order.
        """
        groups: list[tuple[int, ...]] = []
        current: list[int] = []
        longest = 0
        for index in sorted(range(len(texts)), key=lambda position: (len(texts[position]), position)):
            padded = max(longest, len(texts[index]))
            if current and (padded * (len(current) + 1) > _BATCH_MAX_PADDED_CHARS
                            or len(current) >= _BATCH_MAX_ITEMS):
                groups.append(tuple(current))
                current, padded = [], len(texts[index])
            current.append(index)
            longest = padded
        if current:
            groups.append(tuple(current))
        return groups

    async def _encode_many_owned(
        self, texts: list[str], cancelled: threading.Event
    ) -> list[list[float]]:
        # Same ownership discipline as ``_encode_owned``: the physical queue is
        # held by this task across every group, so a cancelled caller cannot
        # interleave another encode into the middle of one batch.
        async with self._encode_queue:
            if cancelled.is_set():
                return []
            vectors: list[list[float] | None] = [None] * len(texts)
            for group in self._encode_groups(texts):
                if cancelled.is_set():
                    return []
                encoded = await asyncio.to_thread(
                    self._encode_group, [texts[index] for index in group], cancelled
                )
                if not encoded:
                    return []  # cancelled while queued
                for index, vector in zip(group, encoded):
                    vectors[index] = vector
            return _require_complete(vectors)

    async def _encode_owned(self, text: str, cancelled: threading.Event) -> list[float]:
        # The owned task, not its cancellable caller, holds this lock until the
        # thread finishes. Queued work consumes no default-executor threads.
        async with self._encode_queue:
            if cancelled.is_set():
                return []
            return await asyncio.to_thread(self._encode, text, cancelled)

    def _encode(self, text: str, cancelled: threading.Event) -> list[float]:
        vectors = self._encode_group([text], cancelled)
        return vectors[0] if vectors else []

    def _encode_group(
        self, texts: list[str], cancelled: threading.Event
    ) -> list[list[float]]:
        with self._physical_lock:
            # Requests cancelled while queued must not start a physical encode.
            if cancelled.is_set():
                return []  # Only consumed by the detached completion observer.
            encode_document = getattr(self._model, "encode_document", None)
            if callable(encode_document):
                encoded = encode_document(texts, normalize_embeddings=True)
            else:
                encoded = self._model.encode(texts, normalize_embeddings=True)
            if len(encoded) != len(texts):
                raise ValueError("WeMM output count must match input count")
            vectors = []
            for vector in encoded:
                if len(vector) != self.dim:
                    raise ValueError("WeMM output dimension must be 2048")
                vectors.append([float(value) for value in vector])
            return vectors


def _require_complete(vectors: list[list[float] | None]) -> list[list[float]]:
    """Every input position must have been filled by exactly one group."""
    if any(vector is None for vector in vectors):
        raise ValueError("WeMM batch left an input unencoded")
    return [vector for vector in vectors if vector is not None]


__all__ = ["WeMMEmbedder"]
