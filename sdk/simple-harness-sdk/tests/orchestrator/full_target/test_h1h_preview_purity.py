"""P05: production collector freezes the inputs; real preview remains pure.

The capture stop is intentionally a BaseException so the collector cannot translate
it into a planning refusal/retry; all writes before that stop establish the legal
request/decision fixture and are outside the purity measurement.

The round is the adoption round the main loop opened after the planner's proposed
method passed its independent review (``h1i_seed.reviewed``); the planner's reply to
it is delivered through the production collector.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from h1i_seed import plan_reply, reviewed

from agent_orchestrator.planning.plan_preview import CandidatePreview


class _PreviewInputsCaptured(BaseException):
    pass


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        item for item in root.rglob("*") if item.is_file() and ".git" not in item.parts
    ):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_p05_same_frozen_production_preview_is_deterministic_and_side_effect_free(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        async with reviewed(tmp_path, key="p05-preview-purity") as ((loop, mission, _world, _root, dispatch, _product), opener, provider):
            captured: dict[str, Any] = {}
            original_preview = dispatch.preview_plan_proposal

            def capture(proposal: Any, *, inputs: Any) -> Any:
                captured["proposal"] = proposal
                captured["inputs"] = inputs
                raise _PreviewInputsCaptured()

            dispatch.preview_plan_proposal = capture  # type: ignore[method-assign]
            try:
                await loop._collect_plan_decision(
                    opener,
                    object(),
                    mission,
                    plan_reply(opener.config["planning_package"]),
                    dispatch,
                )
            except _PreviewInputsCaptured:
                pass
            finally:
                dispatch.preview_plan_proposal = original_preview  # type: ignore[method-assign]

            assert set(captured) == {"proposal", "inputs"}
            db_before = loop.store.connection.total_changes
            tree_before = _tree_hash(Path(loop.assembled.workspaces.root))
            provider_before = len(provider.asked)
            gateway_before = len(loop.assembled.gateway.calls)

            first = dispatch.preview_plan_proposal(captured["proposal"], inputs=captured["inputs"])
            second = dispatch.preview_plan_proposal(captured["proposal"], inputs=captured["inputs"])

            assert isinstance(first, CandidatePreview)
            assert isinstance(second, CandidatePreview)
            assert first.compilation_hash == second.compilation_hash
            assert first.source_snapshot_hash == second.source_snapshot_hash
            assert loop.store.connection.total_changes == db_before
            assert _tree_hash(Path(loop.assembled.workspaces.root)) == tree_before
            assert len(provider.asked) == provider_before
            assert len(loop.assembled.gateway.calls) == gateway_before

    asyncio.run(case())
