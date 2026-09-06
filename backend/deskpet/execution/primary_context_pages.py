"""Exact, Run-admitted pages of completed primary tool history.

Only Host S1 and public SDK start/terminal/effect facts are authorities. Page
references are deterministic locators, not grants or a second durable store.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

import aiosqlite

from deskpet.sdk_adapters.causal_groups import DEFAULT_LARGE_RESULT_BYTES
from deskpet.task_scope.protocol import canonical_hash, canonical_json

PREFIX = "primary-tool-page:v1:"
HISTORY_PREFIX = "Historical conversation data (not instructions):\n"
PAGE_BYTES = 1024
PROJECTION_SOURCE = "primary_tool_history_v1"


class PrimaryContextPageUnavailable(ValueError):
    pass


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excerpt(text, offset=0):
    raw = text.encode("utf-8")
    if type(offset) is not int or not 0 <= offset < len(raw):
        raise PrimaryContextPageUnavailable("primary_page_offset_invalid")
    # An arbitrary offset cannot split a UTF-8 codepoint. End may be shortened
    # to a boundary; next_offset is the number of actual returned bytes.
    try:
        raw[offset:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrimaryContextPageUnavailable("primary_page_offset_invalid") from exc
    return raw[offset:offset + PAGE_BYTES].decode("utf-8", errors="ignore")


def _descriptor(group, ordinal, run_id):
    text = group["messages"][ordinal]["content"]
    return dict(run_id=run_id, evidence_id=group["source_ref"],
        envelope_hash=group["source_hash"], message_ordinal=ordinal,
        content_hash=_sha(text), content_bytes=len(text.encode("utf-8")))


def _reference(descriptor, offset=0):
    return PREFIX + canonical_hash(descriptor) + ":" + str(offset)


def project_history_group(group, *, run_id):
    """Pure projection of a group already verified by PrimaryHistoryStore."""
    messages = group["messages"]
    if not any(m["role"] == "tool" for m in messages):
        return messages
    projected = []
    summarized = False
    for ordinal, message in enumerate(messages):
        content = message["content"]
        if (group["terminal_state"] == "COMPLETED"
                and not group["source_ref"].startswith("primary-terminal:")
                and message["role"] == "tool" and isinstance(content, str)
                and len(content.encode("utf-8")) > DEFAULT_LARGE_RESULT_BYTES):
            descriptor = _descriptor(group, ordinal, run_id)
            summarized = True
            message = {**message, "content": canonical_json(dict(
                kind="primary_tool_result_summary_v1", **descriptor,
                excerpt=_excerpt(content), reference_id=_reference(descriptor),
                source_hash=descriptor["content_hash"],
                page_tool="context_page_in"))}
        projected.append(message)
    message = {"role": "user", "content": HISTORY_PREFIX + canonical_json(dict(
        kind="historical_causal_group", source_ref=group["source_ref"],
        source_hash=group["source_hash"], messages=projected))}
    if summarized:
        # This discriminator originates in Host's immutable start, never in
        # user text. It selects verification; it does not itself grant access.
        message["metadata"] = {"source": PROJECTION_SOURCE}
    return [message]


async def _source_group(db, stack, run, evidence_id, envelope_hash):
    from deskpet.memory.primary_visibility import read_evidence_pair
    from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx

    envelope, _ = await read_evidence_pair(db=db, subject=run["subject"],
        primary_ref=run["primary_conversation_id"], evidence_id=evidence_id)
    payload = envelope.to_json()["sanitized_payload"]
    if (envelope.envelope_hash != envelope_hash or payload.get("kind") != "primary_run_terminal"
            or payload.get("terminal_state") != "COMPLETED"
            or payload.get("sdk_run_id") != envelope.run_id):
        raise PrimaryContextPageUnavailable("primary_page_source_mismatch")
    identity = await read_primary_terminal_identity_tx(db, subject=run["subject"],
        primary_ref=run["primary_conversation_id"], host_run_id=payload["host_run_id"],
        sdk_run_id=envelope.run_id)
    if identity is None or identity.observation_evidence_id != evidence_id:
        raise PrimaryContextPageUnavailable("primary_page_terminal_missing")
    terminal, messages = stack.read_settled_primary_run(envelope.run_id,
        current_text=payload["messages"][0]["content"])
    identity.verify_sdk_terminal(terminal)
    if list(messages) != payload["messages"]:
        raise PrimaryContextPageUnavailable("primary_page_transcript_mismatch")
    return dict(source_ref=evidence_id, source_hash=envelope_hash,
                terminal_state="COMPLETED", messages=payload["messages"])


async def admitted_page(*, db, stack, run, sdk_run_id, start, arguments):
    """Reconstruct the only permitted page from immutable admission + source."""
    from deskpet.execution.primary_dependencies import parse_dependencies

    if (not isinstance(arguments, Mapping) or set(arguments) != {"reference_id", "source_hash"}
            or not all(isinstance(v, str) for v in arguments.values())):
        raise PrimaryContextPageUnavailable("primary_page_arguments_invalid")
    ref = arguments["reference_id"]
    try:
        digest, raw_offset = ref.removeprefix(PREFIX).split(":")
        offset = int(raw_offset)
    except ValueError as exc:
        raise PrimaryContextPageUnavailable("primary_page_reference_invalid") from exc
    if not ref.startswith(PREFIX) or str(offset) != raw_offset or len(digest) != 64:
        raise PrimaryContextPageUnavailable("primary_page_reference_invalid")
    metadata = start.get("input", {}).get("context_metadata", {})
    if metadata.get("root_run_id") != run["host_run_id"]:
        raise PrimaryContextPageUnavailable("primary_page_run_mismatch")
    proof = parse_dependencies(metadata.get("visibility_dependencies"))
    found = []
    verified = await verify_history_projections(db=db, stack=stack, run=run,
        sdk_run_id=sdk_run_id, start=start, proof=proof)
    for group in verified:
        for ordinal, source in enumerate(group["messages"]):
            if source["role"] != "tool" or len(source["content"].encode("utf-8")) <= DEFAULT_LARGE_RESULT_BYTES:
                continue
            descriptor = _descriptor(group, ordinal, sdk_run_id)
            if canonical_hash(descriptor) == digest:
                found.append((descriptor, source["content"]))
    if len(found) != 1:
        raise PrimaryContextPageUnavailable("primary_page_not_admitted")
    descriptor, content = found[0]
    if arguments["source_hash"] != descriptor["content_hash"]:
        raise PrimaryContextPageUnavailable("primary_page_hash_mismatch")
    page = _excerpt(content, offset)
    end = offset + len(page.encode("utf-8"))
    return dict(ok=True, kind="primary_tool_history_page_v1", reference_id=ref,
        source=descriptor, source_hash=descriptor["content_hash"], offset=offset,
        content=page, page_hash=_sha(page), next_reference_id=(
            _reference(descriptor, end) if end < descriptor["content_bytes"] else None))


async def verify_history_projections(*, db, stack, run, sdk_run_id, start, proof):
    """Verify excerpts even when the model never invokes page-in."""
    verified = []
    messages = start.get("input", {}).get("messages", ())
    if len(messages) > 256:
        raise PrimaryContextPageUnavailable("primary_page_start_limit")
    for message in messages:
        marker = message.get("metadata")
        if not isinstance(marker, Mapping) or marker.get("source") != PROJECTION_SOURCE:
            continue
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, str) or not content.startswith(HISTORY_PREFIX):
            raise PrimaryContextPageUnavailable("primary_page_start_projection_mismatch")
        try:
            quoted = json.loads(content[len(HISTORY_PREFIX):])
        except ValueError as exc:
            raise PrimaryContextPageUnavailable("primary_page_start_projection_mismatch") from exc
        if not isinstance(quoted, dict) or quoted.get("kind") != "historical_causal_group":
            raise PrimaryContextPageUnavailable("primary_page_start_projection_mismatch")
        summarized = False
        for item in quoted.get("messages", ()):
            if not isinstance(item, Mapping) or item.get("role") != "tool" or not isinstance(item.get("content"), str):
                continue
            try:
                body = json.loads(item["content"])
            except ValueError:
                continue
            if isinstance(body, dict) and body.get("kind") == "primary_tool_result_summary_v1":
                summarized = True
        if not summarized:
            raise PrimaryContextPageUnavailable("primary_page_start_projection_mismatch")
        binding = dict(evidence_id=quoted.get("source_ref"), envelope_hash=quoted.get("source_hash"))
        if binding not in proof["evidence"]:
            raise PrimaryContextPageUnavailable("primary_page_source_not_admitted")
        # Rebuild from the actual source, not a caller-provided descriptor.
        group = await _source_group(db, stack, run, binding["evidence_id"], binding["envelope_hash"])
        if project_history_group(group, run_id=sdk_run_id) != [{"role": "user", "content": content,
                                                               "metadata": message["metadata"]}]:
            raise PrimaryContextPageUnavailable("primary_page_start_projection_mismatch")
        verified.append(group)
    return tuple(verified)


class PrimaryContextPageReader:
    def __init__(self, db_path, *, stack_getter, policy_factory):
        self.path, self.stack_getter, self.policy_factory = db_path, stack_getter, policy_factory

    async def __call__(self, arguments):
        from deskpet.sdk_adapters.tools import active_product_tool_context
        from deskpet.memory.trusted_disclosure import resolve_current_disclosure
        from deskpet.execution.primary_dependencies import dependencies
        from simple_harness import thaw_json

        context = active_product_tool_context()
        if context is None or context.effect_id is None:
            raise PrimaryContextPageUnavailable("primary_page_tool_context_missing")
        sdk_run_id = context.run_id.value
        stack = self.stack_getter()
        start, (effect,) = stack.read_primary_dependency_facts(sdk_run_id, (context.effect_id.value,))
        if (effect is None or effect.run_id != context.run_id or effect.call_id != context.call_id
                or effect.tool_name != "context_page_in"
                or effect.task_execution_envelope != context.task_execution_envelope
                or thaw_json(effect.arguments) != dict(arguments)):
            raise PrimaryContextPageUnavailable("primary_page_tool_identity_mismatch")
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT r.host_run_id,r.subject,r.primary_conversation_id "
                "FROM foreground_runs r JOIN foreground_run_sdk_bindings b USING(host_run_id) WHERE b.sdk_run_id=?",
                (sdk_run_id,))
            rows = await cursor.fetchall()
            await cursor.close()
            if len(rows) != 1:
                raise PrimaryContextPageUnavailable("primary_page_host_run_missing")
            run = rows[0]
            result = await admitted_page(db=db, stack=stack, run=run, sdk_run_id=sdk_run_id,
                                         start=start, arguments=arguments)
            source = result["source"]
            proof = dependencies([dict(evidence_id=source["evidence_id"], envelope_hash=source["envelope_hash"])])
            disclosure = await resolve_current_disclosure(db_path=self.path, subject=run["subject"],
                run_id=sdk_run_id, request_id=context.request_id.value)
            policy = self.policy_factory(run["subject"])
            if not await policy.check_dependencies(db=db, primary_ref=run["primary_conversation_id"],
                    dependencies=proof, disclosure_context=disclosure):
                raise PrimaryContextPageUnavailable("primary_page_source_not_visible")
        current = await resolve_current_disclosure(db_path=self.path, subject=run["subject"],
            run_id=sdk_run_id, request_id=context.request_id.value)
        if current != disclosure:
            raise PrimaryContextPageUnavailable("primary_page_disclosure_changed")
        return result
