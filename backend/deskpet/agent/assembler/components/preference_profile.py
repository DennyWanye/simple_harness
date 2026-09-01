# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FP-4 WI-3.2 — PreferenceProfileComponent.

Injects user preference / profile facts into the assembler bundle so the
LLM passively "knows" the user (without re-asking every turn).

Design constraints (from spec + §7 red line):
- Reads ONLY categories: preference / profile / constraint.
  Must NOT read goal (execution-state, handled by goal_store).
- Renders pure facts. MUST NOT add sycophancy/stickiness wording.
- verify completion judgment must NOT read this block (interaction-style only).
- bucket="dynamic"  — rebuilt every turn so preference changes reflect next round.
- priority=85       — below persona(90), above skill(70)/tool(60).
- flag_enabled=False → returns empty Slice (meta status=flag_off), BC intact.
- store=None        → returns empty Slice (meta status=no_store), no crash.
- No active rows    → returns empty Slice (no_store waste avoided).

Pin items (pinned=1) are forced to the top with 📌 marker.
Top-N default 10 (spec §2 says 8-12).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext

logger = logging.getLogger(__name__)

# Categories pulled from facts for the profile block.
# MUST NOT include "goal" (execution-state belongs to goal_store).
_PROFILE_CATEGORIES = ("preference", "profile", "constraint")

# WI-CC-5: extra category injected only when auto_learnings flag is ON.
# Kept separate so the default (flag OFF) fetch set is byte-identical.
_LEARNING_CATEGORY = "learning"

# Default top-N cap to stay under ~400 tokens.
_DEFAULT_TOP_N = 10

_PREFERENCE_VALUE_SEMANTICS = {
    ("response.detail", "brief"): (
        "concise_complete（内部值 brief）。最高优先级约束：只压缩措辞，"
        "绝不减少用户当前消息明确给出的信息项。回答前逐项核对并覆盖每个结论、"
        "责任人、行动项和其他可执行信息；若完整与简短冲突，完整优先，"
        "不能只留下结论。"
    ),
    ("response.detail", "detailed"): (
        "detailed（完整展开必要过程、依据和步骤，同时避免无关赘述）"
    ),
}


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    from deskpet.agent.tokens import count_text_tokens
    return count_text_tokens(text)


