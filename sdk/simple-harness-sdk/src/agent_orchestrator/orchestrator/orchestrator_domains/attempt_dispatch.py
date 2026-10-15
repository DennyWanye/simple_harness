# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Attempt dispatch, source and workspace binding, agent and critic binding."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from simple_harness.agents.arp.errors import ArpError

from ...artifacts.store import ArtifactStoreError, read_nofollow, read_verified
from ...artifacts.taskgraph_inputs import decode_target_rules
from ...artifacts.versioning import (
    ArtifactConflict,
    UpstreamInput,
    manifest_upstream_inputs,
    materialise_v2,
)
from ...artifacts.workspace import WorkspaceError
from ...assurance.codec import AssuranceError
from ...contracts import (
    TERMINAL_ATTEMPT,
    TERMINAL_MISSION,
    Attempt,
    ContractError,
    Mission,
    MissionStopReason,
)
from ...contracts.models import sha256_hex
from ...runtime.tool_gateway import CRITIC_TOOLS, WORKER_TOOLS, WorkspaceBinding
from ...storage import planning_human_store
from ...storage.store import DispatchIntent, StoreError
from ...storage.taskgraph_binding import require_bound
from ...verification.evidence_resolver import EvidenceResolver, in_source_roots
from .. import planning_selection
from ..assurance_review_transport import require_review_handoff
from ..occurrence_tasks import read_only_existing_paths
from ..planning_runtime_block import blocks_intent, resume_source_current
from ..planning_selection import dispatch_local
from ..taskgraph_materialization import require_mounts, verify_materialized
from .core import OrchestratorCore
from .support import sha256_hex_text


