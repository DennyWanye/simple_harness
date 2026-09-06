"""New combined public input/draft gate, independent of the original source suites."""
import json

import pytest
import simple_harness_memory as m

from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.operation_audit.current_inputs import CurrentInputJournal, request_hash
from tests.memory.test_current_input_consumer import env, setup
from tests.memory.test_trusted_disclosure import selection
from tests.memory.test_procedure_scope_runtime import create_draft


@pytest.mark.asyncio
async def test_actual_current_input_does_not_inherit_independent_draft_forget(env):
    # A real signed SELF configuration. Replacing an external context DTO would
    # not prove the source/claim authority accepted this intended audience.
    principal, context, binding = await setup(env, disclosure_selection=selection())
    runtime = compose_human_memory_runtime(env.path, env.path.with_name("memory.db"),
        adapter_factory=lambda _: None, principal=principal)
    try:
        memory = await runtime.manager()
        await memory.register_principal_owner(principal, runtime.scope())
        memory_id, revision = await create_draft(runtime, env.path)
        page = await memory.discover_procedure_drafts(principal=principal, scope=runtime.scope(),
            disclosure_context=context, query="记录")
        candidate, = page.candidates
        assert (candidate.memory_id, candidate.revision) == (memory_id, revision)
        draft = m.HistoryProcedureDraftBinding(memory_id, revision, candidate.source_hash)
        values = (binding.evidence, draft)
        journal = CurrentInputJournal(env.path.with_name("operation-audit.db"))
        kwargs = dict(principal=principal, disclosure_context=context, binding=binding, bindings=values)
        before = await journal.check_current_input_visibility(memory, **kwargs)
        assert before.invocation_input_allowed
        assert [item.visible for item in before.history_visibility.items] == [True, True]
        await memory.suppress(principal=principal, request=m.SuppressionRequest(
            "forget-independent-draft", principal.actor_id, m.SuppressionScopeKind.MEMORY,
            memory_id, "user_forget", runtime.semantic_clock()))
        after = await journal.check_current_input_visibility(memory, **kwargs)
        assert after.invocation_input_allowed
        assert [item.visible for item in after.history_visibility.items] == [True, False]
        assert after.authority_epoch > before.authority_epoch
        assert before.request_hash == after.request_hash == request_hash(principal, context, binding, values)
        assert before.snapshot_hash != after.snapshot_hash
        records = (await journal.page(principal=principal))["items"]
        assert len(records) == 2
        assert all(row["state"] == "returned" and row["observation_status"] == "captured_bound"
            for row in records)
        assert {json.loads(row["observation_json"])["snapshot_hash"] for row in records} == {
            before.snapshot_hash, after.snapshot_hash}
        assert all(json.loads(row["observation_json"])["request_hash"] == before.request_hash
            for row in records)
    finally:
        await runtime.close()
