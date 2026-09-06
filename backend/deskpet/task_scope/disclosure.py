"""Deterministic ordinary projection of an owned canonical TaskScope.

Workspace authority continues to consume the original binding internally. This
reader never restamps old text as new evidence and never adds an authority ledger.
"""
from __future__ import annotations

import json
import hashlib
import aiosqlite
from simple_harness import thaw_json
from deskpet.task_scope.protocol import canonical_hash, canonical_json
from deskpet.execution.primary_dependencies import dependencies, parse_dependencies
from contextvars import ContextVar

_active_sources = ContextVar("scope_disclosure_sources", default=())


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _render_scope_disclosure(*, db_path, package, subject, stack, policy=None,
                                  disclosure_context=None, selected=None):
    """Read exact immutable source, derive actual bytes plus complete dependencies.

    selected pins a previously retained set at verification; becoming newly
    visible cannot silently inject another field into a frozen SDK request.
    """
    from deskpet.task_scope.search import TaskScopeSearchStore
    opened = await TaskScopeSearchStore(db_path).open_exact(subject=subject,
        allowed_scope_ids=(package["task_scope_id"],), task_scope_id=package["task_scope_id"],
        source_id=package["source_id"], materialized_only=True)
    original = opened.resume_package
    if any(original[k] != package[k] for k in ("source_id", "source_hash", "task_scope_id",
            "canonical_revision", "binding_set_revision", "binding_receipt_hash")):
        raise ValueError("scope_disclosure_source_mismatch")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT r.state_json,s.state_hash FROM task_scope_canonical_revisions r JOIN task_scope_projection_sources s ON s.task_scope_id=r.task_scope_id AND s.canonical_revision=r.revision WHERE s.source_id=?",
            (package["source_id"],))
        row = await cursor.fetchone()
        state = json.loads(row[0])
        if canonical_hash(state) != row[1]:
            raise ValueError("scope_disclosure_state_hash_mismatch")
        # Only successful, actually bound production CREATE_NEW can explain the
        # original title/goal. Mere equality with today's USER is never proof.
        cursor = await db.execute("SELECT d.receipt_json FROM context_route_decisions d JOIN foreground_run_sdk_bindings b ON b.sdk_run_id=d.sdk_run_id JOIN foreground_runs r ON r.host_run_id=b.host_run_id WHERE d.task_scope_id=? AND d.route='create_new' AND d.origin='context_tool' AND r.subject=? ORDER BY d.rowid LIMIT 257",
            (package["task_scope_id"], subject))
        routes = [json.loads(r[0]) for r in await cursor.fetchall()]
        if len(routes) > 256:
            raise ValueError("scope_disclosure_limit")
        fields = {}
        for route in routes:
            producer_start, (effect,) = stack.read_primary_dependency_facts(route["run_id"], (route["effect_id"],))
            if effect is None or not effect.terminal or effect.result is None:
                continue
            value = thaw_json(effect.result.value)
            args = thaw_json(effect.arguments)
            if (effect.tool_name != "context_route" or effect.run_id.value != route["run_id"]
                    or effect.raw_call_id != route["raw_call_id"]
                    or value.get("context_route_receipt") != route):
                raise ValueError("scope_disclosure_producer_mismatch")
            if producer_start.get("input", {}).get("context_metadata", {}).get("primary_effect_index_version") != 1:
                continue  # old producer had no complete unscoped effect index
            raw = value.get("producer_dependencies")
            if raw is None:
                continue  # preserved legacy producer, not a new fabricated proof
            proof = parse_dependencies(raw)
            # Rebuild the exact consumed prefix, excluding this producer itself.
            from deskpet.execution.primary_dependencies import read_run_dependencies
            found = await read_run_dependencies(db=db, stack=stack, sdk_run_id=route["run_id"],
                                                before_effect_id=route["effect_id"])
            if found is None or found[1] != proof:
                raise ValueError("scope_disclosure_producer_dependencies_mismatch")
            if policy is not None and not await policy.check_dependencies(db=db,
                    primary_ref=found[0]["primary_conversation_id"], dependencies=proof,
                    disclosure_context=disclosure_context):
                continue
            candidates = {"title": str(args.get("title") or "").strip(),
                          "goal": str(args.get("goal") or "").strip() or str(args.get("title") or "").strip()}
            for name, text in candidates.items():
                if name == "goal" and any(op.get("kind") in {"goal.set", "goal.revise"} for op in state.get("operations", ())):
                    continue  # same text after mutation does not preserve original lineage
                if state.get(name) == text and (selected is None or name in selected):
                    text = text.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
                    fields[name] = {"text": text, "utf8_sha256": text_hash(text),
                        "producer_run_id": route["run_id"], "producer_effect_id": route["effect_id"],
                        "producer_receipt_hash": canonical_hash(route), "dependencies": proof}
    # Enums and numeric identity facts only; no title, reason, root path, or raw
    # checkpoint/binding row can enter through the structural channel.
    structural = {k: original[k] for k in ("task_scope_id", "source_id", "source_hash",
        "canonical_revision", "event_watermark", "binding_set_revision", "binding_receipt_hash",
        "checkpoint_sequence", "checkpoint_set_root")}
    if state.get("status") in {"active", "paused", "blocked", "complete"}:
        structural["status"] = state["status"]
    missing = [{"field": name, "reason": "original_sources_unavailable"} for name in
               ("title", "goal", "operations", "resume", "checkpoint_metadata", "binding_paths") if name not in fields]
    retained = {"structure": structural, "fields": {name: row["text"] for name, row in fields.items()},
                "missing_fields": missing}
    body = {"schema_version": 1, "source_ref": original["source_id"], "source_hash": original["source_hash"],
        "original_package_hash": opened.resume_package_hash,
        "original_view_hashes": {k: v["content_sha256"] for k, v in original["read_views"].items()},
        "retained_utf8_hash": text_hash(canonical_json(retained)), "fragments": fields,
        "missing_fields": missing,
        "dependencies": dependencies([v for row in fields.values() for v in row["dependencies"]["evidence"]],
                                     [v for row in fields.values() for v in row["dependencies"]["recall"]],
                                     [v for row in fields.values() for v in row["dependencies"].get("short_horizon", ())],
                                     schema_version=max((row["dependencies"]["schema_version"] for row in fields.values()), default=1))}
    manifest = {**body, "manifest_hash": canonical_hash(body)}
    return {**structural, "schema_version": 1, "disclosure": retained, "disclosure_manifest": manifest}


