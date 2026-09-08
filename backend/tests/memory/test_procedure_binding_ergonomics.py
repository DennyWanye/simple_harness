# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""The frozen binding must be followable by a model, not only correct.

Oracle source: native r14 (`plans/2026-09-07-native-main-journey/
NATIVE-R14-PROCEDURE-CHAIN.md`), real DeepSeek, Host main ``7cec5249``. The
contract behaved exactly as designed and the chain still FAILed: the bind
answer said only ``execution_authorized: false`` and a step count, so the model
re-invented the ``content`` it had just bound, got
``procedure_call_not_bound_step`` behind the single opaque string "Procedure use
was rejected before execution.", concluded it should re-bind, got
``procedure_same_run_changed_use``, and looped to ``react_max_turns_exceeded``.

Nothing below relaxes the contract: the binding is still immutable inside the
Run, the step call still has to match ``tool`` + ``arguments_hash`` exactly and
in order, and the two denials are still denials with the same stable codes. The
assertions are that a model holding only the tool answers can now recover.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
import simple_harness as h
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage

from deskpet.memory.human_memory_service import CreateTaskScopeRequest, QueueTurnRequest
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.procedure_applicability import ProcedureUseRejected
from deskpet.memory.procedure_guidance import (
    bind_next_action,
    bound_step_calls,
    call_rejection_public_message,
)
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.tools import ProductToolRegistration
from tests.memory.test_procedure_recovery_runtime import session
from tests.memory.test_procedure_scope_runtime import STEPS, UseProvider
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root


# --------------------------------------------------------------------------
# Wording unit level: what each denial actually says.
# --------------------------------------------------------------------------

_BOUND = [
    {"text_hash": "a", "tool": "write_file", "arguments": {"content": "actual record", "path": "record-1.txt"},
     "arguments_hash": "h1"},
    {"text_hash": "b", "tool": "write_file", "arguments": {"content": "actual record", "path": "backup-1.txt"},
     "arguments_hash": "h2"},
]


def test_the_rejection_keeps_its_stable_code_while_carrying_actionable_detail():
    error = ProcedureUseRejected("procedure_call_not_bound_step", detail={"total": 2})
    assert str(error) == "procedure_call_not_bound_step"
    assert error.detail == {"total": 2}
    # Every existing raise site passes no detail and must stay unchanged.
    assert ProcedureUseRejected("procedure_persisted_body_corrupt").detail is None


def test_bound_steps_echo_the_exact_calls_in_order():
    assert bound_step_calls(_BOUND) == [
        {"ordinal": 1, "tool": "write_file", "arguments": {"content": "actual record", "path": "record-1.txt"}},
        {"ordinal": 2, "tool": "write_file", "arguments": {"content": "actual record", "path": "backup-1.txt"}},
    ]


def test_a_row_bound_before_arguments_were_persisted_falls_back_to_its_hash():
    legacy = [{"text_hash": "a", "tool": "write_file", "arguments_hash": "h1"}]
    assert bound_step_calls(legacy) == [{"ordinal": 1, "tool": "write_file", "arguments_hash": "h1"}]


def test_next_action_says_the_binding_is_frozen_and_names_the_calls_to_issue():
    text = bind_next_action(bound_step_calls(_BOUND))
    assert "frozen" in text
    # The r14 misreading: false was taken as "you are not allowed to run these".
    assert "execution_authorized" in text and "grants no extra permission" in text
    assert "step 1 `write_file`" in text and "step 2 `write_file`" in text
    assert "verbatim" in text
    assert "Do not call procedure_use again in this run." in text
    # r15: the binding stops governing the Run once its last step succeeds, so
    # the model is told it may verify the result instead of stopping there.
    assert "the binding is complete and ordinary tool calls are available again" in text


def test_not_bound_step_names_the_ordinal_tool_and_exact_expected_arguments():
    message = call_rejection_public_message(ProcedureUseRejected(
        "procedure_call_not_bound_step",
        detail={"expected": bound_step_calls(_BOUND)[0], "total": 2, "received_tool": "write_file"}))
    assert "step 1 of 2" in message
    assert "`write_file`" in message
    assert '"content": "actual record"' in message and '"path": "record-1.txt"' in message
    assert "You sent that tool with different arguments." in message
    assert "cannot be re-bound" in message


