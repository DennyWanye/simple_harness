"""Disposable spike: intercept a durable permission decision before UI projection.

The spike uses the real ReActDriver, SQLite execution UoW, atomic decision
signal, and exact grant creation.  It deliberately discards the first driver
after the open decision to model a crash before auto resolution.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.execution.contracts import RunRef  # noqa: E402
from deskpet.harness.ports import DecisionSignal  # noqa: E402
from deskpet.harness.runtime import DriverRuntime  # noqa: E402


def load_react_test_module():
    path = (
        BACKEND_ROOT
        / "tests"
        / "harness_simplification"
        / "test_wi5_react_driver.py"
    )
    spec = importlib.util.spec_from_file_location("spike_react_test_support", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load ReAct test support")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main() -> None:
    support = load_react_test_module()
    with tempfile.TemporaryDirectory(prefix="deskpet-spike-auto-") as temp:
        base = support._call(0, durable=True)
        call = support.prepared_call(
            tool_name=base.tool_name,
            model_args=dict(base.final_params),
            call_id=base.stable_call_id,
            effect_id=support._EFFECTS[base.stable_call_id],
            capability_hash=support.CAPABILITY_HASH,
            scope_hash=support.SCOPE_HASH,
            requires_authorization=True,
            recoverable_effect=True,
        )
        batch = support.ReactToolBatch(
            "spike-auto-batch", (call,), (support._context(call),)
        )
        first, store, _ = await support._driver(
            Path(temp), support.ScriptedCollaborator([batch])
        )
        initial = await support._collect(first.start(support._request()))
        decision = initial[0]
        if decision.kind != "open_decision":
            raise AssertionError("expected a real durable open decision")

        manual_event = DriverRuntime.event_candidate("react", decision)
        before = await store.get_decision(
            decision.decision_id,
            ref=RunRef("run-react", "session-react"),
            actor=support._actor(),
        )

        # Simulated crash: discard `first` and bind a new driver/live index to
        # the same SQLite UoW before applying the policy:auto response.
        second = support._react_driver(support.ScriptedCollaborator(), store)
        response = {"allow": True, "actor": "policy:auto", "rule_version": 1}
        signal = DecisionSignal(
            "run-react",
            decision.decision_id,
            response,
            nonce=decision.nonce,
            version=0,
        )
        resumed = await support._collect(
            second.signal_decision_atomically(
                signal,
                support._durable_signal(decision, response),
                support._actor(),
            )
        )
        after = await store.get_decision(
            decision.decision_id,
            ref=RunRef("run-react", "session-react"),
            actor=support._actor(),
        )
        execute = next(item for item in resumed if item.kind == "execute_tools")
        grant = execute.grant_refs[0]

        result = {
            "decision_status_before_policy": before.status.value,
            "manual_projection_kind": manual_event.kind if manual_event else None,
            "manual_projection_status": (
                manual_event.status.value if manual_event is not None else None
            ),
            "auto_interceptor_projected_waiting": False,
            "decision_status_after_policy": after.status.value,
            "resumed_candidate_kinds": [item.kind for item in resumed],
            "exact_grant_created": bool(grant and grant.grant_id),
            "grant_bound_to_decision": bool(
                grant and grant.decision_id == decision.decision_id
            ),
            "grant_nonce_matches": bool(
                grant and grant.decision_nonce == decision.nonce
            ),
            "recovered_with_new_driver": first is not second,
            "conclusion": (
                "A runtime interceptor can suppress only the waiting projection, "
                "then reuse the existing durable atomic decision/grant signal. "
                "Production still needs a rule matrix and an open/resolve/signal reconciler."
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
