"""Generate the old defect with unmodified installed H075 public Runtime.

Invoked in an isolated child, never imports the candidate SDK source.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(Path(__file__).parent))
from importlib.metadata import version

import simple_harness

assert simple_harness.__version__ == version('simple-harness-sdk') == '0.7.5'
assert Path(simple_harness.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
from simple_harness import RunId
from simple_harness.tools.authorization import AuthorizationDecision
from test_react_sqlite_runtime import (
    AuthorizationScenario,
    PhysicalToolCounter,
    authorization_runtime,
    start_authorization_wait,
)


async def main():
    clock = [10.0]
    physical = PhysicalToolCounter()
    runtime, uow, db = authorization_runtime(Path(sys.argv[2]),
        authorization=AuthorizationScenario(expires_at=11.0), physical=physical,
        owner_id='legacy-expiry', clock=lambda: clock[0])
    decision = await start_authorization_wait(runtime, uow)
    clock[0] = 12.0
    await runtime.client.decide_authorization(RunId('run-fault'), decision_id=decision.decision_id,
        nonce=str(decision.request['nonce']), expected_version=decision.version,
        decision=AuthorizationDecision.ALLOW)
    assert physical.calls == 0
    assert uow.read_run('run-fault').state.value == 'failed'
    assert uow.read_run_operation_audit(RunId('run-fault')).terminal_evidence is None
    await runtime.close()
    db.close()

asyncio.run(main())