def test_not_bound_step_says_which_other_tool_was_sent():
    message = call_rejection_public_message(ProcedureUseRejected(
        "procedure_call_not_bound_step",
        detail={"expected": bound_step_calls(_BOUND)[1], "total": 2, "received_tool": "read_file"}))
    assert "You sent `read_file` instead." in message


def test_oversized_bound_arguments_are_not_echoed_but_still_pointed_at():
    huge = [{"text_hash": "a", "tool": "write_file",
             "arguments": {"content": "x" * 4000, "path": "record-1.txt"}, "arguments_hash": "h1"}]
    message = call_rejection_public_message(ProcedureUseRejected(
        "procedure_call_not_bound_step",
        detail={"expected": bound_step_calls(huge)[0], "total": 1, "received_tool": "write_file"}))
    assert "x" * 4000 not in message
    assert "copy them verbatim from the procedure_use result" in message


def test_same_run_rebinding_is_answered_with_the_binding_that_already_stands():
    message = call_rejection_public_message(ProcedureUseRejected(
        "procedure_same_run_changed_use", detail={"bound_steps": bound_step_calls(_BOUND)}))
    assert "immutable" in message
    assert "step 1 `write_file`" in message and "step 2 `write_file`" in message
    assert '"path": "backup-1.txt"' in message
    assert "instead of binding again" in message


def test_a_failed_prefix_says_what_to_do_next_and_a_finished_use_has_no_denial():
    failed = call_rejection_public_message(ProcedureUseRejected(
        "procedure_previous_step_not_successful", detail={"failed_ordinal": 1, "total": 2}))
    assert "Step 1 of the 2 bound Procedure steps did not succeed" in failed
    assert "Report the failure to the user" in failed
    # r15 retired procedure_use_already_complete: a completed binding stops
    # governing the Run instead of refusing every later call.
    from deskpet.memory import procedure_guidance
    from deskpet.sdk_adapters import procedure_use as procedure_use_adapter
    assert "procedure_use_already_complete" not in procedure_guidance._DETAILED
    assert "procedure_use_already_complete" not in procedure_guidance._STATIC
    assert "procedure_use_already_complete" not in procedure_use_adapter._GUIDANCE


def test_an_unmapped_pre_call_denial_still_names_its_code_and_stops_the_loop():
    message = call_rejection_public_message(ProcedureUseRejected("procedure_persisted_body_corrupt"))
    assert "procedure_persisted_body_corrupt" in message
    assert "Do not retry the identical call." in message


def test_wording_never_turns_a_clean_deny_into_an_exception():
    # A detail that does not match its builder must degrade, not raise.
    message = call_rejection_public_message(
        ProcedureUseRejected("procedure_call_not_bound_step", detail={"nonsense": True}))
    assert "procedure_call_not_bound_step" in message


# --------------------------------------------------------------------------
# Real runtime: the exact r14 sequence, recovered from the tool answers alone.
# --------------------------------------------------------------------------