async def render_scope_disclosure(**kwargs):
    identity = (kwargs["subject"], kwargs["package"]["source_id"])
    active = _active_sources.get()
    if identity in active or len(active) >= 32:
        raise ValueError("scope_disclosure_source_cycle_or_depth")
    token = _active_sources.set((*active, identity))
    try:
        return await _render_scope_disclosure(**kwargs)
    finally:
        _active_sources.reset(token)


async def verify_scope_disclosure(*, db_path, package, subject, stack):
    manifest = package.get("disclosure_manifest")
    if not isinstance(manifest, dict):
        raise ValueError("scope_disclosure_manifest_missing")
    expected = await render_scope_disclosure(db_path=db_path, package=package, subject=subject,
        stack=stack, selected=set(manifest.get("fragments", {})))
    if expected != package:
        raise ValueError("scope_disclosure_manifest_mismatch")
    return parse_dependencies(manifest["dependencies"])


class ScopeDisclosureReader:
    def __init__(self, db_path, *, stack_getter, policy_factory):
        self.db_path, self.stack_getter, self.policy_factory = db_path, stack_getter, policy_factory

    async def producer_dependencies(self, run_id):
        from deskpet.execution.primary_dependencies import read_run_dependencies
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            found = await read_run_dependencies(db=db, stack=self.stack_getter(), sdk_run_id=run_id)
        if found is None:
            return None  # legacy non-primary producer cannot assert new provenance
        return found[1]

    async def read(self, run_id, package, effect_id):
        from deskpet.memory.trusted_disclosure import resolve_current_disclosure
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT r.subject FROM foreground_runs r JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id WHERE b.sdk_run_id=?", (run_id,))
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("scope_disclosure_run_missing")
        return await render_scope_disclosure(db_path=self.db_path, package=package, subject=row["subject"],
            stack=self.stack_getter(), policy=self.policy_factory(row["subject"]),
            disclosure_context=await resolve_current_disclosure(db_path=self.db_path,
                run_id=run_id, subject=row["subject"], request_id=effect_id))
