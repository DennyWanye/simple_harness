"""预备调用授权策略：按冻结的运行工具权限判定每次调用是否放行（从 tool_authority 挪出）。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from simple_harness import thaw_json
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationResult,
    PreparedToolEffect,
)

from deskpet.permissions.runtime import PreparedAuthorizationPlan, PreparedAuthorizationRuntime
from deskpet.product_state.authorization_saga import AuthorizationSagaIdentity
from deskpet.sdk_adapters.tool_authority import (
    SdkRunToolAuthorityRegistry,
    SdkRunToolAuthorityV1,
    _canonical_sha256,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.types.task_grants import ResourceSelector, TaskGrant
from deskpet.workflows.effects import PreparedToolCall

_ROUTE_ZH = {
    "direct_standalone": "直接回答，不进入任务",
    "continue_active": "继续当前任务",
    "resume_existing": "恢复之前的任务",
}


def _clip(value: object, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_call_zh(tool_name: str, arguments: Mapping[str, Any]) -> str:
    """One plain-Chinese sentence saying what the person is being asked to allow.

    2026-09-25 UI 全量点击：弹窗以前只写 "Allow context_route for this exact
    request?"，用户看不出批准的是什么。只取模型提交的参数做展示，不做授权判断。
    """

    args = arguments if isinstance(arguments, Mapping) else {}
    if tool_name == "context_route":
        route = str(args.get("route") or "")
        if route == "create_new":
            return f"新建任务「{_clip(args.get('title'), 60)}」，并在工作区里为它新建一个任务目录"
        return _ROUTE_ZH.get(route, "切换对话要处理的任务")
    if tool_name == "tool_search":
        return f"在可用工具列表里查找：{_clip(args.get('query'), 60)}"
    if tool_name == "tool_describe":
        return "查看一个工具的用法说明"
    if tool_name == "tool_activate":
        return "启用一个可用工具，供这次对话使用"
    if tool_name == "task_scope_search":
        return f"搜索以前的任务：{_clip(args.get('query'), 60)}"
    if tool_name == "write_file":
        return f"写入文件：{_clip(args.get('path'))}"
    if tool_name == "edit_file":
        return f"修改文件：{_clip(args.get('path'))}"
    if tool_name == "read_file":
        return f"读取文件：{_clip(args.get('path'))}"
    if tool_name == "move_file":
        return f"移动文件：{_clip(args.get('source'))} → {_clip(args.get('destination'))}"
    if tool_name == "run_shell":
        return f"运行命令：{_clip(args.get('command'))}"
    if tool_name == "web_fetch":
        return f"打开网页：{_clip(args.get('url'))}"
    if tool_name == "web_search":
        return f"联网搜索：{_clip(args.get('query'), 60)}"
    return f"使用工具 {tool_name}"


@dataclass(frozen=True, slots=True)
class _PreparedAuthorizationFacts:
    authority: SdkRunToolAuthorityV1
    call: PreparedToolCall
    plan: PreparedAuthorizationPlan
    grant: TaskGrant


@dataclass(frozen=True, slots=True)
class SkillInstallPreflightReady:
    intent_id: str
    artifact_ref: str
    content_digest: str
    member_digest: str
    member_summary: tuple[Mapping[str, Any], ...]
    permission_summary: tuple[str, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class SkillInstallPreflightRejected:
    failure_receipt_ref: str
    code: str
    public_message: str
    retryable: bool
    correlation_id: str
    attempt_generation: int = 1
    allowed_actions: tuple[str, ...] = ("change_source", "cancel")

    def sdk_public_message(self) -> str:
        return json.dumps(
            {
                "schema": "skill-install-preflight-rejection-v1",
                "code": self.code,
                "public_message": self.public_message,
                "retryable": self.retryable,
                "failure_receipt_ref": self.failure_receipt_ref,
                "attempt_generation": self.attempt_generation,
                "allowed_actions": list(self.allowed_actions),
                "correlation_id": self.correlation_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


SkillInstallPreflightOutcome = SkillInstallPreflightReady | SkillInstallPreflightRejected


class ProjectSkillInstallPreflightPort(Protocol):
    async def stage_authorization_preflight(
        self,
        *,
        prepared: PreparedToolEffect,
        context: ToolExecutionContext,
        args_hash: str,
        principal_id: str,
    ) -> SkillInstallPreflightOutcome: ...


class SdkPreparedAuthorizationPolicy:
    """Map SDK effects onto the product prepared authorization runtime."""

    def __init__(
        self,
        runtime: PreparedAuthorizationRuntime,
        authorities: SdkRunToolAuthorityRegistry,
        *,
        clock: Callable[[], float] = time.time,
        initial_policy_generation: int | None = None,
        skill_install_preflight: ProjectSkillInstallPreflightPort | None = None,
    ) -> None:
        self._runtime = runtime
        self._authorities = authorities
        self._clock = clock
        self._facts: dict[tuple[str, str], _PreparedAuthorizationFacts] = {}
        self._auto_skill_approvals: dict[tuple[str, str], Mapping[str, Any]] = {}
        self._skill_install_preflight = skill_install_preflight
        if initial_policy_generation is not None and initial_policy_generation < 0:
            raise ValueError("initial_policy_generation must be non-negative")
        self._policy_generation = initial_policy_generation
        authorities.add_terminal_listener(self._release_run)

    def _release_run(self, authority: SdkRunToolAuthorityV1) -> None:
        self._facts = {
            identity: facts
            for identity, facts in self._facts.items()
            if facts.authority.run_id != authority.run_id
        }
        self._auto_skill_approvals = {
            identity: approval
            for identity, approval in self._auto_skill_approvals.items()
            if identity[0] != authority.run_id
        }

    def auto_skill_approval_for(self, prepared: PreparedToolEffect) -> Mapping[str, Any] | None:
        return self._auto_skill_approvals.get((prepared.run_id.value, prepared.effect_id.value))

    def current_policy_generation(self) -> int:
        if self._policy_generation is None:
            raise RuntimeError("product authorization policy has not been evaluated")
        return self._policy_generation

    def update_policy_generation(self, generation: int) -> None:
        value = int(generation)
        if value < 0:
            raise ValueError("policy generation must be non-negative")
        if self._policy_generation is not None and value < self._policy_generation:
            raise RuntimeError("authorization policy generation regressed")
        self._policy_generation = value

    async def _stage_skill_preflight(
        self,
        *,
        prepared: PreparedToolEffect,
        context: ToolExecutionContext,
        call: PreparedToolCall,
        authority: SdkRunToolAuthorityV1,
    ) -> SkillInstallPreflightReady | AuthorizationResult | None:
        if call.tool_name != "skill_install":
            return None
        if self._skill_install_preflight is None:
            return AuthorizationResult(
                AuthorizationDecision.DENY,
                reason_code="skill_install_preflight_unavailable",
            )
        outcome = await self._skill_install_preflight.stage_authorization_preflight(
            prepared=prepared,
            context=context,
            args_hash=call.args_hash,
            principal_id=authority.principal_id,
        )
        if isinstance(outcome, SkillInstallPreflightRejected):
            return AuthorizationResult(
                AuthorizationDecision.DENY,
                reason_code=outcome.code,
                public_message=outcome.sdk_public_message(),
                receipt_ref=outcome.failure_receipt_ref,
            )
        if not isinstance(outcome, SkillInstallPreflightReady):
            raise TypeError("skill install preflight returned an invalid outcome")
        return outcome

    @staticmethod
    def _resource_selectors(
        prepared: PreparedToolEffect,
        *,
        args_hash: str,
    ) -> tuple[ResourceSelector, ...]:
        selectors: list[ResourceSelector] = []
        for resource in prepared.resources:
            namespace = str(resource.namespace).casefold()
            value = str(resource.resource_id)
            actions = tuple(str(item) for item in resource.actions)
            if namespace in {"filesystem", "file", "path"}:
                selectors.append(ResourceSelector.filesystem(value, *actions))
            elif namespace in {"network", "network_origin"}:
                selectors.append(ResourceSelector.network(value, *actions))
            elif namespace in {
                "process_executable",
                "package_source",
                "application",
                "desktop_target",
                "capability_managed_root",
                "system_change",
            }:
                selectors.append(ResourceSelector(namespace, value, actions))  # type: ignore[arg-type]
            else:
                raise ValueError(f"unsupported SDK Tool resource namespace: {namespace}")
        if selectors:
            return tuple(selectors)
        # A missing sidecar must not collapse to an unscoped allow. Bind the
        # exact argument hash into a symbolic resource so the resulting grant
        # cannot authorize another Tool or another argument payload.
        return (
            ResourceSelector(
                "system_change",
                f"sdk-tool:{prepared.call.name}:{args_hash}",
                ("invoke",),
            ),
        )

    def _prepared_call(
        self,
        prepared: PreparedToolEffect,
        authority: SdkRunToolAuthorityV1,
    ) -> PreparedToolCall:
        name = prepared.call.name
        spec = authority.specs.get(name)
        if spec is None or not self._authorities.is_tool_exposed(authority.run_id, name):
            raise RuntimeError("capability_denied")
        arguments = thaw_json(prepared.call.arguments)
        if not isinstance(arguments, Mapping):
            raise TypeError("SDK Tool arguments must be an object")
        provisional = PreparedToolCall.prepare(
            tool_name=name,
            stable_call_id=prepared.call.call_id.value,
            final_params=dict(arguments),
            tool_spec_version=spec.spec_version,
            schema_hash=spec.schema_hash,
            permission_policy_version=spec.permission_policy_version,
            effect_type=authority.dispatch_kinds[name],
        )
        return replace(
            provisional,
            resource_selectors=self._resource_selectors(prepared, args_hash=provisional.args_hash),
        )

    async def decide(
        self,
        prepared: PreparedToolEffect,
        *,
        request: AuthorizationRequest | None,
    ) -> AuthorizationResult:
        authority = self._authorities.resolve(prepared.run_id)
        authority.assert_workspace_current()
        call = self._prepared_call(prepared, authority)
        context = authority.execution_context(
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
        )
        # ``explicit_only``
        # comes from the frozen EffectClass of the Tool.  A confirm-only class
        # (external_send / destructive / payment / credential / privacy /
        # unknown, or manifest-dangerous) is never covered by an existing grant
        # and never auto-granted: Auto mode returns REQUIRE_USER exactly like
        # Manual mode does, and the candidate grant's source is ``user``.
        explicit_only = authority.specs[call.tool_name].confirm_only
        explicit_fences: dict[str, str] = (
            {
                "decision_nonce": _canonical_sha256(
                    {"effect_id": prepared.effect_id.value, "confirm_only": True}
                ),
                "confirm_only_snapshot_ref": authority.prepared_tool_set.scope_id,
                "confirm_only_snapshot_hash": authority.authority_fingerprint,
            }
            if explicit_only
            else {}
        )
        plan = await self._runtime.plan_prepared_call(
            call=call,
            context=context,
            permission_category=authority.permission_categories[call.tool_name],
            task_grant_id=None,
            principal_id=authority.principal_id,
            explicit_only=explicit_only,
            confirmed=False,
            **explicit_fences,
        )
        self._policy_generation = plan.policy_state.generation
        if plan.action != "allow" and explicit_only and plan.policy_state.mode == "auto":
            # 2026-09-07 product decision (user): the product has only two modes,
            # manual and auto, and auto is the default in which NO prompt is ever
            # shown -- confirm-only effect classes are auto-granted exactly like any
            # other Tool. The frozen EffectClass still travels in the audit facts;
            # only the REQUIRE_USER round trip is removed for auto mode.
            plan = await self._runtime.plan_prepared_call(
                call=call,
                context=context,
                permission_category=authority.permission_categories[call.tool_name],
                task_grant_id=None,
                principal_id=authority.principal_id,
                explicit_only=False,
                confirmed=False,
            )
            self._policy_generation = plan.policy_state.generation
            explicit_only = False
        if plan.action != "allow" and (plan.policy_state.mode == "manual" or explicit_only):
            # SDK owns the final decision nonce.  Compute the exact user grant
            # candidate now, but return REQUIRE_USER so it remains prepared
            # until the SDK's durable decision bind activates it.
            plan = await self._runtime.plan_prepared_call(
                call=call,
                context=context,
                permission_category=authority.permission_categories[call.tool_name],
                task_grant_id=None,
                principal_id=authority.principal_id,
                confirmed=True,
                explicit_only=explicit_only,
                # Manual grants are immutable.  Bind their instance identity
                # to the stable SDK effect so replay is idempotent while two
                # different Tool effects in one Run cannot collide merely
                # because they request the same resource category.
                decision_id=prepared.effect_id.value,
                **explicit_fences,
            )
            self._policy_generation = plan.policy_state.generation
            if plan.action != "allow" or plan.committed_task_grant is None:
                return AuthorizationResult(
                    AuthorizationDecision.DENY,
                    reason_code=f"product_policy:{plan.action}",
                )
            grant = plan.committed_task_grant
            self._facts[(authority.run_id, prepared.effect_id.value)] = _PreparedAuthorizationFacts(
                authority, call, plan, grant
            )
            preflight_outcome = await self._stage_skill_preflight(
                prepared=prepared, context=context, call=call, authority=authority
            )
            if isinstance(preflight_outcome, AuthorizationResult):
                return preflight_outcome
            preflight = preflight_outcome
            nonce = (
                request.nonce
                if request is not None
                else _canonical_sha256(
                    {
                        "effect_id": prepared.effect_id.value,
                        "grant_fingerprint": grant.fingerprint,
                    }
                )
            )
            return AuthorizationResult(
                AuthorizationDecision.REQUIRE_USER,
                reason_code="product_policy_user_confirmation",
                public_message=f"是否允许：{describe_call_zh(call.tool_name, call.final_params)}？",
                request=AuthorizationRequest(
                    f"是否允许：{describe_call_zh(call.tool_name, call.final_params)}？",
                    nonce,
                    expires_at=float(self._clock()) + 300.0,
                    metadata={
                        "tool_name": call.tool_name,
                        "scope_hash": authority.scope_hash,
                        "grant_source": grant.source,
                        "policy_generation": grant.policy_generation,
                        **(
                            {}
                            if preflight is None
                            else {
                                "skill_install_artifact_ref": preflight.artifact_ref,
                                "skill_install_intent_id": preflight.intent_id,
                                "skill_install_content_digest": preflight.content_digest,
                                "skill_install_member_digest": preflight.member_digest,
                                "skill_install_member_summary": list(preflight.member_summary),
                                "skill_install_permission_summary": list(
                                    preflight.permission_summary
                                ),
                                "skill_install_expires_at": preflight.expires_at,
                            }
                        ),
                    },
                ),
            )
        if plan.action != "allow" or plan.committed_task_grant is None:
            decision = (
                AuthorizationDecision.DENY
                if plan.action == "deny"
                else AuthorizationDecision.REQUIRE_USER
            )
            if decision is AuthorizationDecision.REQUIRE_USER:
                return AuthorizationResult(
                    decision,
                    reason_code=f"product_policy:{plan.action}",
                    request=AuthorizationRequest(
                        f"需要你确认：{describe_call_zh(call.tool_name, call.final_params)}",
                        _canonical_sha256(
                            {"effect_id": prepared.effect_id.value, "action": plan.action}
                        ),
                    ),
                )
            return AuthorizationResult(decision, reason_code=f"product_policy:{plan.action}")
        grant = plan.committed_task_grant
        self._facts[(authority.run_id, prepared.effect_id.value)] = _PreparedAuthorizationFacts(
            authority, call, plan, grant
        )
        return await self._allow_after_preflight(
            prepared, context=context, call=call, authority=authority, grant=grant
        )

    async def restore_frozen_authorization(
        self, prepared: PreparedToolEffect, *, grant: TaskGrant
    ) -> AuthorizationResult:
        """Replay after a restart: reuse the grant frozen for this very call, no policy IO.

        2026-09-30（架构方案 A）：重放会重新签发票据，票据编号确定不变、只有过期时间
        变了，指纹一变授权记录就按"同一调用、不同授权"判冲突。这里用授权记录里那张票据
        原样恢复冻结事实，身份哈希自然相同；过期与策略代数由适配器在调用前判定。
        """

        authority = self._authorities.resolve(prepared.run_id)
        authority.assert_workspace_current()
        call = self._prepared_call(prepared, authority)
        context = authority.execution_context(
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
        )
        state = await self._runtime.policy_state()
        plan = PreparedAuthorizationPlan(
            action="allow",
            reason="replay of the frozen authorization",
            policy_state=state,
            exact_request=self._runtime.build_exact_request(
                call=call,
                context=context,
                permission_category=authority.permission_categories[call.tool_name],
                now=float(self._clock()),
            ),
            current_task_grant=grant,
            proposed_task_grant=None,
            policy_decision=None,
        )
        self.restore_facts(prepared, authority=authority, call=call, plan=plan, grant=grant)
        return await self._allow_after_preflight(
            prepared, context=context, call=call, authority=authority, grant=grant
        )

    async def _allow_after_preflight(
        self,
        prepared: PreparedToolEffect,
        *,
        context: ToolExecutionContext,
        call: PreparedToolCall,
        authority: SdkRunToolAuthorityV1,
        grant: TaskGrant,
    ) -> AuthorizationResult:
        preflight_outcome = await self._stage_skill_preflight(
            prepared=prepared, context=context, call=call, authority=authority
        )
        if isinstance(preflight_outcome, AuthorizationResult):
            return preflight_outcome
        if isinstance(preflight_outcome, SkillInstallPreflightReady):
            preflight = preflight_outcome
            self._auto_skill_approvals[(authority.run_id, prepared.effect_id.value)] = {
                "skill_install_intent_id": preflight.intent_id,
                "skill_install_content_digest": preflight.content_digest,
                "skill_install_member_digest": preflight.member_digest,
                "skill_install_expires_at": preflight.expires_at,
            }
            return AuthorizationResult(
                AuthorizationDecision.ALLOW,
                receipt_ref=f"product-policy:auto:{grant.policy_generation}:{grant.fingerprint}",
            )
        return AuthorizationResult(
            AuthorizationDecision.ALLOW,
            receipt_ref=(
                f"product-policy:{grant.source}:{grant.policy_generation}:{grant.fingerprint}"
            ),
        )

    def user_confirmed(self, run_id: str, effect_id: str) -> bool:
        """True only when a person confirmed this exact effect (grant source ``user``).

        A handler runs only after the SDK activated the decision, so a ``user``
        grant here means the popup for this very call was answered "allow".
        """

        facts = self._facts.get((run_id, effect_id))
        return facts is not None and facts.grant.source == "user"

    def facts_for(self, prepared: PreparedToolEffect) -> _PreparedAuthorizationFacts:
        facts = self._facts.get((prepared.run_id.value, prepared.effect_id.value))
        if facts is None:
            raise RuntimeError("SDK prepared authorization facts are unavailable")
        if facts.authority.run_id != prepared.run_id.value:
            raise RuntimeError("SDK prepared authorization facts crossed Run scope")
        return facts

    def restore_facts(
        self,
        prepared: PreparedToolEffect,
        *,
        authority: SdkRunToolAuthorityV1,
        call: PreparedToolCall,
        plan: PreparedAuthorizationPlan,
        grant: TaskGrant,
    ) -> None:
        """Restore already-verified frozen authorization facts without policy IO."""

        if authority.run_id != prepared.run_id.value:
            raise RuntimeError("restored authority crossed Run scope")
        if call.stable_call_id != prepared.call.call_id.value:
            raise RuntimeError("restored call identity differs")
        if call.tool_name != prepared.call.name:
            raise RuntimeError("restored Tool name differs")
        arguments = thaw_json(prepared.call.arguments)
        if not isinstance(arguments, Mapping):
            raise TypeError("SDK Tool arguments must be an object")
        expected = self._prepared_call(prepared, authority)
        if expected != call:
            raise RuntimeError("restored prepared call differs from frozen authority")
        if plan.committed_task_grant != grant:
            raise RuntimeError("restored plan grant differs")
        if grant.principal_id != authority.principal_id:
            raise RuntimeError("restored grant principal differs")
        self._facts[(authority.run_id, prepared.effect_id.value)] = _PreparedAuthorizationFacts(
            authority, call, plan, grant
        )

    def grant_factory(
        self,
        prepared: PreparedToolEffect,
        _result: AuthorizationResult,
    ) -> TaskGrant:
        return self.facts_for(prepared).grant

    def identity_factory(
        self,
        prepared: PreparedToolEffect,
        request: AuthorizationRequest | None,
    ) -> AuthorizationSagaIdentity:
        facts = self.facts_for(prepared)
        authority = facts.authority
        grant = facts.grant
        identity_payload = {
            "run_id": authority.run_id,
            "effect_id": prepared.effect_id.value,
            "call_id": prepared.call.call_id.value,
            "args_hash": facts.call.args_hash,
            "capability_hash": authority.capability_hash,
            "schema_hash": facts.call.schema_hash,
            "scope_hash": authority.scope_hash,
            "grant_fingerprint": grant.fingerprint,
        }
        return AuthorizationSagaIdentity(
            authorization_id=f"sdk-auth:{_canonical_sha256(identity_payload)}",
            principal_id=authority.principal_id,
            session_id=authority.session_id,
            root_run_id=authority.root_run_id,
            run_id=authority.run_id,
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
            tool_name=prepared.call.name,
            arguments=dict(thaw_json(prepared.call.arguments)),
            capability_hash=authority.capability_hash,
            schema_hash=facts.call.schema_hash,
            scope_hash=authority.scope_hash,
            grant_id=grant.task_grant_id,
            grant_version=grant.version,
            grant_fingerprint=grant.fingerprint,
            policy_generation=grant.policy_generation,
            decision_nonce=request.nonce if request is not None else None,
            decision_version=0,
            run_lease_epoch=authority.task_work_context.binding_version,
            execution_lease_epoch=authority.catalog_generation,
        )


__all__ = [
    "ProjectSkillInstallPreflightPort",
    "SdkPreparedAuthorizationPolicy",
    "SkillInstallPreflightOutcome",
    "SkillInstallPreflightReady",
    "SkillInstallPreflightRejected",
]