def _json_or_none(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


class ReboundProvider(UseProvider):
    """r14's model: bind, then send different arguments, then try to re-bind."""

    def configure(self, scope_id, memory_id, revision, index):
        super().configure(scope_id, memory_id, revision, index)
        self.tool_texts: list[str] = []

    async def invoke(self, request, *, cancel):
        n = self.stage
        self.stage += 1
        self.requests.append(request)
        texts = [m.content for m in request.messages
                 if m.role.value == "tool" and isinstance(m.content, str)]
        self.tool_texts = texts
        results = [_json_or_none(text) for text in texts]
        bound = [{"path": f"record-{self.index}.txt", "content": "actual record"},
                 {"path": f"backup-{self.index}.txt", "content": "actual record"}]
        if n == 0:
            name, args = "context_route", {"route": "resume_existing", "task_scope_id": self.scope_id}
        elif n == 1:
            name, args = "tool_search", {"query": "write_file"}
        elif n == 2:
            name, args = "tool_describe", {"capability_id": results[-1]["value"]["matches"][0]["capability_id"]}
        elif n == 3:
            name, args = "tool_activate", {key: results[-1]["value"][key]
                                           for key in ("capability_id", "schema_hash", "describe_nonce")}
        elif n == 4:
            name, args = "procedure_use", {"memory_id": self.memory_id, "revision": self.revision,
                "steps": [dict(text=text, tool="write_file", arguments_json=json.dumps(value))
                          for text, value in zip(STEPS, bound, strict=True)]}
        elif n == 5:
            self.bind_answer = results[-1]["value"]
            # r14 step 3: the same tool, freshly invented content.
            name, args = "write_file", {"path": bound[0]["path"], "content": "rewritten record"}
        elif n == 6:
            self.not_bound_step_answer = texts[-1]
            # r14 step 4: "let me re-bind" with the new arguments.
            name, args = "procedure_use", {"memory_id": self.memory_id, "revision": self.revision,
                "steps": [dict(text=text, tool="write_file", arguments_json=json.dumps(value))
                          for text, value in zip(STEPS, [{"path": bound[0]["path"],
                                                          "content": "rewritten record"}, bound[1]],
                                                 strict=True)]}
        elif n in (7, 8):
            if n == 7:
                self.same_run_answer = texts[-1]
            # Recovery: replay the calls the tool answers echoed back, verbatim.
            echoed = self.bind_answer["bound_steps"][n - 7]
            name, args = echoed["tool"], echoed["arguments"]
        elif n == 9:
            instruction = next(json.loads(m.content) for m in request.messages if m.role.value == "system"
                and isinstance(m.content, str) and '"task_scope_closure_required"' in m.content)
            name, args = "task_scope_update", {"outcome": "no_mutation",
                "base_revision": instruction["current_revision"],
                "closure_reason": "Both actual files were written; task metadata unchanged.",
                "evidence_refs": instruction["allowed_evidence_refs"],
                "idempotency_key": f"rebound-close-{self.index}"}
        else:
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "两份文件已写入。"),
                model="model", usage=ProviderUsage(10, 10, 20))
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "执行 " + name),
            tool_calls=(ProviderToolCall(h.CallId(f"rebound-{self.index}-{n}"), name, args),),
            model="model", usage=ProviderUsage(10, 10, 20))


@pytest.mark.asyncio
async def test_the_r14_loop_now_recovers_from_the_tool_answers_without_loosening_the_binding(tmp_path):
    provider = ReboundProvider()
    async with session(tmp_path, provider) as ctx:
        scope = await ctx.service.create_task_scope(CreateTaskScopeRequest(
            "rebound-scope", "写记录和备份", "Write both files", "rebound-create"))
        await bind_scope_root(ctx.state, scope["scope_ref"], ctx.root, tag="rebound-root")
        provider.configure(scope["scope_ref"], ctx.memory_id, ctx.revision, 1)
        await ctx.service.enqueue_turn(QueueTurnRequest(None, "rebound-turn", "执行记录和备份两步。"))
        await ctx.runtime.after_enqueue(subject=local_owner_auth().subject)
        await asyncio.wait_for(ctx.runtime.drain(), 60)
        assert ctx.runtime.last_error is None

        # 1. The bind answer echoes the frozen calls and says what to do now.
        answer = provider.bind_answer
        assert answer["execution_authorized"] is False and answer["binding_frozen"] is True
        assert answer["steps"] == 2
        assert answer["bound_steps"] == [
            {"ordinal": 1, "tool": "write_file",
             "arguments": {"path": "record-1.txt", "content": "actual record"}},
            {"ordinal": 2, "tool": "write_file",
             "arguments": {"path": "backup-1.txt", "content": "actual record"}}]
        assert "Do not call procedure_use again in this run." in answer["next_action"]

        # 2. The changed-argument call is still denied, now legibly.
        denial = provider.not_bound_step_answer
        assert "procedure_call_not_bound_step" in denial
        assert "Procedure use was rejected before execution." not in denial
        assert "step 1 of 2" in denial and "actual record" in denial

        # 3. Re-binding in the same Run is still denied, now legibly.
        rebind = provider.same_run_answer
        assert "procedure_same_run_changed_use" in rebind
        assert "immutable" in rebind and "backup-1.txt" in rebind

        # 4. Replaying the echoed calls verbatim executes the real Procedure.
        assert (ctx.root / "record-1.txt").read_text() == "actual record"
        assert (ctx.root / "backup-1.txt").read_text() == "actual record"

        # 5. Exactly one binding was ever recorded (the re-bind wrote nothing),
        #    and exactly the two bound steps were reserved, in order. The
        #    rejected attempts reserved and executed nothing.
        with sqlite3.connect(ctx.state) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT use_id, body_json FROM procedure_uses").fetchall()
        assert len(rows) == 1
        use = json.loads(rows[0]["body_json"])
        assert [step["tool"] for step in use["steps"]] == ["write_file", "write_file"]
        assert use["steps"][0]["arguments"] == {"path": "record-1.txt", "content": "actual record"}
        reservations = await ctx.memory.procedure_runtime.store.reservations(rows[0]["use_id"])
        assert [item["step_ordinal"] for item in reservations] == [1, 2]
        assert [item["call"]["tool"] for item in reservations] == ["write_file", "write_file"]


