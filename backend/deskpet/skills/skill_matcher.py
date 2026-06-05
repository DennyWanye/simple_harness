# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""SkillMatcher — embedding-based skill similarity matching (WI-4.1).

Computes cosine similarity between an incoming query and each skill's
description (+when_to_use if present) to rank which skills are most
relevant to the current user turn.

Design notes
------------
* ``embedder`` is injected so no second model instance is created —
  the existing BGE-M3 (or any embedder with ``encode(text) -> list[float]``)
  is reused from the retrieval stack.
* Skill embeddings are pre-computed in ``build(skills)`` and cached in a
  dict to avoid per-query recomputation. Cache is invalidated on each
  ``build()`` call (called from ``SkillLoader.reload()``).
* Query embedding is computed freshly per ``match()`` call (query is
  per-turn; not cacheable).
* If ``embedder`` is None (offline / not yet initialised), ``match()``
  returns ``[]`` and the caller degrades gracefully to desc-list-only.
* ``encode`` may be a sync CPU-bound call; ``match_async`` wraps it in
  ``asyncio.to_thread`` to avoid blocking the event loop (T2 requirement).
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, Optional


class SkillMatcher:
    """Ranks skills by cosine similarity to a query.

    Parameters
    ----------
    embedder:
        Any object with ``encode(text: str) -> list[float]`` (sync is fine;
        ``match_async`` wraps in ``asyncio.to_thread``). Pass ``None`` to
        run in degraded mode (always returns empty list).
    """

    def __init__(self, embedder: Optional[Any]) -> None:
        self._embedder = embedder
        # name → pre-computed embedding vector
        self._cache: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Build / invalidate
    # ------------------------------------------------------------------

    def build(self, skills: list[Any]) -> None:
        """Pre-compute embeddings for all skill descriptions.

        Called once on loader start and after each reload. ``skills`` is a
        list of duck-typed objects with at least ``.name``, ``.description``
        and optionally ``.when_to_use`` attributes (or keys if dict).

        If the embedder is unavailable the cache is cleared so we don't
        serve stale embeddings from a prior build.
        """
        self._cache = {}
        if self._embedder is None:
            return
        for skill in skills:
            name = _skill_attr(skill, "name", "")
            description = _skill_attr(skill, "description", "") or ""
            when_to_use = _skill_attr(skill, "when_to_use", "") or ""
            text = description
            if when_to_use:
                text = f"{description}\n{when_to_use}"
            if not text.strip() or not name:
                continue
            try:
                vec = self._embedder.encode(text)
                self._cache[name] = _normalise(vec)
            except Exception:  # noqa: BLE001 — degrade silently
                pass

    # ------------------------------------------------------------------
    # Synchronous match
    # ------------------------------------------------------------------

    def match(self, query: str, skills: list[Any]) -> list[tuple[str, float]]:
        """Compute cosine similarity between ``query`` and each skill.

        Returns a list of ``(name, similarity)`` sorted descending by
        similarity. Skills not in the cache (no embedding) are omitted.

        If the embedder is None or the cache is empty, returns ``[]``.
        """
        if self._embedder is None or not self._cache:
            return []
        if not skills:
            return []
        try:
            query_vec = _normalise(self._embedder.encode(query))
        except Exception:  # noqa: BLE001
            return []

        results: list[tuple[str, float]] = []
        for skill in skills:
            name = _skill_attr(skill, "name", "")
            skill_vec = self._cache.get(name)
            if skill_vec is None:
                continue
            sim = _cosine_sim(query_vec, skill_vec)
            results.append((name, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Async variant (avoids blocking event loop for sync CPU-bound encode)
    # ------------------------------------------------------------------

    async def match_async(self, query: str, skills: list[Any]) -> list[tuple[str, float]]:
        """Async wrapper — runs the sync ``match()`` in a thread pool.

        Use this from async component ``provide()`` to avoid blocking the
        event loop when the embedder is a local CPU model (e.g. BGE-M3).
        """
        if self._embedder is None:
            return []
        return await asyncio.to_thread(self.match, query, skills)


# ---------------------------------------------------------------------------
# Vector utilities
# ---------------------------------------------------------------------------

def _normalise(vec: list[float]) -> list[float]:
    """Return L2-normalised vector. Zero vector → zero vector (no division)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-12:
        return list(vec)
    return [x / norm for x in vec]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two L2-normalised vectors."""
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


def _skill_attr(s: Any, attr: str, default: Any = None) -> Any:
    if isinstance(s, dict):
        return s.get(attr, default)
    return getattr(s, attr, default)


__all__ = ["SkillMatcher"]
