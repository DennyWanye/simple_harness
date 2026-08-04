# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from deskpet.agent.turn_preparer import (
    PreparedTurnContext,
    RoutedTurnIntent,
    TurnInput,
)
from deskpet.harness.adapters.product_turn_open import (
    ProductTurnIdentityResolver,
    ProductTurnPreparationService,
)
from deskpet.harness.contracts import HostContext
from deskpet.harness.kernel import root_run_identity


def _host(session_id: str, workspace: str) -> HostContext:
    return HostContext(
        session_id=session_id,
        principal_id=f"principal-{session_id}",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("fixture",),
        trace_id="trace-product-turn-open",
        write_scope_root=workspace,
    )


def test_identity_resolver_freezes_trusted_task_and_workspace(tmp_path) -> None:
    workspace = str(tmp_path / "workspace")
    turn = TurnInput(
        text="build it",
        session_id="session-open",
        request_id="request-open",
        turn_id="turn-open",
    )

    resolved = ProductTurnIdentityResolver().resolve(
        turn,
        _host(turn.session_id, workspace),
        current_message_id=42,
    )

    _, expected_root = root_run_identity(
        turn.session_id,
        turn.request_id,
        turn.turn_id,
    )
    assert resolved.root_ref == expected_root
    assert resolved.turn.root_run_id == expected_root.run_id
    assert resolved.turn.task_scope_id == resolved.task_scope_id
    assert resolved.turn.workspace_ref == resolved.host.workspace
    assert resolved.host.write_scope_root == resolved.host.workspace
    assert resolved.conversation.seed_message_refs == ("message:42",)
    assert resolved.projection.root_run_id == expected_root.run_id


def test_identity_resolver_marks_session_workspace_as_existing(tmp_path) -> None:
    workspace = str(tmp_path / "confirmed-project")
    host = replace(
        _host("session-existing", workspace),
        workspace=workspace,
    )
    turn = TurnInput(
        text="continue it",
        session_id=host.session_id,
        request_id="request-existing",
        turn_id="turn-existing",
    )

    resolved = ProductTurnIdentityResolver().resolve(
        turn,
        host,
        current_message_id=43,
    )

    assert resolved.work_context.workspace_root == workspace
    assert resolved.work_context.workspace_source == "existing"


@pytest.mark.parametrize(
    "turn, host, error",
    (
        (
            TurnInput(
                text="x",
                session_id="turn-session",
                request_id="request",
                turn_id="turn",
            ),
            _host("host-session", "F:/workspace"),
            "session_id",
        ),
        (
            TurnInput(text="x", session_id="session"),
            _host("session", "F:/workspace"),
            "request_id",
        ),
    ),
)
def test_identity_resolver_rejects_untrusted_or_unstable_identity(
    turn: TurnInput,
    host: HostContext,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        ProductTurnIdentityResolver().resolve(
            turn,
            host,
            current_message_id=None,
        )


class _Preparer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def prepare_context(self, turn, **_kwargs):
        self.calls.append("context")
        return PreparedTurnContext(
            turn=turn,
            messages=[{"role": "user", "content": turn.text}],
            bundle=SimpleNamespace(task_type="creation"),
            assembler=None,
        )

    async def prepare_direct_run(self, prepared, *, services):
        assert services["marker"] == "service"
        self.calls.append("direct")
        return RoutedTurnIntent(prepared=prepared)

    def prepare_workflow_request_payload(self, routed, turn, config):
        assert routed.prepared.turn is turn
        assert config.marker == "config"
        self.calls.append("payload")
        return {"text": turn.text}


@pytest.mark.asyncio
async def test_preparation_service_keeps_context_before_direct_run_and_trace() -> None:
    preparer = _Preparer()
    turn = TurnInput(
        text="build it",
        session_id="session",
        request_id="request",
        turn_id="turn",
    )

    result = await ProductTurnPreparationService(preparer).prepare(
        turn,
        services={"marker": "service"},
        config=SimpleNamespace(marker="config"),
        local_llm=None,
        tool_registry=None,
        provider=None,
        current_message_id=7,
        companion_ingress_owner=None,
        summary_user_is_confused=None,
        summary_latest_task_snapshot=None,
        summary_build_reinject_msg=None,
    )

    assert preparer.calls == ["context", "direct", "payload"]
    assert result.prepared is result.routed.prepared
    assert result.request_payload["text"] == "build it"
    trace = result.request_payload["product_turn_trace"]
    assert [stage["step"] for stage in trace] == [
        "prepare_context",
        "direct_run",
    ]
    assert trace[0]["input"]["current_message_id"] == 7
    assert trace[1]["output"]["authority"] == "main_agent"