# --------------------------------------------------------------------------
# r15 blocker 1: verifying the result after the bound steps.
# --------------------------------------------------------------------------


def read_file_registration(root):
    """A real non-control read tool, so the journey's step 5 can be replayed."""
    async def handler(arguments, _context):
        return {"path": arguments["path"], "content": (root / arguments["path"]).read_text()}
    return ProductToolRegistration(name="read_file", description="Read one file from the task workspace",
        input_schema={"type": "object", "required": ["path"], "additionalProperties": False,
                      "properties": {"path": {"type": "string", "maxLength": 512}}},
        handler=handler, dispatch_kind="async", permission_category="read_file",
        projectless_admission="safe", metadata={"source": "test-read-file", "version": "1",
            "stable_handler_id": "test.read_file.v1"})


class VerifyingProvider(UseProvider):
    """The native journey's step 5: run the Procedure, then read both files back."""

    def configure(self, scope_id, memory_id, revision, index):
        super().configure(scope_id, memory_id, revision, index)
        self.read_values: list[dict] = []
        self.rejections: list[str] = []

    async def invoke(self, request, *, cancel):
        n = self.stage
        self.stage += 1
        self.requests.append(request)
        texts = [m.content for m in request.messages
                 if m.role.value == "tool" and isinstance(m.content, str)]
        results = [_json_or_none(text) for text in texts]
        self.rejections = [value["error_code"] for value in results
                           if isinstance(value, dict) and value.get("error_code")]
        bound = [{"path": f"record-{self.index}.txt", "content": "actual record"},
                 {"path": f"backup-{self.index}.txt", "content": "actual record"}]
        # Both step tools and the verification tool are activated before the
        # binding: tool_search/describe/activate are controls, but keeping them
        # ahead of procedure_use is what a real Run does anyway.
        if n == 0:
            name, args = "context_route", {"route": "resume_existing", "task_scope_id": self.scope_id}
        elif n in (1, 4):
            name, args = "tool_search", {"query": "write_file" if n == 1 else "read_file"}
        elif n in (2, 5):
            matches = results[-1]["value"]["matches"]
            wanted = "write_file" if n == 2 else "read_file"
            name, args = "tool_describe", {"capability_id": next(
                m["capability_id"] for m in matches if m["capability_id"].endswith(wanted))}
        elif n in (3, 6):
            name, args = "tool_activate", {key: results[-1]["value"][key]
                                           for key in ("capability_id", "schema_hash", "describe_nonce")}
        elif n == 7:
            name, args = "procedure_use", {"memory_id": self.memory_id, "revision": self.revision,
                "steps": [dict(text=text, tool="write_file", arguments_json=json.dumps(value))
                          for text, value in zip(STEPS, bound, strict=True)]}
        elif n in (8, 9):
            self.bind_answer = results[-1]["value"] if n == 8 else self.bind_answer
            echoed = self.bind_answer["bound_steps"][n - 8]
            name, args = echoed["tool"], echoed["arguments"]
        elif n in (10, 11):
            # The user asked to read both files back and compare. Under the old
            # barrier this was procedure_use_already_complete.
            name, args = "read_file", {"path": bound[n - 10]["path"]}
        elif n == 12:
            self.read_values = [value["value"] for value in results[-2:]]
            instruction = next(json.loads(m.content) for m in request.messages if m.role.value == "system"
                and isinstance(m.content, str) and '"task_scope_closure_required"' in m.content)
            name, args = "task_scope_update", {"outcome": "no_mutation",
                "base_revision": instruction["current_revision"],
                "closure_reason": "Both actual files were written and read back identical.",
                "evidence_refs": instruction["allowed_evidence_refs"],
                "idempotency_key": f"verify-close-{self.index}"}
        else:
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "两份文件内容一致。"),
                model="model", usage=ProviderUsage(10, 10, 20))
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "执行 " + name),
            tool_calls=(ProviderToolCall(h.CallId(f"verify-{self.index}-{n}"), name, args),),
            model="model", usage=ProviderUsage(10, 10, 20))


