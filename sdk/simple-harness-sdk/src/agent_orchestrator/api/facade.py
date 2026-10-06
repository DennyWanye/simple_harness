# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P3.1 external control facade (the user's Phase3 plan §3.3–§3.4; host support S2).

``MissionControlV1`` is the one surface a product talks to — the Host desktop App calls it
in process.  Nothing here is a second state machine: every write goes through the
Orchestrator door (:meth:`Orchestrator.create_mission`), the Commit Service or the
Approval API, and every read is a projection of the orchestration library.

* **Strict fields** (P3.1-A06): a request names only the open fields; an unknown field is
  refused, a field this surface does not open (tool set, risk level, task kind or
  runtime budgets) is refused by name — nothing is silently dropped.
* **Persistent receipts** (P3.1-A03): the same idempotency key with the same body returns
  the first receipt; a different body under that key is a ``conflict``.
* **Ownership** (P3.1-A04): every object is checked against the caller's tenant; a
  foreign object and a missing one read the same (``not_found``, no id in the message).
* **One read** (P3.1-A05): a snapshot and its ``through_seq`` come from the same read
  transaction; event pages are bounded and gap-free after that cursor.
* **Content-addressed artifacts**: read by immutable id only, re-hashed before they are
  returned, bounded in size; a local path is never accepted.

The caller's :class:`Principal` is fixed when the facade is made (never from a model, an
envelope or a request field).
"""

from __future__ import annotations

import hashlib
from functools import wraps
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..contracts import ContractError
from ..contracts.error_table import RecoveryBoundaryCode
from ..governance.budgets import BudgetError
from ..governance.permissions import Principal
from ..observability.secrets import find_secrets
from ..orchestrator.action_commits import ActionCommitError
from ..orchestrator.commit_service import MissionConflict
from ..orchestrator.source_commits import SourceCommitError
from ..storage.store import StoreError
from .approvals import ApprovalApi, ApprovalRequestError
from .missions import MissionRequestError

FACADE_VERSION = "mission-control-v1"
OPEN_FIELDS = frozenset(
    {
        "goal",
        "success_criteria",
        "idempotency_key",
        "budget",
        "stop_conditions",
        "untrusted_sources",
        "runtime_profile_id",
        "orchestration_semantics_version",
        "planning_protocol_version",
        "workspace_seed",
        "domain",  # P3.3 (D1): which domain profile this Mission freezes
    }
)
CLOSED_FIELDS = {
    "allowed_tools": "the tool set is the deployment's",
    "risk_level": "risk levels are set by the deployment",
    "task_kind": "not open on this surface",
}
OPEN_BUDGET = frozenset({"max_tokens", "max_attempts"})
LIST_FIELDS = ("success_criteria", "stop_conditions", "untrusted_sources")
CLOSED_BUDGET = frozenset(
    {"max_runtime_seconds", "max_concurrency", "max_tool_calls"}
)
MAX_EVENT_PAGE = 200
MAX_ARTIFACT_BYTES = 256 * 1024
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
DECISIONS = ("approve", "reject", "review_pass", "review_fail", "arbitrate")
TAKEOVER_ACTIONS = ("stop", "retry_with_note")
NOT_FOUND = "no such object for this caller"


#: Who issued a planning grant (2026-09-25): a person, or the Host under auto mode.
PLANNING_APPROVAL_SOURCES = frozenset({"HUMAN", "HOST_AUTO_PERMISSION"})

class FacadeError(ValueError):
    """A refused request; ``code`` is stable for products to map."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _native_root(method):
    @wraps(method)
    def checked(self, *args, **kwargs):
        self._require_native_root()
        return method(self, *args, **kwargs)
    return checked


