"""I08: original admission producers and Commit work without NanoJev imports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


_CHILD = r'''
import asyncio, importlib.abc, json, sys
from pathlib import Path

# Fail on attempted dependency loading, rather than merely inspecting a flag.
class NoNanoJev(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if "nanojev" in fullname.lower():
            raise AssertionError("removed dependency requested: " + fullname)
sys.meta_path.insert(0, NoNanoJev())
root, checkout = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(checkout / "src"), str(checkout / "tests/orchestrator/full_target")]
from h1i_seed import committed, committing_round
import agent_orchestrator.governance.planning_authorization as authorization
import agent_orchestrator.runtime.planning_operations as operations
from agent_orchestrator.orchestrator.commit_service import CommitService
from agent_orchestrator.orchestrator.hierarchical_dispatch import HierarchicalDispatch
from agent_orchestrator.planning.plan_preview import CandidatePreview

async def main():
    counts = {"authorization": 0, "operation_snapshot": 0, "shape": 0, "commit": 0}
    originals = (authorization.build_planning_authorization, operations.build_operation_snapshot,
                 HierarchicalDispatch.preview_plan_proposal, CommitService.commit_planning_revision)
    def authority(*args, **kwargs):
        result = originals[0](*args, **kwargs)
        assert isinstance(result, authorization.PlanningAuthorizationSnapshot)
        counts["authorization"] += 1
        return result
    def operation(*args, **kwargs):
        result = originals[1](*args, **kwargs)
        counts["operation_snapshot"] += 1
        return result
    def preview(*args, **kwargs):
        result = originals[2](*args, **kwargs)
        assert isinstance(result, CandidatePreview)
        counts["shape"] += 1
        return result
    def commit(*args, **kwargs):
        result = originals[3](*args, **kwargs)
        counts["commit"] += 1
        return result
    authorization.build_planning_authorization = authority
    operations.build_operation_snapshot = operation
    HierarchicalDispatch.preview_plan_proposal = staticmethod(preview)
    CommitService.commit_planning_revision = commit
    # The product's own main loop: proposal, its independent review, adoption and commit.
    async with committed(root, key="h1h-i08") as (loop, mission, _world, _root, dispatch, product):
        intent, _raw = committing_round(loop, mission.id)
        assert all(value > 0 for value in counts.values()), counts
        assert counts["shape"] == counts["commit"] == 1
        assert dispatch.network(mission.id).plan_revision == 1
        events = loop.store.list_events(mission.id)
        assert len([e for e in events if e.type == "PlanRevisionCommitted"]) == 1
        assert not [e for e in events if "shadow" in e.type.lower() or "nanojev" in e.type.lower()]
        assert not [name for name in sys.modules if "nanojev" in name.lower()]
        print(json.dumps({"counts": counts, "revision": 1, "asked": product.provider.asked}))
asyncio.run(main())
'''


def test_i08_no_nanojev_process_executes_three_producers_and_original_commit(tmp_path):
    checkout = Path(__file__).resolve().parents[3]
    # No credentials, deployment flags or application-specific environment survive.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    child = subprocess.run(
        [sys.executable, "-I", "-c", _CHILD, str(tmp_path), str(checkout)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert child.returncode == 0, (child.stdout, child.stderr)
    result = json.loads(child.stdout.splitlines()[-1])
    assert result["revision"] == 1
    assert result["counts"]["commit"] == 1