@pytest.mark.asyncio
async def test_after_the_last_bound_step_succeeds_ordinary_calls_verify_the_result(tmp_path):
    provider = VerifyingProvider()
    async with session(tmp_path, provider,
                       extra_registrations=(read_file_registration(tmp_path / "workspace"),)) as ctx:
        scope = await ctx.service.create_task_scope(CreateTaskScopeRequest(
            "verify-scope", "写记录和备份", "Write both files", "verify-create"))
        await bind_scope_root(ctx.state, scope["scope_ref"], ctx.root, tag="verify-root")
        provider.configure(scope["scope_ref"], ctx.memory_id, ctx.revision, 1)
        await ctx.service.enqueue_turn(QueueTurnRequest(
            None, "verify-turn", "按流程执行，做完把两个文件读出来核对内容一致。"))
        await ctx.runtime.after_enqueue(subject=local_owner_auth().subject)
        await asyncio.wait_for(ctx.runtime.drain(), 60)
        assert ctx.runtime.last_error is None

        # 1. Nothing in the Run was rejected: the two verification reads after
        #    the last bound step were admitted, not procedure_use_already_complete.
        assert provider.rejections == []
        assert [value["content"] for value in provider.read_values] == ["actual record"] * 2
        assert [value["path"] for value in provider.read_values] == ["record-1.txt", "backup-1.txt"]

        # 2. The binding still governed exactly its own two steps, in order. The
        #    verification reads reserved nothing and are attributed to no step.
        with sqlite3.connect(ctx.state) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT use_id FROM procedure_uses").fetchall()
        assert len(rows) == 1
        reservations = await ctx.memory.procedure_runtime.store.reservations(rows[0]["use_id"])
        assert [(item["step_ordinal"], item["call"]["tool"]) for item in reservations] == [
            (1, "write_file"), (2, "write_file")]

        # 3. The observation is exactly one success, unchanged by the reads.
        manager = await ctx.memory.manager()
        assert await MemoryIngestionOutboxWorker(
            ctx.state, ctx.memory.manager, owner_id="verify-outbox").run_once() == "delivered"
        authority = ctx.memory.conversation_evidence_authority
        run_ids = await authority.completed_run_ids()
        assert len(run_ids) == 1
        group = await authority.registrations_for_run(run_ids[0])
        await PrimaryShortIndexingService(authority, manager=manager,
                                          principal=ctx.memory.principal()).register_group(group)
        await ctx.memory.procedure_runtime.observe_group(group, manager)
        applied = await ctx.memory.procedure_runtime.store.journal(rows[0]["use_id"], "applied")
        assert applied["result"]["independent_successes"] == 1
        assert applied["result"]["lifecycle_state"] == "draft"


