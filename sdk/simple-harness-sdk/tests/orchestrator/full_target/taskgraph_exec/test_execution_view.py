# SPDX-License-Identifier: Apache-2.0
"""NEXT-TG-1.0 §8: the execution-process read beside the strict TaskGraph read.

Real Orchestrator on the product deployment (TaskGraph bound at creation), real planner
commit and a real Worker Attempt through the original dispatch/collect/verify path; only
the model replies are scripted.  The execution read shares the graph's read token, pages without
mixing cuts, links execution instances by recorded identities, shows a turn's
visible work through the whitelist, and writes nothing.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from production_fixture import OUTPUT, enabled_world, scripted_worker
from agent_orchestrator.api.taskgraph import TaskGraphReadError
from agent_orchestrator.orchestrator.taskgraph_execution_view import EDGE_KINDS, NODE_KINDS

SECRET = "sk-" + "a" * 30
def _worker():  # type: ignore[no-untyped-def]
    return scripted_worker(("workspace_write_file", {"path": OUTPUT, "content": "Repository facts, " + SECRET}))


def test_execution_view_shares_the_graph_token_links_instances_and_reads_turns(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key="tg-exec-view", worker=_worker()) as world:
            await world.commit_seed()
            await world.run_worker()
            store, mission, reads = world.loop.store, world.mission.id, world.graph.reads
            before = (store.last_event_seq(mission), world.provider.calls, store.connection.total_changes)

            view = reads.execution_snapshot(mission)
            graph = reads.snapshot(mission)
            assert view["read_token"] == view["graph"]["read_token"]
            assert view["graph"]["read_token"]["plan_revision"] == graph["read_token"]["plan_revision"] == 1
            assert view["complete"] is True and view["next_cursor"] is None
            labels = {row["occurrence_id"]: row for row in view["occurrence_labels"]}
            assert labels and all(row["step_key"] for row in labels.values())
            nodes = {n["node_id"]: n for n in view["execution_nodes"]}
            assert {n["kind"] for n in nodes.values()} <= set(NODE_KINDS)
            assert {e["kind"] for e in view["execution_edges"]} <= set(EDGE_KINDS)

            [attempt] = [n for n in nodes.values() if n["kind"] == "attempt"]
            [check] = [n for n in nodes.values() if n["kind"] == "check"]
            # The Planner proposed a method, it passed its independent review, then the Planner
            # adopted it: two Planner rounds, the second one committed plan revision 1.
            proposer, planner = [n for n in nodes.values() if n["kind"] == "planning" and n["role"] == "planner"]
            assert [d["decision_type"] for d in proposer["decisions"]] == ["PROPOSE_METHOD"]
            structure = {n["occurrence_id"] for n in view["graph"]["nodes"]}
            assert attempt["occurrence_id"] in structure and attempt["plan_revision"] == 1
            assert attempt["summary"]["text"] == "写好了要求的文件"
            assert attempt["summary"]["source_kind"] == "result_envelope"
            edges = {(e["kind"], e["source"], e["target"]) for e in view["execution_edges"]}
            assert ("attempt_of", attempt["node_id"], attempt["occurrence_id"]) in edges
            assert ("review_of", check["node_id"], attempt["node_id"]) in edges
            assert ("committed_as", planner["node_id"], "plan_revision:1") in edges
            assert [d["decision_type"] for d in planner["decisions"]] == ["REFINE"]
            # process edges never masquerade as scheduling edges
            assert not {"order", "data", "refinement"} & {e["kind"] for e in view["execution_edges"]}

            detail = reads.execution_detail(mission, attempt["node_id"])
            assert detail["turn"]["coverage"] == "COMPLETE"
            tools = [item for item in detail["items"] if item["t"] == "tool"]
            assert tools and tools[0]["tool"] == "workspace_write_file" and tools[0]["ok"] is True
            assert any(item["t"] == "submit" and item["text"] == "写好了要求的文件"
                       for item in detail["items"])
            text = json.dumps(detail, ensure_ascii=False)
            assert SECRET not in text  # tool arguments never leave, credentials redacted
            assert "[role:worker]" not in text and "instructions" not in text
            pinned = reads.execution_detail(mission, attempt["node_id"],
                                            through_journal_seq=detail["turn"]["through_journal_seq"])
            assert pinned["items"] == detail["items"]

            # keyset pages under one pinned cut; a tampered cursor is refused
            seen, cursor = [], None
            while True:
                page = reads.execution_snapshot(mission, cursor=cursor, limit=1)
                assert (page["graph"] is None) == (cursor is not None)
                assert (page["occurrence_labels"] is None) == (cursor is not None)
                seen += [n["node_id"] for n in page["execution_nodes"]]
                if page["complete"]:
                    break
                cursor = page["next_cursor"]
            assert seen == [n["node_id"] for n in view["execution_nodes"]]
            first = reads.execution_snapshot(mission, limit=1)
            import base64
            forged = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
            forged["h"] = "0" * 64
            bad = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode()
            with pytest.raises(TaskGraphReadError) as changed:
                reads.execution_snapshot(mission, cursor=bad)
            assert changed.value.code == "SNAPSHOT_CHANGED"
            with pytest.raises(TaskGraphReadError) as malformed:
                reads.execution_snapshot(mission, cursor="not-a-cursor!")
            assert malformed.value.code == "INVALID_CURSOR"
            with pytest.raises(TaskGraphReadError) as missing:
                reads.execution_detail(mission, "attempt:nope")
            assert missing.value.code == "NOT_FOUND"
            with pytest.raises(TaskGraphReadError) as absent:
                reads.diff(mission, 1, 7)
            assert absent.value.code == "REVISION_NOT_FOUND"

            # reads write nothing and call no model
            assert (store.last_event_seq(mission), world.provider.calls,
                    store.connection.total_changes) == before

    asyncio.run(case())