class PreferenceProfileComponent:
    """Injects active user preferences / profile into the context bundle.

    Constructor args:
        store       -- FactsStore instance (or None when flag off / tests).
        flag_enabled -- bool; False = return empty Slice (BC). Corresponds to
                       config.memory.v2.persona_inject flag in main.py.
        top_n       -- Maximum number of facts to include (default 10).
    """

    name: str = "preference_profile"

    def __init__(
        self,
        store: Any | None = None,
        *,
        flag_enabled: bool = True,
        top_n: int = _DEFAULT_TOP_N,
        include_learnings: bool = False,
        resolver: Any | None = None,
        owner_provider: Any | None = None,
        request_override_provider: Any | None = None,
        relevant_keys_provider: Any | None = None,
    ) -> None:
        self._store = store
        self._flag_enabled = flag_enabled
        self._top_n = top_n
        # WI-CC-5: when True, also inject category='learning' (procedural
        # auto-memory) into the profile block. Default False = BC: fetch set
        # stays (preference/profile/constraint), bundle byte-identical.
        # Set by main.py from cfg.memory.v2.auto_learnings.
        self._include_learnings = bool(include_learnings)
        self._resolver = resolver
        self._owner_provider = owner_provider
        self._request_override_provider = request_override_provider
        self._relevant_keys_provider = relevant_keys_provider

    async def provide(self, ctx: ComponentContext) -> Slice:
        # Gate 1: flag off → BC empty slice
        if not self._flag_enabled:
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=85,
                bucket="dynamic",
                meta={"status": "flag_off"},
            )

        # Gate 2: no store → empty slice (tests / flag-off via None injection)
        if self._store is None and self._resolver is None:
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=85,
                bucket="dynamic",
                meta={"status": "no_store"},
            )

        start = time.monotonic()
        resolution = None
        try:
            if self._resolver is not None:
                if self._owner_provider is None:
                    raise RuntimeError("preference resolver owner provider missing")
                owner = self._owner_provider(ctx)
                overrides = (
                    tuple(self._request_override_provider(ctx))
                    if self._request_override_provider is not None
                    else ()
                )
                relevant_keys = (
                    self._relevant_keys_provider(ctx)
                    if self._relevant_keys_provider is not None
                    else None
                )
                resolution = self._resolver.resolve(
                    owner,
                    request_id=str(
                        ctx.config.get("request_id")
                        or ctx.current_message_id
                        or ctx.session_id
                        or "preference-profile"
                    ),
                    overrides=overrides,
                    relevant_keys=relevant_keys,
                )
            rows = await self._fetch_rows(resolution=resolution)
        except Exception as exc:  # noqa: BLE001 — component must not raise
            return Slice(
                component_name=self.name,
                text_content="",
                priority=85,
                bucket="dynamic",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        if not rows:
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=85,
                bucket="dynamic",
                meta={"status": "empty", "facts": 0},
            )

        text = self._render(rows)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        # Observability (FP-4 WI-3.2): preference/profile injection was invisible
        # in logs — emit how many facts got injected so real-machine acceptance
        # (TC-4.2/4.3) has hard evidence the profile block reached the prompt.
        logger.info(
            "preference_profile_injected facts=%d task_type=%s",
            len(rows),
            getattr(ctx, "task_type", "?"),
        )
        return Slice(
            component_name=self.name,
            text_content=text,
            tokens=_approx_tokens(text),
            priority=85,
            bucket="dynamic",
            meta={
                "facts": len(rows),
                "latency_ms": round(elapsed_ms, 2),
                "preference_snapshot_hash": (
                    resolution.snapshot_hash if resolution is not None else None
                ),
                "preference_dependency_count": (
                    len(resolution.dependencies) if resolution is not None else 0
                ),
            },
        )

    # ------------------------------------------------------------------ #
    # Internals

    async def _fetch_rows(self, *, resolution: Any | None = None) -> list[dict[str, Any]]:
        """Pull rows for all profile categories, sort by pinned→confidence DESC."""
        all_rows: list[dict[str, Any]] = []
        if resolution is not None:
            for item in resolution.items:
                value = item.value
                if not isinstance(value, str):
                    import json

                    value = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                all_rows.append(
                    {
                        "category": "preference",
                        "key": item.preference_key,
                        "value": value,
                        "confidence": 1.0 if item.authority == "explicit" else 0.8,
                        "pinned": int(item.authority == "explicit"),
                        "updated_at": float(item.state_version),
                    }
                )
        # WI-CC-5: append 'learning' only when flag ON (BC: default set unchanged).
        categories = (
            tuple(
                item
                for item in _PROFILE_CATEGORIES
                if resolution is None or item != "preference"
            )
            + (
            (_LEARNING_CATEGORY,) if self._include_learnings else ()
            )
        )
        if self._store is not None:
            for cat in categories:
                rows = await self._store.list_active(
                    category=cat,
                    limit=self._top_n,
                )
                all_rows.extend(rows)

        # Sort: pinned=1 first, then by confidence DESC, then recency (updated_at DESC)
        all_rows.sort(
            key=lambda r: (
                -int(r.get("pinned") or 0),     # pinned first (negate for desc)
                -float(r.get("confidence") or 0.0),
                -float(r.get("updated_at") or 0.0),
            )
        )
        return all_rows[: self._top_n]

    def _render(self, rows: list[dict[str, Any]]) -> str:
        """Render the PROFILE.md-style block.

        Format (pure facts — NO sycophancy, NO stickiness instructions):
            ## 用户画像 (PROFILE, 活跃偏好)
            - 📌 [preference] 编辑器: neovim
            - [preference] 饮料: 乌龙茶
            - [profile] 称呼: 老王
            - [constraint] 工作时段: 只在晚上工作
        """
        lines = ["## 用户画像 (PROFILE, 活跃偏好)"]
        for r in rows:
            category = r.get("category") or "preference"
            key = r.get("key") or ""
            value = r.get("value") or ""
            value = _PREFERENCE_VALUE_SEMANTICS.get(
                (str(key), str(value)),
                value,
            )
            pinned = int(r.get("pinned") or 0)
            pin_mark = "📌 " if pinned else ""
            lines.append(f"- {pin_mark}[{category}] {key}: {value}")
        return "\n".join(lines)


# Verify protocol compliance at import time.
_ASSERT_PROTOCOL: Component = PreferenceProfileComponent()
