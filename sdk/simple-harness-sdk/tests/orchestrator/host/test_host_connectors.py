# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""``OrchestrationHost(connectors=...)`` 注册时的检查，与执行器对连接器回执、报错文字的处理。"""

from __future__ import annotations

from typing import Any

import pytest
from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.runtime.actions import RECEIPT_LIMIT_BYTES, checked_receipt, error_text
from agent_orchestrator.runtime.connectors import OperationSpec, Receipt


class Good:
    name = "photos"
    supports_idempotency = True
    supports_reconciliation = True
    lookup_authority = "authoritative"

    def __init__(self, **operations: OperationSpec) -> None:
        self.operations = operations or {"move": OperationSpec("move", "L2", ("to",))}

    def normalize_target(self, target: str) -> str:
        return target

    def execute(self, operation, target, params, *, idempotency_key):  # pragma: no cover
        raise AssertionError("not called")

    def lookup(self, idempotency_key):  # pragma: no cover
        return None


def _host(tmp_path, connectors: list[Any], **settings: Any) -> OrchestrationHost:
    return OrchestrationHost(
        tmp_path,
        principal=Principal("u", "用户"),
        settings=HostSettings(**settings),
        connectors=connectors,
    )


def test_registering_connectors_needs_an_explicit_ceiling(tmp_path):
    with pytest.raises(ValueError, match="max_action_level"):
        _host(tmp_path, [Good()])
    assert _host(tmp_path, [Good()], max_action_level="L2")._app_connectors.keys() == {"photos"}
    with pytest.raises(ValueError, match="max_action_level"):
        HostSettings(max_action_level="L9")


@pytest.mark.parametrize(
    ("connector", "fragment"),
    [
        (Good(read=OperationSpec("read", "L0", (), mutates=False)), "read-only"),
        (type("NoKey", (Good,), {"supports_idempotency": False})(), "deduplicate"),
        (type("NoLookup", (Good,), {"supports_reconciliation": False})(), "deduplicate"),
        (type("Vague", (Good,), {"lookup_authority": "maybe"})(), "lookup_authority"),
        (type("Builtin", (Good,), {"name": "file_publish"})(), "built in"),
        (type("Dotted", (Good,), {"name": "a.b"})(), "plain"),
        (Good(move=OperationSpec("other", "L2")), "OperationSpec"),
    ],
)
def test_a_connector_that_could_never_run_safely_is_refused_at_registration(
    tmp_path, connector, fragment
):
    with pytest.raises(ValueError, match=fragment):
        _host(tmp_path, [connector], max_action_level="L2")


def test_a_name_is_registered_once(tmp_path):
    with pytest.raises(ValueError, match="twice"):
        _host(tmp_path, [Good(), Good()], max_action_level="L2")


def test_only_a_canonical_bounded_receipt_counts():
    good = Receipt("k", "photos", "move", "t", "h" * 64, True, after={"to": "x"})
    assert checked_receipt(good) is good
    assert checked_receipt({"applied": True}) is None
    assert checked_receipt(Receipt("k", "photos", "move", "t", "h", True, after={1j})) is None
    big = Receipt("k", "photos", "move", "t", "h", True, after="x" * (RECEIPT_LIMIT_BYTES + 1))
    assert checked_receipt(big) is None


def test_connector_error_text_is_short_and_never_carries_a_secret():
    assert error_text(RuntimeError("x" * 2000)).startswith("RuntimeError: ")
    assert len(error_text(RuntimeError("x" * 2000))) <= 500
    leaked = RuntimeError("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz0123456789")
    assert error_text(leaked) == "RuntimeError"