class MissionControlV1:
    def __init__(self, orchestrator: Any, *, tenant_id: str, principal: Principal) -> None:
        if not str(tenant_id).strip():
            raise ValueError("a tenant is required")
        self._orchestrator = orchestrator
        self._tenant = str(tenant_id)
        self._principal = principal
        self._approvals = ApprovalApi(
            orchestrator.commit, principal, deployment=orchestrator.config.deployment_policy
        )

    @property
    def _store(self) -> Any:
        return self._orchestrator.store

    # ------------------------------------------------------------ ownership
    def _require_native_root(self) -> None:
        from ..assurance.codec import AssuranceError

        gate = self._orchestrator.commit._assurance_root_gate
        if gate is not None:
            try:
                gate.require_execution()
            except AssuranceError as error:
                raise FacadeError(error.code, "root requires current authorization") from error

    def assurance_root_diagnostic(self) -> dict[str, Any]:
        """非披露诊断（原计划 §10.3）：只有状态、能否执行、是否要当前认证、匿名阻塞码。"""
        gate = self._orchestrator.commit._assurance_root_gate
        if gate is None:
            return {"state": "NOT_INSTALLED", "execution_allowed": False,
                    "current_authentication_required": True}
        report = gate.diagnostic()
        # 第 2 批 A02：启动时安装 / 核对根失败 → 编排只开管理模式（隔离）；诊断如实说隔离，带阻塞码。
        code = getattr(self._orchestrator, "_assurance_quarantine_code", None)
        if code is not None:
            report = {**report, "state": "QUARANTINED", "execution_allowed": False,
                      "current_authentication_required": True, "blocking_code": str(code)}
        return report

    @_native_root
    def approve_assurance_check_policy(self, command: Mapping[str, Any]) -> dict[str, Any]:
        from ..assurance.checks import CriterionPolicy
        from ..assurance.codec import AssuranceError, array, fields
        from ..assurance.refs import AssuranceRef

        try:
            body = fields(dict(command), {"mission_id", "command_id", "requirements_ref",
                "candidate_mapping"},
                {"completion_scope", "planning_subject", "result_ref", "purpose",
                 "approval_source", "effect_key"})
            self._refuse_isolated(body["mission_id"])
            purpose = body.get("purpose", "CONTENT")
            # NEXT-TG-1.0 2B: the two operation reviews are approved on the effect
            # owner's Scope like the others (an assured publish was refused
            # CHECK_POLICY_UNRESOLVED at submission — nobody could approve them).
            # 2026-10-01 (HTN 精简 片 A): METHOD_PLAN is approved on the planning subject
            # Task instead of a Scope — a method is proposed before any Scope exists.
            if purpose not in ("CONTENT", "METHOD_PLAN", "MISSION_FINAL", "ACTION_PROPOSAL",
                               "OPERATION_OUTCOME"):
                raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID", str(purpose))
            if (purpose == "METHOD_PLAN") != (body.get("planning_subject") is not None) or (
                    body.get("completion_scope") is None) != (purpose == "METHOD_PLAN"):
                raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID", str(purpose))
            # 2026-09-25: the Host says when it is the one approving (recorded as system)
            approval_source = body.get("approval_source", "HUMAN")
            if approval_source not in ("HUMAN", "HOST_LOSSLESS_AUTO"):
                raise AssuranceError("CHECK_POLICY_APPROVAL_INVALID", str(approval_source))
            self._mission(body["mission_id"])
            ref = self._orchestrator.commit.approve_assurance_check_policy(
                tenant_id=self._tenant, principal=self._principal,
                mission_id=body["mission_id"], command_id=body["command_id"],
                requirements_ref=AssuranceRef.from_json(body["requirements_ref"], kinds={"requirements"}),
                completion_scope=None if body.get("completion_scope") is None else
                    AssuranceRef.from_json(body["completion_scope"], kinds={"completion_scope"}),
                planning_subject=None if body.get("planning_subject") is None else
                    AssuranceRef.from_json(body["planning_subject"], kinds={"task"}),
                candidate_mapping=tuple(CriterionPolicy.from_json(row)
                    for row in array(body["candidate_mapping"], minimum=1)),
                result_ref=None if body.get("result_ref") is None else
                    AssuranceRef.from_json(body["result_ref"], kinds={"result"}),
                purpose=purpose,
                effect_key=None if body.get("effect_key") is None else str(body["effect_key"]),
                approval_source=approval_source,
            )
            return {"check_policy_ref": ref.to_json()}
        except AssuranceError as error:
            raise FacadeError(error.code, "check policy approval refused") from error



    # ------------------------------------------------------ assurance reads
    def _assurance_read(self, verb: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """S25 read verbs: caller fixed at construction; the body never names it."""
        from .assurance import AssuranceReadError

        try:
            api = self._orchestrator.assurance_read_api(tenant_id=self._tenant, principal=self._principal)
            return getattr(api, verb)(dict(body))
        except AssuranceReadError as error:
            request_id = body.get("request_id") if isinstance(body, Mapping) else None
            wire = error.to_json(request_id if isinstance(request_id, str) and request_id else "unknown")
            raised = FacadeError(error.code, str(error))
            raised.wire = wire  # type: ignore[attr-defined]
            raise raised from error

    def assurance_snapshot(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._assurance_read("snapshot", body)

    def assurance_review(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._assurance_read("review", body)

    def assurance_use_check(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._assurance_read("use_check", body)

    def _mission(self, mission_id: object) -> Any:
        self._require_native_root()
        mission = self._store.get_mission(str(mission_id))
        if mission is None or mission.tenant_id != self._tenant:
            raise FacadeError("not_found", NOT_FOUND)
        return mission

    def _refuse_isolated(self, mission_id: object) -> None:
        """重启核对没通过、已隔离的任务只接受取消（N3-27）。

        隔离判断只有一个：:meth:`Orchestrator.recovery_isolated`，主循环、保证通道、执行图通知与
        部署职责读的都是它。推进这个任务的写入（改要求、确认完成要求、规划授权、回答提问、审批、
        接管、裁定、换资料、评论、提交操作、检查策略）在这里一律具名拒绝；取消不走这里。
        别人的任务照样读作 ``not_found``，不因隔离泄露它在不在。"""
        mid = str(mission_id)
        if not self._orchestrator.recovery_isolated(mid):
            return
        self._mission(mid)
        raise FacadeError(str(RecoveryBoundaryCode.MISSION_RECOVERY_ISOLATED),
                          "this Mission failed its restart check and is isolated; it can only be cancelled")

    def _owner_of(self, target_id: object) -> Any:
        target = str(target_id)
        store = self._store
        if store.get_mission(target) is not None:
            return self._mission(target)
        approval = store.get_approval(target)
        if approval is not None:
            return self._mission(approval.get("mission_id"))
        task = store.get_task(target)
        if task is not None:
            return self._mission(task.mission_id)
        raise FacadeError("not_found", NOT_FOUND)

    @staticmethod
    def _clean(*texts: str) -> None:
        for text in texts:
            if text and find_secrets(text):
                raise FacadeError("secret_rejected", "the text looks like it contains a secret")

    # ------------------------------------------------------------ commands
    @_native_root
    def planning_authorization(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Explicit Host commands over the existing authenticated grant issuer.

        The caller supplies no principal, tenant, decision allowlist or policy;
        those remain fixed by this facade and the original issuer API.
        """
        from .planning_authorization import PlanningAuthorizationApi
        operation = command.get("operation")
        fields = {
            "issue": {"mission_id", "request_id", "command_id"},
            "bind": {"request_id", "grant_id"},
            "renew": {"grant_id", "expected_revision", "command_id"},
            "revoke": {"grant_id", "expected_revision", "command_id", "reason"},
        }
        # 2026-09-25: ``approval_source`` says who issues -- a person (default) or the
        # Host acting under the principal's auto permission mode.  Recorded, never
        # part of the command hash, so a replay keeps its identity.
        optional = {"approval_source"} if operation == "issue" else set()
        if (not isinstance(operation, str) or operation not in fields
                or not fields[operation] | {"operation"} <= set(command) <= fields[operation] | {"operation"} | optional):
            raise FacadeError("invalid_request", "unknown planning authorization command or fields")
        approval_source = command.get("approval_source", "HUMAN")
        if approval_source not in PLANNING_APPROVAL_SOURCES:
            raise FacadeError("invalid_request", "approval_source must be HUMAN or HOST_AUTO_PERMISSION")
        body = {key: command[key] for key in fields[operation]}
        for key, value in body.items():
            if key == "expected_revision":
                if type(value) is not int or value < 1:
                    raise FacadeError("invalid_request", "expected_revision must be a positive integer")
            elif not isinstance(value, str) or not value.strip():
                raise FacadeError("invalid_request", "planning authorization identifiers must be nonempty strings")
        self._clean(*(v for v in body.values() if isinstance(v, str)))
        self._refuse_isolated_grant(operation, body)
        api = PlanningAuthorizationApi(self._orchestrator.commit, tenant_id=self._tenant,
            principal=self._principal, deployment=self._orchestrator.config.deployment_policy)
        try:
            if operation == "bind":
                return api.bind_request(**body)
            if operation == "issue":
                body["approval_source"] = approval_source
            receipt = getattr(api, operation)(**body)
            return receipt.to_json()
        except (ContractError, ValueError, StoreError) as error:
            raise FacadeError("refused", str(error)) from error

    def _refuse_isolated_grant(self, operation: str, body: Mapping[str, Any]) -> None:
        """签发、绑定、续期都推进规划；撤销是收回，照常（与取消同向）。"""
        from ..storage.planning_admission_store import PlanningAdmissionStore
        from ..storage.planning_decision_store import PlanningDecisionStore

        mission_id: object = None
        if operation == "issue":
            mission_id = body["mission_id"]
        elif operation == "bind":
            request = PlanningDecisionStore(self._store).get_planning_request(str(body["request_id"]))
            mission_id = None if request is None else request.mission_id
        elif operation == "renew":
            grant = PlanningAdmissionStore(self._store).get_grant(str(body["grant_id"]))
            mission_id = None if grant is None else grant["mission_id"]
        if mission_id is not None:
            self._refuse_isolated(mission_id)

    def pending_planning_authorizations(self) -> list[dict[str, Any]]:
        """Every planning request of this caller's Missions still awaiting authority.

        Read-only; the same rows the Mission snapshot shows as
        ``planning_authorization_requests``.  Lets the Host issue for them under its
        auto permission mode without reading storage itself.
        """
        from ..orchestrator.planning_selection import awaits_authority
        from ..storage.planning_decision_store import PlanningDecisionStore
        store = self._store
        rows: list[dict[str, Any]] = []
        with store.read_view():
            planning = PlanningDecisionStore(store)
            owned: dict[str, bool] = {}
            for intent in store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED"):
                mid = intent.mission_id
                if mid not in owned:
                    try:
                        self._mission(mid)
                        owned[mid] = True
                    except FacadeError:
                        owned[mid] = False
                if not owned[mid] or not awaits_authority(store, intent):
                    continue
                request = planning.get_planning_request_for_intent(intent.intent_id)
                if request is not None:
                    rows.append({"mission_id": mid, "request_id": request.request_id,
                                 "intent_id": intent.intent_id, "state": "AUTHORIZATION_REQUIRED"})
        return rows

    @_native_root
    def answer_planning_question(self, command: Mapping[str, Any]) -> dict[str, Any]:
        from .planning_answers import answer_planning_question

        required = {"decision_id", "answer", "expected_version", "nonce"}
        if (not isinstance(command, Mapping) or not required <= set(command)
                or set(command) - required - {"attach_as_source"}):
            raise FacadeError("invalid_request", "answer requires decision_id, answer, expected_version "
                              "and nonce; attach_as_source is optional")
        if not all(isinstance(command[k], str) for k in ("decision_id", "answer", "nonce")):
            raise FacadeError("invalid_request", "answer fields must be strings")
        if type(command["expected_version"]) is not int:
            raise FacadeError("invalid_request", "expected_version must be an integer")
        attach = command.get("attach_as_source", False)
        if type(attach) is not bool:
            raise FacadeError("invalid_request", "attach_as_source must be a boolean")
        self._clean(command["answer"])
        from ..storage.planning_human_store import PlanningHumanStore

        question = PlanningHumanStore(self._store).get(command["decision_id"])
        if question is not None:
            self._refuse_isolated(question["mission_id"])
        try:
            return answer_planning_question(
                self._orchestrator, tenant_id=self._tenant, principal=self._principal,
                decision_id=command["decision_id"], answer=command["answer"],
                expected_version=command["expected_version"], nonce=command["nonce"],
                attach_as_source=attach)
        except SourceCommitError as error:
            raise FacadeError(error.code, str(error)) from error
        except (ValueError, StoreError, ContractError) as error:
            raise FacadeError("invalid_request", str(error)) from error

    @_native_root
    def submit_operation_intent(self, command: Mapping[str, Any]) -> dict[str, Any]:
        from .operation_intents import OperationIntentApi

        if isinstance(command, Mapping) and isinstance(command.get("mission_id"), str):
            self._refuse_isolated(command["mission_id"])
        try:
            return OperationIntentApi(
                self._orchestrator, tenant_id=self._tenant, principal=self._principal,
            ).submit(command)
        except (ContractError, ValueError, StoreError) as error:
            raise FacadeError(getattr(error, "code", "invalid_request"), str(error)) from error

    @_native_root
    def operation_intent_status(self, intent_id: str) -> dict[str, Any]:
        from .operation_intents import OperationIntentApi

        try:
            return OperationIntentApi(
                self._orchestrator, tenant_id=self._tenant, principal=self._principal,
            ).status(intent_id)
        except (ContractError, ValueError, StoreError) as error:
            raise FacadeError(getattr(error, "code", "invalid_request"), str(error)) from error

    def _require_effects_supported(self, command: Mapping[str, Any]) -> None:
        """Refuse, by name, an effect this deployment's profile cannot reach (阶段 B 裁决第 4 类).

        The page lists only supported choices; this is the gate for a caller that went
        around it, or a deployment that changed — never left to fail at materialisation."""
        from ..contracts.operation_completion import OperationCompletionRequirementsV1
        from ..runtime.operation_profiles import BuiltinOperationProfiles
        from ..orchestrator.operation_materialization_inputs import OperationMaterializationInputError

        proposal = command.get("proposal") if isinstance(command, Mapping) else None
        if not isinstance(proposal, Mapping) or not proposal.get("effects"):
            return
        effects = OperationCompletionRequirementsV1.from_json(proposal).effects
        try:
            registry = getattr(self._orchestrator, "_operation_profiles", None) or BuiltinOperationProfiles(
                self._orchestrator.connectors)
            for effect in effects:
                registry.require_effect_supported(effect)
        except OperationMaterializationInputError as error:
            raise FacadeError(error.code, str(error)) from error

    @_native_root
    def approve_operation_completion_spec(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Confirm the exact completion mapping using this facade's fixed caller."""
        from .operation_completion import OperationCompletionApi
        from ..orchestrator.operation_completion import OperationCompletionError

        if isinstance(command, Mapping) and isinstance(command.get("mission_id"), str):
            self._refuse_isolated(command["mission_id"])
        try:
            self._require_effects_supported(command)
            receipt = OperationCompletionApi(
                self._orchestrator.commit, tenant_id=self._tenant, principal=self._principal,
            ).approve(command)
        except OperationCompletionError as error:
            raise FacadeError(error.code, str(error)) from error
        except ContractError as error:
            raise FacadeError("invalid_request", str(error)) from error
        except StoreError as error:
            raise FacadeError("conflict", str(error)) from error
        return receipt.to_json()

    @_native_root
    def amend_requirements(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Amend a running Mission's requirements (add / rewrite / remove entries) using this
        facade's fixed caller.  One transaction; the same command id replays its receipt."""
        from ..orchestrator.requirements_amendment import RequirementsAmendmentError, amend_requirements

        fields = {"mission_id", "command_id", "expected_requirements_ref", "changes", "reason", "source"}
        if not isinstance(command, Mapping) or set(command) != fields:
            raise FacadeError("invalid_request", "amend_requirements takes exactly " + ", ".join(sorted(fields)))
        if (not all(isinstance(command[key], str) and command[key] for key in ("mission_id", "command_id"))
                or not isinstance(command["reason"], str)
                or not isinstance(command["expected_requirements_ref"], Mapping)
                or not isinstance(command["source"], Mapping)
                or not isinstance(command["changes"], (list, tuple))
                or not all(isinstance(item, Mapping) for item in command["changes"])):
            raise FacadeError("invalid_request", "amend_requirements fields have the wrong shape")
        self._clean(command["reason"], *(str(item.get("statement") or "") for item in command["changes"]))
        self._refuse_isolated(command["mission_id"])
        try:
            return amend_requirements(
                self._orchestrator, mission_id=command["mission_id"], tenant_id=self._tenant,
                command_id=command["command_id"],
                expected_requirements_ref=command["expected_requirements_ref"],
                changes=command["changes"], reason=command["reason"], source=command["source"],
                principal=self._principal)
        except RequirementsAmendmentError as error:
            raise FacadeError(error.code, str(error)) from error
        except ContractError as error:
            raise FacadeError("invalid_request", str(error)) from error
        except StoreError as error:
            raise FacadeError("conflict", str(error)) from error

    def list_method_library(self) -> dict[str, Any]:
        """Every entry of the method library (precedents promoted from delivered Missions):
        id, one line of purpose, state, where it came from and which Missions blamed it."""
        from ..orchestrator.method_library import library_listing

        return {"entries": library_listing(self._store)}

    def retire_library_entry(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Retire one library entry on the person's word, using this facade's fixed caller.
        One transaction; the same command id replays its receipt."""
        from ..orchestrator.method_library import LibraryCommandError, retire_by_command

        fields = {"entry_id", "command_id", "reason"}
        if (not isinstance(command, Mapping) or set(command) != fields
                or not all(isinstance(command[key], str) and command[key] for key in ("entry_id", "command_id"))
                or not isinstance(command["reason"], str)):
            raise FacadeError("invalid_request", "retire_library_entry takes exactly entry_id, command_id, reason")
        self._clean(command["reason"])
        try:
            return retire_by_command(
                self._store, command_id=command["command_id"], entry_id=command["entry_id"],
                reason=command["reason"], principal=str(self._principal.principal_id))
        except LibraryCommandError as error:
            raise FacadeError(error.code, str(error)) from error
        except StoreError as error:
            raise FacadeError("conflict", str(error)) from error

    @_native_root
    def create(self, command: Mapping[str, Any]) -> dict[str, Any]:
        request = self._strict(command)
        self._clean(*self._texts(request))
        try:
            mission, created = self._orchestrator.create_mission(
                tenant_id=self._tenant, request=request
            )
        except MissionConflict as error:
            raise FacadeError(
                "conflict", "this idempotency_key already names a different request"
            ) from error
        except (MissionRequestError, ContractError, BudgetError, ValueError) as error:
            raise FacadeError("invalid_request", str(error)) from error
        except StoreError as error:
            raise FacadeError("refused", str(error)) from error
        found = self._store.find_mission(self._tenant, mission.idempotency_key)
        return {
            "mission_id": mission.id,
            "created": created,
            "spec_hash": "" if found is None else found[1],
            "status": str(mission.status),
            "facade": FACADE_VERSION,
        }

    @_native_root
    def create_with_sources(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Create the Mission and all initial source registrations before Host wake."""
        if not isinstance(command, Mapping) or set(command) != {"mission", "sources"}:
            raise FacadeError("invalid_request", "source batch requires exactly mission/sources")
        request = self._strict(command["mission"])
        self._clean(*self._texts(request))
        try:
            receipt = self._orchestrator.create_mission_with_sources(
                tenant_id=self._tenant,
                request=request,
                sources=command["sources"],
                principal=self._principal,
            )
        except SourceCommitError as error:
            raise FacadeError(error.code, str(error)) from error
        except MissionConflict as error:
            raise FacadeError("conflict", "source batch key has a different body") from error
        except (MissionRequestError, ContractError, BudgetError, ValueError) as error:
            raise FacadeError("invalid_request", str(error)) from error
        except StoreError as error:
            raise FacadeError("refused", str(error)) from error
        return {**dict(receipt), "facade": FACADE_VERSION}

    def register_source(self, command: Mapping[str, Any]) -> dict[str, Any]:
        return self._source_command("register", command)

    def supersede_source(self, command: Mapping[str, Any]) -> dict[str, Any]:
        return self._source_command("supersede", command)

    def revoke_source(self, command: Mapping[str, Any]) -> dict[str, Any]:
        return self._source_command("revoke", command)

    def _source_command(self, operation: str, command: Mapping[str, Any]) -> dict[str, Any]:
        required = {"mission_id", "path", "idempotency_key"}
        if operation != "revoke":
            required.update({"content", "kind"})
        else:
            required.add("reason")
        if operation != "register":
            required.add("expected_version_hash")
        if not isinstance(command, Mapping) or set(command) != required:
            raise FacadeError(
                "invalid_request", f"source command requires exactly {sorted(required)}"
            )
        if any(not isinstance(command[name], str) for name in required):
            raise FacadeError("invalid_request", "source command fields must be strings")
        self._mission(command["mission_id"])
        self._refuse_isolated(command["mission_id"])
        # A reopened Mission uses today's physical publication roots. Only the
        # Orchestrator knows these; the low-level CommitService does not guess them.
        try:
            self._orchestrator.validate_source_storage(command["mission_id"])
            method = getattr(self._orchestrator.commit, f"{operation}_source")
            extra = (
                {}
                if operation == "register"
                else {"deployment": self._orchestrator.config.deployment_policy}
            )
            return dict(
                method(**dict(command), tenant_id=self._tenant, principal=self._principal, **extra)
            )
        except SourceCommitError as error:
            raise FacadeError(error.code, str(error)) from error
        except ContractError as error:
            raise FacadeError("invalid_request", str(error)) from error
        except StoreError as error:
            raise FacadeError("refused", str(error)) from error

    @staticmethod
    def _strict(command: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(command, Mapping):
            raise FacadeError("invalid_request", "a command must be an object")
        unknown = sorted(set(command) - OPEN_FIELDS - set(CLOSED_FIELDS))
        if unknown:
            raise FacadeError("invalid_request", f"unknown fields: {unknown}")
        closed = sorted(set(command) & set(CLOSED_FIELDS))
        if closed:
            reasons = "; ".join(f"{name}: {CLOSED_FIELDS[name]}" for name in closed)
            raise FacadeError(
                "invalid_request", f"fields not open to this surface: {closed} ({reasons})"
            )
        budget = command.get("budget", {})
        if not isinstance(budget, Mapping):
            raise FacadeError("invalid_request", "budget must be an object")
        unknown_budget = sorted(set(budget) - OPEN_BUDGET - CLOSED_BUDGET)
        if unknown_budget:
            raise FacadeError(
                "invalid_request",
                f"unknown budget fields: {['budget.' + k for k in unknown_budget]}",
            )
        closed_budget = sorted(set(budget) & CLOSED_BUDGET)
        if closed_budget:
            raise FacadeError(
                "invalid_request",
                f"budget fields not open to this surface: {['budget.' + k for k in closed_budget]}",
            )
        for name in LIST_FIELDS:  # review round 2 P2-1: a string is not a list of strings
            value = command.get(name)
            if value is not None and (
                isinstance(value, str)
                or not isinstance(value, (list, tuple))
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                raise FacadeError("invalid_request", f"{name} must be a list of non-blank strings")
        seed = command.get("workspace_seed")
        if seed is not None and (
            not isinstance(seed, Mapping)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in seed.items())
        ):
            raise FacadeError("invalid_request", "workspace_seed must map paths to text")
        return dict(command)

    @staticmethod
    def _texts(request: Mapping[str, Any]) -> list[str]:
        """Every caller-written text that can reach a model (review round 2 P2-7)."""

        texts = [
            str(request.get("goal", "")),
            *(str(c) for c in request.get("success_criteria", ()) or ()),
        ]
        for path, body in dict(request.get("workspace_seed") or {}).items():
            texts += [str(path), str(body)]
        return texts

    def cancel(self, mission_id: str) -> dict[str, Any]:
        mission = self._mission(mission_id)
        if str(mission.status) in TERMINAL:  # idempotent: an ended Mission is left as it is
            return {"mission_id": mission.id, "status": str(mission.status), "changed": False}
        try:
            updated = self._orchestrator.commit.cancel_mission(mission.id)
        except (StoreError, ContractError, ValueError) as error:
            # review round 2 P2-3: it may have ended between the check and the commit
            current = self._mission(mission.id)
            if str(current.status) in TERMINAL:
                return {"mission_id": mission.id, "status": str(current.status), "changed": False}
            raise FacadeError("refused", str(error)) from error
        return {"mission_id": mission.id, "status": str(updated.status), "changed": True}

    def decide(
        self,
        request_id: str,
        decision: str,
        *,
        reason: str = "",
        note: str = "",
        ruling: str = "",
        basis: str = "",
        nonce: str | None = None,
    ) -> dict[str, Any]:
        # the shape of the request first, then who owns the object (review round 2 P2-4)
        if decision not in DECISIONS:
            raise FacadeError("invalid_request", f"decision must be one of {list(DECISIONS)}")
        if decision == "reject" and not reason.strip():
            raise FacadeError("invalid_request", "a rejection needs a reason")
        if decision == "arbitrate" and (not ruling or not basis.strip()):
            raise FacadeError("invalid_request", "an arbitration needs a ruling and a basis")
        self._clean(reason, note, basis)
        request = self._store.get_approval(str(request_id))
        if request is None:
            raise FacadeError("not_found", NOT_FOUND)
        self._mission(request.get("mission_id"))
        self._refuse_isolated(request.get("mission_id"))
        try:
            if request["kind"] == "source_change" and decision == "approve":
                self._orchestrator.validate_source_storage(str(request["mission_id"]))
            if decision == "approve":
                result = self._approvals.approve(request_id, nonce=nonce)
            elif decision == "reject":
                result = self._approvals.reject(request_id, reason=reason, nonce=nonce)
            elif decision in ("review_pass", "review_fail"):
                result = self._approvals.review(
                    request_id,
                    verdict="pass" if decision == "review_pass" else "fail",
                    note=note,
                    nonce=nonce,
                )
            else:
                result = self._approvals.arbitrate(
                    request_id, ruling=ruling, basis=basis, nonce=nonce
                )
        except ApprovalRequestError as error:
            raise FacadeError("invalid_request", str(error)) from error
        except (ActionCommitError, StoreError, ContractError) as error:
            raise FacadeError("refused", str(error)) from error
        decided = dict(result.get("request") or self._store.get_approval(str(request_id)) or {})
        return {
            "request_id": str(request_id),
            "request_state": decided.get("state"),
            "receipt_hash": result.get("receipt_hash"),
        }

    def takeover(self, task_id: str, action: str, *, basis: str, note: str = "") -> dict[str, Any]:
        if action not in TAKEOVER_ACTIONS:
            raise FacadeError("invalid_request", f"action must be one of {list(TAKEOVER_ACTIONS)}")
        if not basis.strip():
            raise FacadeError("invalid_request", "a takeover needs a basis")
        self._clean(basis, note)
        task = self._store.get_task(str(task_id))
        if task is None:
            raise FacadeError("not_found", NOT_FOUND)
        self._mission(task.mission_id)
        self._refuse_isolated(task.mission_id)
        try:
            return dict(
                self._approvals.takeover(str(task_id), action=action, basis=basis, note=note)
            )
        except ApprovalRequestError as error:
            raise FacadeError("invalid_request", str(error)) from error
        except (ActionCommitError, StoreError, ContractError) as error:
            raise FacadeError("refused", str(error)) from error

    def resolve_unknown(self, action_key: str, *, outcome: str, basis: str) -> dict[str, Any]:
        """A person rules on an action nothing else can settle (阶段 B 裁决第 3 类): an
        UNKNOWN outcome, or a failed hand-off with no proof — "applied" / "not applied".
        The ruling is the person's click; a model never reaches this."""
        if outcome not in {"succeeded", "failed"}:
            raise FacadeError("invalid_request", "outcome must be succeeded or failed")
        if not str(basis).strip():
            raise FacadeError("invalid_request", "a ruling needs a basis")
        self._clean(basis)
        action = self._store.get_action(str(action_key))
        if action is None:
            raise FacadeError("not_found", NOT_FOUND)
        self._mission(str(action["mission_id"]))
        self._refuse_isolated(action["mission_id"])
        from ..orchestrator.operation_runtime import ensure_operation_runtime

        try:
            ensure_operation_runtime(self._orchestrator)
            return dict(self._approvals.resolve_unknown(
                str(action_key), outcome=outcome, basis=str(basis),
                evidence={"source": "user_ruling", "action_key": str(action_key)}))
        except ApprovalRequestError as error:
            raise FacadeError("invalid_request", str(error)) from error
        except (ActionCommitError, StoreError, ContractError) as error:
            raise FacadeError("refused", str(error)) from error

    def comment(self, target_id: str, text: str) -> dict[str, Any]:
        if not str(text).strip():
            raise FacadeError("invalid_request", "a comment needs text")
        self._clean(text)
        self._refuse_isolated(self._owner_of(target_id).id)
        try:
            return dict(self._approvals.comment(str(target_id), text))
        except ApprovalRequestError as error:
            raise FacadeError("invalid_request", str(error)) from error
        except (ActionCommitError, StoreError, ContractError) as error:
            raise FacadeError("refused", str(error)) from error

    # ------------------------------------------------------------ reads
    @_native_root
    def missions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        store = self._store
        with store.read_view():
            mine = [m for m in store.list_missions() if m.tenant_id == self._tenant]
            mine.sort(key=lambda m: m.created_at, reverse=True)
            return [
                {
                    "mission_id": m.id,
                    "goal": m.goal[:120],
                    "status": str(m.status),
                    "stop_reason": m.stop_reason,
                    "created_at": m.created_at,
                    "pending_approvals": len(store.list_approvals(m.id, "PENDING")),
                }
                for m in mine[: max(1, min(int(limit), 200))]
            ]

    def _unrefined_goals(self, mission_id: str) -> list[dict[str, Any]]:
        """Goals in the current plan that no method has refined yet — the planning frontier,
        by the same function the execution-graph snapshot uses (阶段 E)."""
        from ..planning.htn.refinement import planning_frontier

        dispatch = self._orchestrator._dispatch_for(mission_id)
        if dispatch is None:
            return []
        try:
            network = dispatch.network(mission_id)
            frontier = planning_frontier(network)
        except (ContractError, StoreError):
            return []
        rows = []
        for item in frontier:
            binding = network.binding_for_occurrence(item.occurrence_id)
            goal = dict(binding.typed_parameters).get("goal")
            rows.append({"occurrence_id": str(item.occurrence_id), "task_id": str(binding.task_id),
                         "label": str(goal).strip()[:60] if isinstance(goal, str) and goal.strip()
                         else str(binding.goal_signature.signature_id)})
        return rows

    def _steps_no_longer_counting(self, mission_id: str) -> list[dict[str, Any]]:
        """Steps that passed their acceptance but no longer count under the current plan and
        requirements (e.g. accepted under an earlier version of the requirements) — the same
        reading the Planner package's ``counts_under_current`` gives."""
        from ..orchestrator.planner_views import accepted_steps

        dispatch = self._orchestrator._dispatch_for(mission_id)
        if dispatch is None:
            return []
        try:
            network = dispatch.network(mission_id)
            steps = accepted_steps(self._store, mission_id, network, dispatch.semantics())
        except (ContractError, StoreError):
            return []
        rows = []
        for acceptance, occurrence, counts in steps:
            if counts:
                continue
            task = self._store.get_task(str(occurrence.task_id))
            goal = str(getattr(task, "goal", "") or "").strip()
            rows.append({"occurrence_id": str(occurrence.occurrence_id), "task_id": str(occurrence.task_id),
                         "acceptance_id": str(acceptance.acceptance_id),
                         "requirements_revision": int(acceptance.requirements_revision),
                         "label": goal[:60] or str(occurrence.task_id)})
        return rows

    def snapshot(self, mission_id: str) -> dict[str, Any]:
        store = self._store
        with store.read_view():  # the snapshot and its cursor come from one read
            mission = self._mission(mission_id)
            snapshot = store.snapshot(mission.id)
            from ..storage.planning_human_store import PlanningHumanStore
            snapshot["planning_questions"] = PlanningHumanStore(store).list(mission.id)
            # 这件事（含下级、含换过的做法）到现在花了多少：读时由尝试与结算推出
            from ..orchestrator.obligation_accounts import obligation_accounts, obligation_rows
            snapshot["obligation_accounts"] = obligation_accounts(store, mission.id)
            snapshot["budget_by_duty"] = obligation_rows(store, mission.id)
            snapshot["unrefined_goals"] = self._unrefined_goals(mission.id)
            snapshot["steps_no_longer_counting"] = self._steps_no_longer_counting(mission.id)
            from ..orchestrator.planning_selection import awaits_authority
            from ..storage.planning_decision_store import PlanningDecisionStore
            planning = PlanningDecisionStore(store)
            snapshot["planning_authorization_requests"] = [
                {"mission_id": mission.id, "request_id": request.request_id,
                 "intent_id": intent.intent_id, "state": "AUTHORIZATION_REQUIRED"}
                for intent in store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED")
                if intent.mission_id == mission.id and awaits_authority(store, intent)
                and (request := planning.get_planning_request_for_intent(intent.intent_id)) is not None]
            from .operation_workspace import operation_workspace
            snapshot["operation_workspace"] = operation_workspace(self._orchestrator, mission, principal=self._principal)
            from ..verification.conflicts import mission_disputes
            snapshot["disputes"] = mission_disputes(store, mission.id)  # read from the claims
            through = store.last_event_seq(mission.id)
        return {
            "mission_id": mission.id,
            "through_seq": through,
            "state_version": mission.version,
            "snapshot": snapshot,
            "facade": FACADE_VERSION,
        }

    def events(self, mission_id: str, *, after_seq: int = 0, limit: int = 100) -> dict[str, Any]:
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise FacadeError("invalid_request", "after_seq must be a non-negative integer")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_EVENT_PAGE
        ):
            raise FacadeError("invalid_request", f"limit must be between 1 and {MAX_EVENT_PAGE}")
        store = self._store
        with store.read_view():
            mission = self._mission(mission_id)
            rows = store.list_events(mission.id, after_seq=after_seq, limit=limit + 1)
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "mission_id": mission.id,
            "events": [{**event.to_json(), "seq": event.seq} for event in rows],
            "through_seq": rows[-1].seq if rows else after_seq,
            "has_more": has_more,
        }

    @_native_root
    def approvals(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        if mission_id is not None:
            self._mission(mission_id)
            return self._approvals.list(mission_id)
        mine = {m.id for m in self._store.list_missions() if m.tenant_id == self._tenant}
        return [item for item in self._approvals.list(None) if item.get("mission_id") in mine]

    def _authorize_artifact_read(self, artifact: Any):
        from ..assurance.codec import AssuranceError
        from ..assurance.refs import AssuranceRef, Pin

        commit = self._orchestrator.commit
        gate = commit._assurance_root_gate
        if gate is None:
            return None
        try:
            try:
                gate.require_execution()
                return None  # Native-root ownership remains the original tenant check.
            except AssuranceError:
                pass
            authority = commit._assurance_read_authority
            if authority is None:
                raise AssuranceError("CURRENT_READ_AUTHORITY_UNAVAILABLE")
            ref = AssuranceRef("artifact", Pin(artifact.id, artifact.version, artifact.content_hash))
            current = authority(self._principal, self._tenant, artifact.mission_id, ref, "DISCLOSE")
            from ..orchestrator.assurance_clock import observe_assurance_clock

            now_ms = int(self._store.now * 1000)
            if observe_assurance_clock(commit, now_ms=now_ms).state != "STABLE":
                raise AssuranceError("TIME_DISCONTINUITY")
            return gate.require_read(principal=self._principal, tenant_id=self._tenant,
                mission_id=artifact.mission_id, ref=ref, purpose="DISCLOSE", current=current,
                now_ms=now_ms)
        except AssuranceError as error:
            raise FacadeError(error.code, "artifact needs current read authorization") from error

    def artifact_read(self, artifact_id: str) -> dict[str, Any]:
        identifier = str(artifact_id)
        artifact = None
        if "/" not in identifier and "\\" not in identifier:  # an id, never a path
            artifact = self._store.get_artifact(identifier)
        if artifact is None:
            raise FacadeError("not_found", NOT_FOUND)
        mission = self._store.get_mission(artifact.mission_id)
        if mission is None or mission.tenant_id != self._tenant:
            raise FacadeError("not_found", NOT_FOUND)
        access = self._authorize_artifact_read(artifact)
        # review round 2 P2-2: one streaming read hashes everything and keeps the head, so
        # the returned content is exactly the bytes whose hash was checked
        digest = hashlib.sha256()
        head = bytearray()
        size = 0
        from ..artifacts.store import ArtifactStoreError, open_nofollow

        try:
            if not artifact.storage_uri:  # P3.2 D3: bytes lost before the library upgrade
                raise ArtifactStoreError("unavailable", artifact.path)
            with open_nofollow(Path(artifact.storage_uri)) as handle:  # P3.2 D3: no symlink
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    if len(head) <= MAX_ARTIFACT_BYTES:
                        head.extend(chunk[: MAX_ARTIFACT_BYTES + 1 - len(head)])
        except (OSError, ArtifactStoreError) as error:
            raise FacadeError("integrity_error", "the artifact's content is missing") from error
        if digest.hexdigest() != artifact.content_hash:
            raise FacadeError(
                "integrity_error", "the artifact's content no longer matches its recorded hash"
            )
        current_artifact = self._store.get_artifact(identifier)
        if current_artifact != artifact or self._authorize_artifact_read(artifact) != access:
            raise FacadeError("RECHECK_REQUIRED", "artifact authorization changed while reading")
        truncated = size > MAX_ARTIFACT_BYTES
        body = bytes(head[:MAX_ARTIFACT_BYTES])
        content: str | None
        try:
            content, encoding = body.decode("utf-8"), "utf-8"
        except UnicodeDecodeError as error:
            if truncated and error.start >= len(body) - 3:  # a character cut by the limit
                content, encoding = body[: error.start].decode("utf-8"), "utf-8"
            else:
                content, encoding = None, "binary"  # never a silently replaced text
        return {
            "artifact_id": artifact.id,
            "mission_id": artifact.mission_id,
            "path": artifact.path,
            "content_hash": artifact.content_hash,
            "size_bytes": size,
            "encoding": encoding,
            "content": content,
            "truncated": truncated,
        }


__all__ = ("FACADE_VERSION", "FacadeError", "MissionControlV1")