# --------------------------------------------------------------------------
# r15 blocker 2: a Run that contains a pre-dispatch denial keeps its group.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_with_a_denied_call_still_registers_its_group_and_observation(tmp_path):
    """The r14 recovery Run must still count. Regression oracle: before this
    fix the denied ``write_file`` had no ``primary_effect_identities`` row, so
    the terminal fell back to ``primary-message-v1`` and
    ``registrations_for_run`` raised ``terminal_multiple_items_not_representable``
    - the Run's Procedure observation and short indexing were both lost."""
    provider = ReboundProvider()
    async with session(tmp_path, provider) as ctx:
        scope = await ctx.service.create_task_scope(CreateTaskScopeRequest(
            "denied-scope", "写记录和备份", "Write both files", "denied-create"))
        await bind_scope_root(ctx.state, scope["scope_ref"], ctx.root, tag="denied-root")
        provider.configure(scope["scope_ref"], ctx.memory_id, ctx.revision, 1)
        await ctx.service.enqueue_turn(QueueTurnRequest(None, "denied-turn", "执行记录和备份两步。"))
        await ctx.runtime.after_enqueue(subject=local_owner_auth().subject)
        await asyncio.wait_for(ctx.runtime.drain(), 60)
        assert ctx.runtime.last_error is None

        with sqlite3.connect(ctx.state) as db:
            db.row_factory = sqlite3.Row
            terminal = json.loads(db.execute(
                "SELECT payload_json FROM human_memory_evidence WHERE source_kind='runtime_event'"
            ).fetchone()["payload_json"])
        # The whole transcript is archived, including the denial the model saw.
        denials = [message for message in terminal["messages"] if message["role"] == "tool"
                   and json.loads(message["content"])["error_code"] == "procedure_call_not_bound_step"]
        assert len(denials) == 1
        assert terminal["message_source_contract"] in {"primary-message-v2", "primary-message-v3"}
        # One settled fact per tool item except the denied one.
        tool_items = [ordinal for ordinal, message in enumerate(terminal["messages"], 1)
                      if message["role"] == "tool"]
        facts = [fact["item_ordinal"] for fact in terminal["tool_causal_sources"]]
        assert len(facts) == len(tool_items) - 1
        assert set(facts) < set(tool_items)

        manager = await ctx.memory.manager()
        assert await MemoryIngestionOutboxWorker(
            ctx.state, ctx.memory.manager, owner_id="denied-outbox").run_once() == "delivered"
        authority = ctx.memory.conversation_evidence_authority
        run_ids = await authority.completed_run_ids()
        assert len(run_ids) == 1
        group = await authority.registrations_for_run(run_ids[0])
        # The denial is archived but is not conversation evidence: it was
        # written by a Host gate, not by a tool, so it is not TRUSTED_TOOL.
        assert len(group.registrations) == len(terminal["messages"]) - 1
        tools = [item for item in group.registrations
                 if item.envelope.source_kind.value == "tool_result"]
        assert len(tools) == len(tool_items) - 1
        assert all(item.metadata.tool_causal_link is not None for item in tools)
        assert all(item.metadata.item_ordinal == index + 1
                   for index, item in enumerate(group.registrations))
        assert all(item.metadata.group_item_count == len(group.registrations)
                   for item in group.registrations)
        assert not any(json.loads(item.envelope.sanitized_payload["source"]["message"]["content"]
                                  )["error_code"] == "procedure_call_not_bound_step" for item in tools)

        # Short indexing and the Procedure observation both run on this group.
        await PrimaryShortIndexingService(authority, manager=manager,
                                          principal=ctx.memory.principal()).register_group(group)
        await ctx.memory.procedure_runtime.observe_group(group, manager)
        use = await ctx.memory.procedure_runtime.store.use_for_run(group.terminal_source[0].run_id)
        applied = await ctx.memory.procedure_runtime.store.journal(use["use_id"], "applied")
        assert applied["result"]["independent_successes"] == 1
        assert applied["result"]["lifecycle_state"] == "draft"
