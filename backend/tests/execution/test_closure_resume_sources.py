"""New mutable-field producer controls; no model/native or fabricated SDK rows."""
import asyncio
import json
import sqlite3

import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage

from deskpet.memory.human_memory_service import QueueTurnRequest
from tests.execution.test_closure_request_guard import world, attempt_rows

RESUME = "Next: inspect the actual written file."


def mutation(observation, *, value=RESUME):
    return dict(outcome="mutate", base_revision=observation["current_revision"],
        operations=[dict(operation_id="resume-source", kind="resume.update", value=value,
                        reason_code="next_step", evidence_refs=observation["allowed_evidence_refs"])],
        evidence_refs=observation["allowed_evidence_refs"], idempotency_key="resume-source-plan")


async def run(w):
    await w.runtime.after_enqueue(subject=w.runtime.subject)
    await asyncio.wait_for(w.runtime.drain(), 20)
    if w.runtime.last_error is not None:
        raise w.runtime.last_error


async def write_again(w, monkeypatch, scope):
    requests = []
    async def invoke(request, *, cancel):
        n = len(requests); requests.append(request)
        values = [json.loads(m.content)["value"] for m in request.messages
            if m.role.value == "tool" and isinstance(m.content, str)
            and isinstance(json.loads(m.content).get("value"), dict)]
        if n == 0:
            name, args = "context_route", {"route":"resume_existing", "task_scope_id":scope}
        elif n == 1:
            assert values[-1]["resume_package"]["disclosure"]["fields"]["resume"] == RESUME
            name, args = "tool_search", {"query":"write_file"}
        elif n == 2:
            name, args = "tool_describe", {"capability_id":values[-1]["matches"][0]["capability_id"]}
        elif n == 3:
            name, args = "tool_activate", {k:values[-1][k] for k in ("capability_id", "schema_hash", "describe_nonce")}
        elif n == 4:
            name, args = "write_file", {"path":"second.txt", "content":"actual later task work"}
        else:
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Second write completed."),
                model="model", usage=ProviderUsage(10, 10, 20))
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, name),
            tool_calls=(ProviderToolCall(CallId(f"second-{n}"), name, args),), model="model", usage=ProviderUsage(10, 10, 20))
    monkeypatch.setattr(w.provider, "invoke", invoke)
    await w.service.enqueue_turn(QueueTurnRequest(None, "second-resume-source", "Resume the task and write another file"))
    await run(w)
    return requests


