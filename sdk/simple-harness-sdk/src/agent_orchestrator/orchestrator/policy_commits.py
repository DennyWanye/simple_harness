# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The Commit Service's policy half (plan D9-2' / D9-3'): the policy version a library
is seeded with and the version every Mission is bound to.  A mixin of ``CommitService``,
so the single writer keeps writing every row and event inside a Store transaction
(ORCH §2).

Deployment-level facts live on the sentinel timeline ``deployment``; every event key
carries something unique (an activation sequence number, a configuration hash) so a
repeated change is never swallowed by idempotency (review P1-7)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contracts.models import sha256_hex
from ..governance.promotion import (
    DEPLOYMENT_TIMELINE,
    NON_PROMOTABLE,
    interpreter_versions,
    params_hash,
)
from ..governance.promotion import (
    version_id as make_version_id,
)
from ..storage.assurance_changes import original_source_mutation

if TYPE_CHECKING:
    from ..contracts import Event
    from ..storage.store import Store

POLICY_PATH_PREFIX = "policy/"  # plan D9-10': files an Agent may write but never apply
CONFIG_FILE_NAMES = frozenset(
    {"deployment.json", "deployment_policy.json", "orchestrator_config.json"}
)


class PolicyCommitsMixin:
    if TYPE_CHECKING:
        _store: Store

        def _emit(
            self,
            event_type: str,
            mission_id: str,
            *,
            key: str,
            task_id: str | None = None,
            attempt_id: str | None = None,
            payload: Mapping[str, Any] | None = None,
            actor_type: str = ...,
            actor_id: str = ...,
        ) -> Event: ...

    # ------------------------------------------------------------ helpers
    def _policy_event(
        self,
        kind: str,
        key: str,
        payload: Mapping[str, Any],
    ) -> None:
        self._emit(kind, DEPLOYMENT_TIMELINE, key=key, payload=dict(payload))

    def _version_record(
        self,
        params: Mapping[str, Any],
        *,
        source: str,
        status: str,
        detail: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "version_id": make_version_id(params),
            "params_hash": params_hash(params),
            "params": dict(params),
            "source": source,
            "status": status,
            "interpreter_versions": interpreter_versions(),
            "detail": dict(detail or {}),
        }

    # ------------------------------------------------------------ seed and binding
    def seed_policy(
        self,
        params: Mapping[str, Any],
        *,
        source: str = "seed",
        detail: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The ACTIVE version of a library: the resolved built-in policy (plan D9-3').
        A library that already has an ACTIVE version keeps it."""

        with original_source_mutation(self._store, writer="PolicyCommitsMixin.seed_policy"):
            active = self._store.active_policy()
            if active is not None:
                return active
            record = self._version_record(params, source=source, status="ACTIVE", detail=detail)
            self._store.insert_policy_version(record)
            self._store.set_policy_version_status(record["version_id"], "ACTIVE")
            seq = self._store.insert_policy_activation(
                {
                    "version_id": record["version_id"],
                    "action": "seed",
                    "previous_version_id": None,
                    "detail": dict(detail or {}),
                }
            )
            self._policy_event(
                "PolicySeeded",
                f"seed:{seq}",
                {
                    "version_id": record["version_id"],
                    "seq": seq,
                    "source": source,
                    "detail": dict(detail or {}),
                },
            )
            version = self._store.get_policy_version(record["version_id"])
            assert version is not None
            return version

    def record_policy_drift(
        self, *, config_hash: str, differences: Sequence[Mapping[str, Any]]
    ) -> None:
        """The deployment configuration names whitelisted values that differ from the
        ACTIVE version: recorded, and the ACTIVE version still governs (plan D9-3')."""

        with original_source_mutation(self._store, writer="PolicyCommitsMixin.record_policy_drift"):
            active = self._store.active_policy()
            seq = len(self._store.list_policy_activations())
            self._policy_event(
                "PolicyConfigDrift",
                f"{seq}:{config_hash}:{self._store.now}",
                {
                    "active_version_id": None if active is None else active["version_id"],
                    "config_hash": config_hash,
                    "differences": [dict(d) for d in differences],
                    "note": "生效的仍是建库时写入的 ACTIVE 版本",
                },
            )

    def bind_policy(
        self,
        mission_id: str,
        *,
        provider_kind: str,
        default_params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bind a new Mission to the ACTIVE version in its creation transaction (plan
        D9-3')."""

        with original_source_mutation(self._store, writer="PolicyCommitsMixin.bind_policy"):
            existing = self._store.get_mission_policy(mission_id)
            if existing is not None:
                return existing
            active = self._store.active_policy()
            if active is None:
                if default_params is None:
                    from ..governance.promotion import resolve_params
                    from ..runtime.assembly import OrchestratorConfig

                    default_params = resolve_params(
                        OrchestratorConfig(evidence_root=self._store.path.parent)
                    )
                active = self.seed_policy(
                    default_params, detail={"config": "default configuration (no Orchestrator)"}
                )
            binding = {
                "mission_id": mission_id,
                "version_id": str(active["version_id"]),
                "source": "active",
                "provider_kind": provider_kind,
            }
            self._store.bind_mission_policy(binding)
            return binding

    # ------------------------------------------------------------ drift
    def record_interpreter_drift(
        self, mission_id: str, *, version_id: str, differences: Sequence[Mapping[str, Any]]
    ) -> None:
        """A resumed Mission's policy is read by other code than the code it was bound
        under (plan D9-4'): said once, on the Mission's own timeline — never silent."""

        rows = [dict(d) for d in differences]
        with original_source_mutation(
            self._store, writer="PolicyCommitsMixin.record_interpreter_drift"
        ):
            self._emit(
                "PolicyInterpreterDrift",
                mission_id,
                key=f"{version_id}:{sha256_hex(rows)[:16]}",
                payload={
                    "version_id": version_id,
                    "differences": rows,
                    "note": (
                        "绑定版本记录的解释器版本与当前代码不同；"
                        "代码无法运行旧解释器，按当前代码继续并如实记录"
                    ),
                },
            )

    def record_policy_route_unavailable(
        self, mission_id: str, *, version_id: str, dropped: Mapping[str, str]
    ) -> None:
        """A bound routing override names a profile this deployment does not have: that
        item falls back to the deployment's routing, on record (plan D9-4')."""

        with original_source_mutation(
            self._store, writer="PolicyCommitsMixin.record_policy_route_unavailable"
        ):
            self._emit(
                "PolicyRouteUnavailable",
                mission_id,
                key=f"{version_id}:{mission_id}",
                payload={"version_id": version_id, "dropped": dict(dropped)},
            )

    # ------------------------------------------------------------ Agents cannot set policy
    def record_policy_suggestion_refused(
        self,
        mission_id: str,
        *,
        source: str,
        keys: Sequence[str],
        path: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        """Plan D9-10' (S9-06): an online Agent proposed changing policy or a core rule.
        Nothing changes; the refusal is on the Mission's timeline."""

        named = sorted({str(k) for k in keys})
        digest = sha256_hex({"keys": named, "detail": dict(detail or {})})[:16]
        with original_source_mutation(
            self._store, writer="PolicyCommitsMixin.record_policy_suggestion_refused"
        ):
            self._emit(
                "PolicySuggestionRefused",
                mission_id,
                key=f"{source}:{path or ''}:{digest}",
                payload={
                    "source": source,
                    "path": path,
                    "keys": named,
                    "core_keys": [k for k in named if k in NON_PROMOTABLE],
                    "detail": dict(detail or {}),
                    "note": "在线 Agent 不能修改策略或核心安全 / 调度规则",
                },
            )

    def refuse_policy_files(
        self, stored: Any, *, mission_id: str, task_id: str, result_id: str
    ) -> None:
        """An accepted artifact under ``policy/`` or named like a deployment configuration
        is the Agent trying to set policy: refused on record, nothing reads it."""

        for artifact_id in stored.artifacts:
            artifact = self._store.get_artifact(str(artifact_id))
            if artifact is None:
                continue
            path = str(artifact.path)
            if not (path.startswith(POLICY_PATH_PREFIX) or Path(path).name in CONFIG_FILE_NAMES):
                continue
            keys: list[str] = []
            try:
                from ..artifacts.store import read_verified  # P3.2 D3: one way to read

                parsed = json.loads(read_verified(artifact).decode("utf-8"))
                if isinstance(parsed, Mapping):
                    keys = sorted(str(k) for k in parsed)
            except (OSError, ValueError):
                keys = []
            self.record_policy_suggestion_refused(
                mission_id,
                source="worker",
                keys=keys,
                path=path,
                detail={"task_id": task_id, "result_id": result_id, "artifact_id": artifact.id},
            )


__all__ = ("PolicyCommitsMixin",)