class AttemptDispatchMixin(OrchestratorCore):
    """Attempt dispatch, source and workspace binding, agent and critic binding."""

    __slots__ = ()

    async def _dispatch(self, intent: DispatchIntent) -> bool:
        """ORCH §4.3 steps 2–3 with the identity frozen in the intent (D5')."""

        self._require_assurance_execution_root()

        try:
            require_review_handoff(self.commit, intent)
        except AssuranceError as error:
            self._note(f"intent {intent.intent_id}: Assurance handoff waits ({error.code})")
            return False
        mission = self.store.get_mission(intent.mission_id)
        if mission is not None and self._assembly_missing(mission, at="dispatch"):
            return False

        if blocks_intent(self.store, intent):
            return False
        if (
            intent.kind == "plan"
            and mission is not None
            and not resume_source_current(self, intent, mission)
        ):
            return False

        if intent.kind != "critic" and planning_human_store.PlanningHumanStore(self.store).pending(
            intent.mission_id
        ):
            # Existing handed-off work is collected normally; do not start another
            # plan or Worker while a blocking human question is pending.
            return False
        if intent.kind == "critic" and self._critic_subject_stopped(intent):
            return await self._collect_stopped_critic(intent)

        try:
            with self.store.read_view():
                attempt = (
                    self.store.get_attempt(intent.subject_id) if intent.kind == "attempt" else None
                )
                # Terminal cleanup below does no handoff. Do not strand its
                # original intent/accounting behind the newly installed fence.
                if attempt is None or attempt.status not in TERMINAL_ATTEMPT:
                    self.commit.require_taskgraph_handoff(intent)
        except StoreError:
            self._note(f"intent {intent.intent_id}: TaskGraph handoff is currently blocked")
            return False

        if planning_selection.awaits_authority(self.store, intent):
            return False
        if intent.config.get("native_planning_decision") is None and self._pool_missing(intent):
            return False
        self._context_profile_for(intent.config)
        claimed = self.commit.claim_intent(
            intent.intent_id, owner=self._owner, lease_seconds=self._config.lease_seconds
        )
        if claimed is None:
            return False
        config = claimed.config
        if config.get("native_planning_decision") is not None:
            return await dispatch_local(self, claimed)
        if claimed.kind == "attempt":
            attempt = self.store.get_attempt(claimed.subject_id)
            assert attempt is not None
            if attempt.status in TERMINAL_ATTEMPT:  # cancelled / superseded before it ran
                self.commit.settle_intent(claimed.intent_id, "FAILED")
                return True
            try:
                self._bind_workspace(attempt)
            except ArtifactConflict as error:  # an upstream artifact file moved / changed
                self.commit.settle_intent(claimed.intent_id, "FAILED")
                self.commit.mark_attempt_lost(attempt.id, reason="upstream_artifact_missing")
                self._commit_stop_task(
                    attempt.task_id,
                    stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                    detail={"error": str(error)},
                )
                await self._release_mission(attempt.mission_id)
                self._note(f"attempt {attempt.id}: upstream artifacts unusable → stopped")
                return True
        if claimed.state == "CLAIMED":
            try:
                require_review_handoff(self.commit, claimed)
            except AssuranceError as error:
                self._note(f"intent {claimed.intent_id}: Assurance create waits ({error.code})")
                return False

            try:
                agent_id, _run_id, _ = await self.bridge_for(claimed).create(
                    creation_key=claimed.creation_key,
                    config_json=config["agent_config"],
                    intent=claimed,
                )
            except ArpError as error:
                # NEXT-TG-1.0 §10: a named refusal of the native plane (e.g. the Mission
                # sources moved) stops this intent only — never the loop every other
                # Mission runs on.  The intent stays claimed for its own recovery paths.
                if claimed.intent_id not in self._attempts.creation_refusals_noted:
                    self._attempts.creation_refusals_noted.add(claimed.intent_id)
                    self._note(f"intent {claimed.intent_id}: Agent creation refused ({error.code})")
                return False
            expected = await self.bridge_for(claimed).expected_turn_id(
                agent_id=agent_id, input_id=claimed.input_id
            )
            self._fault("after_agent_created", claimed.kind)
            claimed = self.commit.record_agent_created(
                claimed.intent_id, agent_id=agent_id, expected_turn_id=expected
            )
            if claimed.kind == "critic" or self._assured_review_intent(claimed):
                self._bind_critic(agent_id, config)
            elif claimed.kind == "attempt":
                self._bind_agent(agent_id, config)
        if claimed.state == "AGENT_CREATED":
            assert claimed.agent_id is not None
            try:
                require_review_handoff(self.commit, claimed)
            except AssuranceError as error:
                self._note(f"intent {claimed.intent_id}: Assurance submit waits ({error.code})")
                return False
            if claimed.kind == "critic" or self._assured_review_intent(claimed):
                if self._critic_subject_stopped(claimed):
                    return await self._collect_stopped_critic(claimed)
                self._bind_critic(claimed.agent_id, config)
            elif claimed.kind == "attempt":
                self._bind_agent(claimed.agent_id, config)
            try:
                with self.store.read_view():
                    self.commit.require_taskgraph_handoff(claimed)
            except StoreError:
                self._note(f"intent {claimed.intent_id}: TaskGraph submit is currently blocked")
                return False
            try:
                receipt = await self.bridge_for(claimed).submit(
                    agent_id=claimed.agent_id,
                    input_id=claimed.input_id,
                    message_json=config["message"],
                )
            except Exception as error:  # noqa: BLE001 - the created Agent is gone (P0-3)
                if claimed.kind != "attempt":
                    raise
                self._note(f"{claimed.subject_id}: created executor unreachable ({error})")
                self.commit.mark_attempt_lost(claimed.subject_id, reason="executor_agent_missing")
                self.commit.settle_intent(claimed.intent_id, "FAILED")
                await self._release_attempt(claimed.subject_id, cancel=False)
                return True
            self._fault("after_submit", claimed.kind)
            self.commit.record_submitted(claimed.intent_id, receipt=receipt)
            self._note(f"dispatched {claimed.kind} {claimed.subject_id} → agent {claimed.agent_id}")
        return True

    def _upstream_inputs(self, attempt: Attempt) -> list[UpstreamInput]:
        context = self.commit.taskgraph_attempt_context(attempt.mission_id, attempt.id)
        return list(context.upstream)

    def _active_source_binding(self, mission_id: str) -> dict[str, Any]:
        domain = self.commit.domain_for(mission_id)
        if not domain.source_roots:
            return {}

        # Only what the reader will accept is frozen: registered rows outside the
        # domain's source roots (e.g. Assurance review output) are not Mission sources.
        return {
            "source_versions": {
                row["path"]: row["version_hash"]
                for row in self.store.list_sources(mission_id, active_only=True)
                if in_source_roots(row["path"], domain.source_roots)
            },
            "source_roots": list(domain.source_roots),
        }

    def _frozen_source_binding(self, attempt: Attempt) -> dict[str, Any]:
        intent = self.store.get_intent_for_subject(attempt.id)
        if intent is None or "source_versions" not in intent.config:
            return {}  # Legacy intents never acquire today's source registry.
        return {
            "source_versions": dict(intent.config["source_versions"]),
            "source_roots": list(intent.config.get("source_roots", ())),
        }

    def _current_source_files(self, mission: Mission) -> dict[str, bytes]:
        """The Mission's registered sources at their current versions, read back from the
        CAS: what the judgment tree carries alongside the accepted outputs."""

        roots = tuple(self.commit.domain_for(mission.id).source_roots)
        rows = [
            row
            for row in self.store.list_sources(mission.id, active_only=True)
            if roots and in_source_roots(str(row["path"]), roots)
        ]
        if not rows:
            return {}
        self.validate_source_storage(mission.id)
        resolver = EvidenceResolver(self.store, self.assembled.workspaces.artifact_store)
        files: dict[str, bytes] = {}
        for row in rows:
            source = resolver.read_source(
                tenant_id=mission.tenant_id,
                mission_id=mission.id,
                path=str(row["path"]),
                version=str(row["version_hash"]),
                source_roots=roots,
            )
            if source.status != "resolved" or source.data is None:
                raise ArtifactConflict(f"current source {row['path']} is {source.status}")
            files[str(row["path"])] = source.data
        return files

    def _source_files(self, attempt: Attempt) -> dict[str, bytes]:

        binding = self._frozen_source_binding(attempt)
        versions = binding.get("source_versions", {})
        if not versions:
            return {}
        self.validate_source_storage(attempt.mission_id)
        mission = self.store.get_mission(attempt.mission_id)
        assert mission is not None
        resolver = EvidenceResolver(self.store, self.assembled.workspaces.artifact_store)
        files: dict[str, bytes] = {}
        for path, version in versions.items():
            source = resolver.read_source(
                tenant_id=mission.tenant_id,
                mission_id=mission.id,
                path=path,
                version=version,
                source_roots=binding["source_roots"],
            )
            if source.status != "resolved" or source.data is None:
                raise ArtifactConflict(f"frozen source {path} is {source.status}")
            files[path] = source.data
        return files

    def _taskgraph_tool_handoff(self, binding: Any) -> None:
        self._require_assurance_execution_root()
        if binding.view != "work":
            return

        with self.store.read_view():
            attempt = self.store.get_attempt(binding.attempt_id)
            if attempt is None:
                raise StoreError("TASKGRAPH_TOOL_ATTEMPT_MISSING")
            require_bound(self.store, attempt.mission_id)
            if binding.mission_id is not None and binding.mission_id != attempt.mission_id:
                raise StoreError("TASKGRAPH_TOOL_MISSION_MISMATCH")
            intent = self.store.get_intent_for_subject(attempt.id)
            if intent is None or attempt.status in TERMINAL_ATTEMPT:
                raise StoreError("TASKGRAPH_TOOL_ATTEMPT_STOPPED")
            self.commit.require_taskgraph_handoff(intent)

    def _taskgraph_mount_rules(self, attempt: Attempt) -> Any:

        self.commit.taskgraph_attempt_context(attempt.mission_id, attempt.id)
        intent = self.store.get_intent_for_subject(attempt.id)
        if intent is None:
            raise ArtifactConflict("TASKGRAPH_MATERIAL_INTENT_MISSING")
        return decode_target_rules(intent.config["taskgraph_inputs"]["target_rules"])

    def _bind_workspace(self, attempt: Attempt) -> None:
        mission = self.store.get_mission(attempt.mission_id)
        assert mission is not None
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        previous = None
        if attempt.retry_of is not None:
            previous = self.assembled.workspaces.root / attempt.retry_of

        graph_rules = self._taskgraph_mount_rules(attempt)
        upstream = self._upstream_inputs(attempt)
        require_mounts(upstream, graph_rules)
        taskgraph_context = self.commit.taskgraph_attempt_context(attempt.mission_id, attempt.id)

        manifest_paths = {
            item.path
            for item in manifest_upstream_inputs(
                taskgraph_context.manifest, graph_rules, network=taskgraph_context.network
            )
        }
        inputs: dict[str, Path | bytes] = {}
        for item in upstream:
            artifact = self.store.get_artifact(item.artifact_id)
            try:  # P3.2 D3: the stored bytes, hash re-checked, never through a symlink
                if (
                    artifact is None
                    or artifact.mission_id != attempt.mission_id
                    or artifact.task_id != item.task_id
                    or artifact.content_hash != item.content_hash
                ):
                    raise ArtifactStoreError("missing", item.path)
                read_verified(artifact)
            except ArtifactStoreError as error:
                raise ArtifactConflict(
                    f"upstream artifact {item.artifact_id} ({item.path}) is missing or changed"
                ) from error
            # TaskGraph DATA is written below by materialise_v2, which derives the
            # exact declared paths from the frozen manifest. Overlay/Selection
            # material remains an explicit, separately verified input so accepted
            # producer files are not silently dropped from a real TaskGraph tree.
            if item.path not in manifest_paths:
                inputs[item.path] = Path(artifact.storage_uri)
        binding = self._frozen_source_binding(attempt)
        source_roots = binding.get("source_roots", ())
        source_files = self._source_files(attempt)
        task = self.store.get_task(attempt.task_id)
        require_mounts(upstream, graph_rules, supplementary=source_files)
        combined = [
            *upstream,
            *(
                UpstreamInput("source", path, sha256_hex_text(data), "source")
                for path, data in source_files.items()
                if path not in {item.path for item in upstream}
            ),
        ]
        require_mounts(combined, graph_rules)
        inputs.update(source_files)
        # P3.2 D4 (review round 2 P2-4): a rebind — recover() and every dispatch — is
        # checked against the registered identity, never the directory's content
        base = sha256_hex(
            {
                "seed": {path: sha256_hex_text(content) for path, content in seed.items()},
                "inputs": {item.path: item.content_hash for item in self._upstream_inputs(attempt)},
                "previous": attempt.retry_of,
                **self._frozen_source_binding(attempt),
            }
        )
        detail = {
            "path": attempt.id,
            "seed": sorted(seed),
            "read_only_inputs": sorted(self._read_only_inputs(attempt.id)),
            "bound_workspace_files": sorted(item.path for item in self._upstream_inputs(attempt)),
            "writable_outputs": [] if task is None else list(task.outputs),
            "adopted": False,
        }
        record = self.store.get_workspace(attempt.id)
        root_path = self.assembled.workspaces.root / attempt.id
        if root_path.is_symlink():  # code review round 1 P2-10: never adopt a link as a tree
            raise ArtifactConflict(
                f"workspace_identity_mismatch: {attempt.id} is a symlink, not a workspace"
            )
        exists = root_path.exists()
        building = False
        if record is None and exists:
            raise ArtifactConflict("TASKGRAPH_UNREGISTERED_WORKSPACE")
        elif record is None or record["state"] == "CREATING":
            if record is not None:  # a tree half-made by a crash is rebuilt
                self.assembled.workspaces.remove(attempt.id)
            self.store.register_workspace(
                attempt.id,
                kind="attempt",
                mission_id=attempt.mission_id,
                attempt_id=attempt.id,
                base_snapshot=base,
                state="CREATING",
                detail=detail,
            )
            building = True
        elif record["state"] != "ACTIVE" or record["base_snapshot"] != base:
            raise ArtifactConflict(
                f"workspace_identity_mismatch: {attempt.id} is registered "
                f"{record['state']} with another seed, inputs or repair source"
            )
        try:
            workspace = self.assembled.workspaces.create(
                attempt.id,
                seed=seed,
                previous=previous,
                inputs=inputs,
                replace_input_roots=source_roots,
            )
        except WorkspaceError as error:  # P3.2 D3: e.g. a symlink in the previous tree
            raise ArtifactConflict(str(error)) from error
        if building:
            context = taskgraph_context
            artifacts = {}
            # 第三批：交付绑定与端口绑定一样物化（同一份清单、同一道身份复核）
            for entry in (*context.manifest.bindings, *context.manifest.deliveries):
                artifact = self.store.get_artifact(entry.artifact_id)
                if artifact is None:
                    raise ArtifactConflict(
                        f"TaskGraph manifest artifact {entry.artifact_id} is unavailable"
                    )
                # The frozen manifest is an authorization record, not a lookup
                # hint.  Re-check the stored record's mission, producer task and
                # bytes before handing it to the materializer; otherwise a
                # recycled artifact id or a foreign-task record could cross the
                # DATA boundary during recovery.
                if (
                    artifact.mission_id != attempt.mission_id
                    or artifact.task_id != str(entry.producer_task_ref)
                    or artifact.content_hash != entry.content_hash
                ):
                    raise ArtifactConflict(
                        f"TaskGraph manifest artifact {entry.artifact_id} identity changed"
                    )
                artifacts[entry.artifact_id] = artifact
            try:
                materialise_v2(
                    workspace,
                    context.manifest,
                    artifacts,
                    target_rules=graph_rules,
                    network=context.network,
                )
            except (ArtifactConflict, WorkspaceError) as error:
                raise ArtifactConflict(f"TaskGraph DATA materialisation failed: {error}") from error
        if not building:
            # Detect tampering before a protected-file refresh could overwrite it.
            verify_materialized(
                workspace.root,
                [item for item in upstream if task is None or item.path not in task.outputs],
            )
        if task is not None:
            protected_files = self._protected_files(mission, task, attempt)
            require_mounts(
                upstream,
                graph_rules,
                supplementary={
                    path: content if isinstance(content, bytes) else content.encode("utf-8")
                    for path, content in protected_files.items()
                },
            )
            for path, content in protected_files.items():
                if isinstance(content, bytes):
                    if not building:
                        continue  # Preserve ACTIVE-tree tamper evidence across recovery.
                    try:
                        current_bytes = read_nofollow(workspace.root / path)
                    except ArtifactStoreError:
                        current_bytes = None
                    if current_bytes != content:
                        workspace.write_bytes(path, content)
                elif (
                    workspace.read_text(path) != content
                    if (workspace.root / path).is_file()
                    else True
                ):
                    workspace.write_text(path, content)

        # A declared output may legitimately change after initial binding.
        # Recovery still verifies every protected input against its origin.
        verify_materialized(
            workspace.root,
            upstream
            if building
            else [item for item in upstream if task is None or item.path not in task.outputs],
        )
        if building:
            self.store.set_workspace_state(attempt.id, "ACTIVE")

    def _register_copy(
        self, kind: str, name: str, *, mission_id: str, attempt_id: str, detail: dict[str, Any]
    ) -> None:
        """P3.2 D4: a verification copy or judgment tree, registered each time it is rebuilt
        (its identity is what it was rebuilt from)."""

        self.store.register_workspace(
            name,
            kind=kind,
            mission_id=mission_id,
            attempt_id=attempt_id,
            base_snapshot=sha256_hex(detail),
            state="ACTIVE",
            detail={"path": name, **detail},
        )

    def cleanup_workspaces(self) -> list[str]:
        """P3.2 D4: remove the directories of finished Missions once the retention period
        has passed.  The registry rows (now CLEANED) and the content-addressed Artifact
        bytes stay, so snapshots, artifact reads, replay and reconciliation still work."""

        cutoff = self.store.now - self._config.workspace_retention_seconds
        removed: list[str] = []
        for row in self.store.list_workspaces():
            if row["state"] == "CLEANED" or row["updated_at"] > cutoff:
                continue
            mission = self.store.get_mission(row["mission_id"])
            if mission is None or mission.status not in TERMINAL_MISSION:
                continue
            self.assembled.workspaces.remove(str(row["detail"].get("path", row["workspace_id"])))
            self.store.set_workspace_state(row["workspace_id"], "CLEANED")
            removed.append(row["workspace_id"])
        return removed

    def _input_files(self, attempt: Attempt) -> dict[str, bytes]:
        """P3.2 review round 2 P1-3: an Attempt's upstream inputs, read back from the
        store with their hashes re-checked — the verification copy is rebuilt from them."""

        files: dict[str, bytes] = {}
        for item in self._upstream_inputs(attempt):
            artifact = self.store.get_artifact(item.artifact_id)
            try:
                if artifact is None or artifact.content_hash != item.content_hash:
                    raise ArtifactStoreError("missing", item.path)
                files[item.path] = read_verified(artifact)
            except ArtifactStoreError as error:
                raise WorkspaceError(f"upstream input unreadable: {error}") from error
        rules = self._taskgraph_mount_rules(attempt)
        additional = self._source_files(attempt)
        if rules is not None:
            require_mounts(
                [
                    UpstreamInput("frozen", path, sha256_hex_text(data), "frozen")
                    for path, data in files.items()
                ],
                rules,
                supplementary=additional,
            )
        files.update(additional)
        return files

    def _read_only_initial(self, attempt: Attempt) -> dict[str, str]:
        """Path → hash of seed ∪ overlay/upstream.

        Same set ``read_only_rewrites`` uses as ``initial`` and the gateway
        snapshot uses as ``read_only_existing``.  Retry copies of a previous
        Attempt's new outputs are not in this map.
        """

        mission = self.store.get_mission(attempt.mission_id)
        if mission is None:
            raise WorkspaceError("read-only snapshot: mission missing")
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        upstream = self._upstream_inputs(attempt)
        allowed = set(read_only_existing_paths(seed, (item.path for item in upstream)))
        initial: dict[str, str] = {}
        for path, content in seed.items():
            if path in allowed:
                initial[path] = sha256_hex_text(content)
        for item in upstream:
            if item.path in allowed:
                initial[item.path] = item.content_hash
        return initial

    def _schedule_read_only_kept_writing_stop(self, attempt: Attempt) -> None:
        """End the Attempt after consecutive existing-file writes (P2.3u P2-2)."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._stop_read_only_leaf_kept_writing(attempt.id))

    async def _stop_read_only_leaf_kept_writing(self, attempt_id: str) -> None:
        attempt = self.store.get_attempt(attempt_id)
        if attempt is None or attempt.status in TERMINAL_ATTEMPT:
            return
        intent = self.store.get_intent_for_subject(attempt_id)
        if intent is not None:
            await self._cancel_turn(intent)
        attempt = self.store.get_attempt(attempt_id)
        if attempt is None or attempt.status in TERMINAL_ATTEMPT:
            return
        self.commit.mark_attempt_lost(attempt_id, reason="read_only_leaf_kept_writing")
        if intent is not None:
            self._settle_intent(intent, "FAILED")
        self._settle_if_known(attempt)
        await self._release_attempt(attempt_id, cancel=True)
        self._note(f"attempt {attempt_id}: read-only leaf kept writing → LOST")

    def _bind_agent(self, agent_id: str, config: Mapping[str, Any]) -> None:
        cap = config.get("max_tool_calls")
        context_profile = self._context_profile_for(config)
        attempt = self.store.get_attempt(str(config["attempt_id"]))
        if attempt is None:
            raise ContractError("Cannot bind an unknown Attempt")
        if self.commit.domain_for(attempt.mission_id).id == "appworld-v1":
            self.assembled.gateway.bind_appworld(attempt.mission_id)
        read_only_existing: tuple[str, ...] = ()
        read_only_writes_blocked = False
        if config.get("read_only_leaf"):
            # P2.3u: seed ∪ overlay/upstream, the same set ``read_only_rewrites``
            # uses as ``initial``.  A retry copy of the previous Attempt's REPORT.md
            # is not in this snapshot, so the leaf can update its own report.
            try:
                read_only_existing = tuple(sorted(self._read_only_initial(attempt)))
            except WorkspaceError:
                read_only_writes_blocked = True
        self.assembled.gateway.bind(
            agent_id,
            WorkspaceBinding(
                str(config["attempt_id"]),
                "work",
                True,
                tuple(config.get("allowed_tools", WORKER_TOOLS)),
                tuple(str(p) for p in config.get("untrusted_sources", ())),
                max_tool_calls=None if cap is None else int(cap),
                # 批准过的尝试：可申请数按核额度用的基础额度说（opt.171 评估建议 2）
                max_request_amount=(
                    None
                    if not isinstance(config.get("resource_grant"), Mapping)
                    else int(config["resource_grant"]["base_cap"])
                ),
                max_search_calls=(
                    None
                    if config.get("max_search_calls") is None
                    else int(config["max_search_calls"])
                ),
                mission_id=attempt.mission_id,
                protected=self._read_only_inputs(str(config["attempt_id"])),
                protected_prefixes=tuple(config.get("source_roots", ())),
                denied_prefixes=self._config.deployment_policy.denied_path_prefixes,
                context_policy=context_profile.context_policy,
                tokenizer=context_profile.tokenizer,
                read_only_existing=read_only_existing,
                read_only_writes_blocked=read_only_writes_blocked,
            ),
        )

    def _bind_critic(self, agent_id: str, config: Mapping[str, Any]) -> None:
        if config.get("assurance_protocol") == "assurance-exec-v1.1":
            # Frozen initial materials use the Assurance disclosure gate. Never
            # inherit the legacy verify-workspace tool authority implicitly; the
            # assured runtime binds exactly its read-only evidence tools (§4).
            if not config.get("agent_config", {}).get("tool_names"):
                return
            if self._wiring.assurance_reviews is None:
                raise ContractError("Assurance evidence tool binding is not installed")
            self._wiring.assurance_reviews.evidence_tools.bind(agent_id, config)
            return
        context_profile = self._context_profile_for(config)
        self.assembled.gateway.bind(
            agent_id,
            WorkspaceBinding(
                str(config["attempt_id"]),
                "verify",
                False,
                tuple(t for t in CRITIC_TOOLS if t in self._config.deployment_policy.allowed_tools),
                tuple(str(p) for p in config.get("untrusted_sources", ())),
                protected_prefixes=tuple(config.get("source_roots", ())),
                denied_prefixes=self._config.deployment_policy.denied_path_prefixes,
                context_policy=context_profile.context_policy,
                tokenizer=context_profile.tokenizer,
            ),
        )