@pytest.mark.asyncio
# 2026-09-10 删记忆 SDK：去掉 "fallback_forget"。
@pytest.mark.parametrize("producer", ["tool", "tool_then_resume", "fallback", "fallback_commit_fault"])
async def test_nonempty_resume_actual_producer_to_next_closure(tmp_path, monkeypatch, producer):
    def reply(observation, ordinal):
        if producer.startswith("fallback") and ordinal == 1:
            return mutation({**observation["task_scope"], "allowed_evidence_refs":observation["allowed_evidence_refs"]})
        return dict(outcome="no_mutation", base_revision=observation["task_scope"]["current_revision"],
            evidence_refs=observation["allowed_evidence_refs"], idempotency_key="later-close", closure_reason="No further metadata change.")
    w = await world(tmp_path, monkeypatch, "allow", closure_reply=reply)
    try:
        if producer == "fallback_commit_fault":
            from deskpet.memory.human_memory_program import HumanMemoryProgramStore
            from deskpet.task_scope.mutation_disclosure import RESULT_POLICY
            append = HumanMemoryProgramStore.append_evidence_tx
            async def fault_after_append(self, db, envelope, receipt, **kwargs):
                result = await append(self, db, envelope, receipt, **kwargs)
                if envelope.filter_policy_version == RESULT_POLICY:
                    raise RuntimeError("injected_after_closure_result_source")
                return result
            monkeypatch.setattr(HumanMemoryProgramStore, "append_evidence_tx", fault_after_append)
        if producer.startswith("tool"):
            old = w.provider.invoke
            async def first(request, *, cancel):
                n = len(w.provider.requests)
                if producer == "tool_then_resume" and n == 6:
                    # Freeze the actual field immediately after its SDK mutation,
                    # before the same Run consumes a later scope route. That
                    # future route must not rewrite this producer's input proof.
                    from deskpet.task_scope.disclosure import render_scope_disclosure
                    from deskpet.task_scope.search import TaskScopeSearchStore
                    from deskpet.memory.trusted_disclosure import resolve_current_disclosure
                    current = await w.queue.current_snapshot(w.runtime.subject)
                    scope_id = w.provider.route_result["context_route_receipt"]["task_scope_id"]
                    opened = await TaskScopeSearchStore(w.state).open_exact(subject=w.runtime.subject,
                        allowed_scope_ids=(scope_id,), task_scope_id=scope_id)
                    disclosure = await resolve_current_disclosure(db_path=w.state, subject=w.runtime.subject,
                        run_id=current.sdk_run_id, request_id=request.request_id.value)
                    w.before_later_route = await render_scope_disclosure(db_path=w.state,
                        package=opened.resume_package, subject=w.runtime.subject, stack=w.stack,
                        policy=w.runtime.history_policy, disclosure_context=disclosure)
                    assert w.before_later_route["disclosure"]["fields"]["resume"] == RESUME
                    w.provider.requests.append(request)
                    return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Reopen this task."),
                        tool_calls=(ProviderToolCall(CallId("resume-after-mutation"), "context_route",
                            {"route":"resume_existing", "task_scope_id":scope_id}),),
                        model="model", usage=ProviderUsage(10, 10, 20))
                if n != 5:
                    return await old(request, cancel=cancel)
                w.provider.requests.append(request)
                instruction = next(json.loads(m.content) for m in request.messages if m.role.value == "system"
                    and isinstance(m.content, str) and '"task_scope_closure_required"' in m.content)
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Record the next step."),
                    tool_calls=(ProviderToolCall(CallId("actual-resume-mutation"), "task_scope_update", mutation(instruction)),),
                    model="model", usage=ProviderUsage(10, 10, 20))
            monkeypatch.setattr(w.provider, "invoke", first)
        if producer == "fallback_commit_fault":
            with pytest.raises(RuntimeError, match="injected_after_closure_result_source"):
                await run(w)
            with sqlite3.connect(w.state) as db:
                assert db.execute("SELECT COUNT(*) FROM human_memory_evidence WHERE source_ref LIKE 'closure-result-source:%'").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM task_scope_mutation_decisions WHERE outcome='mutate'").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM post_turn_invocation_attempts WHERE status='succeeded'").fetchone()[0] == 0
            assert len(w.sent) == 1  # real send is retained, never called not_sent
            return
        await run(w)
        if producer == "tool_then_resume":
            from deskpet.task_scope.disclosure import verify_scope_disclosure
            # Rebuild against completed real public effects; frozen proof must
            # remain identical and must not recurse through the later route.
            await verify_scope_disclosure(db_path=w.state, package=w.before_later_route,
                subject=w.runtime.subject, stack=w.stack)
            with sqlite3.connect(w.state) as db:
                indexed = db.execute("SELECT tool_name,sequence FROM primary_effect_identities "
                    "WHERE tool_name IN ('task_scope_update','context_route') ORDER BY sequence").fetchall()
            assert [r[0] for r in indexed] == ["context_route", "task_scope_update", "context_route"]
            assert indexed[1][1] < indexed[2][1]
        with sqlite3.connect(w.state) as db:
            scope, state_json = db.execute("SELECT s.task_scope_id,r.state_json FROM task_scopes s JOIN task_scope_heads h USING(task_scope_id) JOIN task_scope_canonical_revisions r ON r.task_scope_id=h.task_scope_id AND r.revision=h.current_revision").fetchone()
            assert json.loads(state_json)["resume"] == RESUME
            carriers = db.execute("SELECT COUNT(*) FROM human_memory_evidence WHERE source_ref LIKE 'closure-result-source:%'").fetchone()[0]
            assert carriers == (1 if producer.startswith("fallback") else 0)
        # 2026-09-10 删记忆 SDK：fallback_forget 档依赖抑制权威，整段删除。
        requests = await write_again(w, monkeypatch, scope)
        assert len(requests) == 6
        assert w.outcomes[-1].status == "no_mutation"
        assert w.sent[-1]["messages"][1]["content"].count(RESUME) >= 1
        assert all(r["status"] == "succeeded" for r in attempt_rows(w))
    finally:
        await w.runtime.close()
        await w.stack.close()
        await w.client.aclose()
