# SPDX-License-Identifier: Apache-2.0
"""原生执行池的部署拼装（ARP-EXEC-1.1.1 RP-E3；2026-10-03 从 Host 搬入，HTN 补齐阶段 A′）。

The SDK's native plane refuses to guess: a pool on it needs the deployment's real
authorization port, a certified token meter, an authenticated creation caller for every
dispatch intent, and the deployment's own ports (session root, Assurance acceptance
reader, artifact reader, embedding resource).  This module builds those from what the
deployment hands in and reports what it does not have (no embedding model → lexical
only; no approved script runner → SCRIPT skills refused by name).  Nothing here reads a
request body: tenant, principal and policy come from deployment composition only.

**身份字节**：下面所有 ``host-…`` / ``host:…`` 字串、配置修订号、尺寸档、落盘路径名都是已有
执行池的身份，原样沿用 Host 的值（Host 的字节快照测试钉住），改任何一个都会让已有执行池起不来。
本机资源（DeepSeek 用量计数器、向量模型、沙箱脚本执行器、模型权重路径）由部署传入。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from simple_harness.agents.arp.assurance_acceptance import AssuranceSkillAcceptance
from simple_harness.agents.arp.errors import ArpError
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.ports import ArpPorts, TrustedCaller, bootstrap_root, now_ms
from simple_harness.agents.arp.profile import ProfileRefs, default_policy
from simple_harness.agents.arp.profile import RuntimeProfile as ArpRuntimeProfile
from simple_harness.agents.arp.shared_catalogue import SharedSkillCatalogue, mirror_caller
from simple_harness.agents.arp.strict import digest
from simple_harness.agents.context.budget import ContextPolicy
from simple_harness.runtime.ports import AuthorizationResult

from ..runtime.assembly import OWNER_SCOPE, resolve_profile_context_policy
from ..runtime.mission_sources import MissionSourceReader
from ..runtime.model_router import RuntimeProfile
from ..runtime.native_plane import NativePlaneAssembly, intent_caller
from ..runtime.tool_gateway import ASSURANCE_REVIEWER_TOOLS

logger = logging.getLogger(__name__)

NATIVE_PROFILE_PREFIX = "deepseek-native-"
NATIVE_OUTPUT_TOKENS = 32_768
#: The context sizes this deployment offers, smallest first (the first is the catalogue owner).
CONTEXT_INPUT_LIMITS = (262_144, 524_288)


def native_profile_id(tokens: int, *, thinking: bool = False) -> str:
    """A native pool's id.  Thinking-mode pools are separate pools (own id, own execution
    library, own provider and counter): an Agent's thinking mode is frozen by the pool its
    Mission was frozen on, so changing the deployment setting never breaks existing Agents."""
    return f"{NATIVE_PROFILE_PREFIX}{tokens // 1024}k-{'thinking-' if thinking else ''}v1"


def is_native_profile(profile_id: str | None) -> bool:
    return isinstance(profile_id, str) and profile_id.startswith(NATIVE_PROFILE_PREFIX)


#: NEXT-TG-1.0 §11: the one pool whose catalogue is the deployment's Skill authority; every
#: other native pool mirrors it.  Fixed (the first native pool), so it never moves between
#: restarts.
def catalogue_owner_profile_id() -> str:
    return native_profile_id(CONTEXT_INPUT_LIMITS[0])


class DeploymentToolAuthorization:
    """The pool's real authorization port: the deployment policy decides by tool name.

    It is not ``AllowAll``: a tool outside ``DeploymentPolicy.allowed_tools`` is denied
    with the policy named, and the policy identity is part of the port.  The Assurance
    reviewers' read-only tools (two evidence tools and, since K01, the two blackboard
    readers) are part of the policy: every role of a Mission runs on the Mission's pool,
    and a reviewer that cannot read evidence can only answer INCONCLUSIVE (the tool
    gateway still confines them to the reviewer role).
    """

    def __init__(self, allowed_tools: Sequence[str]) -> None:
        self._allowed = frozenset(str(name) for name in allowed_tools) | frozenset(ASSURANCE_REVIEWER_TOOLS)
        self.policy_id = "host-deployment-policy:" + digest(sorted(self._allowed))[:16]

    async def request_authorization(self, request: Any) -> AuthorizationResult:
        name = getattr(getattr(request, "tool_call", None), "name", None)
        if name in self._allowed:
            return AuthorizationResult.allow()
        return AuthorizationResult.deny(f"tool {name!r} is outside {self.policy_id}")


def _scaled_policy(policy_id: str, *, tokens: int, output_tokens: int, embedding: bool) -> dict[str, Any]:
    policy = default_policy(policy_id)
    policy["max_context_tokens"] = tokens
    policy["output_reserve_tokens"] = output_tokens
    policy["embedding_required_for_activation"] = embedding
    if tokens < 524_288:
        scale = tokens / 524_288
        for name in ("recent_min_tokens", "recall_max_tokens", "fixed_soft_max_tokens"):
            policy[name] = max(4096, int(policy[name] * scale))
        policy["section_soft_caps"] = {k: max(2048, int(v * scale)) for k, v in policy["section_soft_caps"].items()}
    return policy


class NativePools:
    """Everything a deployment supplies to run pools on the native plane.

    ``embedding`` is the deployment's embedding port (or ``None`` with ``embedding_reason``);
    ``script_runner`` builds a SCRIPT-skill runner for a pool's run directory (``None`` when
    the deployment proved no sandbox) and ``script_runner_label`` names it in the status.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        allowed_tools: Sequence[str],
        meter_factory: Callable[..., Any],
        embedding: Any | None = None,
        embedding_reason: str | None = None,
        script_runner: Callable[[Path], Any] | None = None,
        script_runner_label: str | None = None,
        clock_ms: Callable[[], int] = now_ms,
    ) -> None:
        self.tenant_id = tenant_id
        self.principal_id = principal_id
        self.clock_ms = clock_ms
        self.script_runner = script_runner
        self.script_runner_label = script_runner_label
        self.authorization = DeploymentToolAuthorization(allowed_tools)
        self.owner_contract = Pin(
            "policy", f"host-owner:{tenant_id}", 1,
            digest({"tenant_id": tenant_id, "authorization": self.authorization.policy_id}),
        )
        # One acceptance reader per pool: the SDK binds a reader to the pool's own Skill
        # lifecycle (``bind_lifecycle``), so a shared reader would answer for the last pool.
        self.acceptances: dict[str, AssuranceSkillAcceptance] = {}
        self.embedding, self.embedding_reason = embedding, embedding_reason
        self.embedding_ref = Pin(
            "deployment", "bge-m3-int8-local", 1,
            digest({"fingerprint": None if self.embedding is None else self.embedding.fingerprint}),
        )
        self._meter_factory = meter_factory
        self._orchestrator: Any = None
        # The SDK's own Mission source reader over this Orchestrator's records (bound late:
        # pools are assembled before the Orchestrator exists).
        self.mission_sources = MissionSourceReader(lambda: self._orchestrator)
        # One Skill catalogue authority for all native pools (the owner pool's catalogue).
        self.catalogue_owner_id = catalogue_owner_profile_id()
        self.shared_catalogue = SharedSkillCatalogue(caller=mirror_caller(f"host:{tenant_id}"))
        self.profiles: dict[str, dict[str, Any]] = {}
        self._runtimes: dict[str, Any] = {}

    # ---- late bindings (the Orchestrator exists only after the profiles do) ----------------

    def bind_orchestrator(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator
        for acceptance in self.acceptances.values():
            acceptance.store = orchestrator.store
        self.sync_catalogue(reason="startup")

    def sync_catalogue(self, *, reason: str) -> dict[str, Any] | None:
        """Mirror the owner's Skills into every member pool (idempotent); a failure is
        logged and changes nothing — each use still asks the owner first."""
        if self.shared_catalogue.owner_id is None:
            return None
        try:
            report = self.shared_catalogue.sync()
        except Exception:  # noqa: BLE001 - never block the orchestrator on a mirror pass
            logger.warning("shared skill catalogue sync failed (%s)", reason, exc_info=True)
            return None
        failed = [(pool, row) for pool, body in report["members"].items() for row in body.get("skills", []) if not row.get("mirrored")]
        if failed:
            logger.info("shared skill catalogue (%s): %d skill(s) not usable in some pools: %s", reason, len(failed),
                        [(pool, row.get("skill_ref", {}).get("id"), row.get("error")) for pool, row in failed][:8])
        return report

    def _root_incarnation(self) -> Any:
        gate = getattr(self._orchestrator, "_assurance_root_gate", None)
        if gate is None:
            raise ArpError("SOURCE_UNAVAILABLE", "the Assurance root gate is not installed on this deployment")
        return gate.require_execution()

    def artifacts(self, ref: Pin) -> bytes:
        assembled = getattr(self._orchestrator, "assembled", None)
        if assembled is None:
            raise ArpError("SOURCE_UNAVAILABLE", "artifact store is not assembled")
        from ..artifacts.store import ArtifactStoreError

        try:
            return assembled.workspaces.artifact_store.read(ref.content_hash)
        except ArtifactStoreError as error:
            raise ArpError("SOURCE_UNAVAILABLE", f"artifact bytes unavailable: {error.reason}") from error

    # ---- callers -----------------------------------------------------------------------------

    def _principal_ref(self) -> Pin:
        return Pin("principal", self.principal_id, 0, digest({"principal_id": self.principal_id}))

    def intent_caller(self, intent: Any) -> TrustedCaller:
        return intent_caller(intent, principal_id=self.principal_id, owner_contract_ref=self.owner_contract)

    def control_caller(self, body: Mapping[str, Any]) -> TrustedCaller:
        """The caller of one control-channel request: deterministic in the request body so a
        replayed command hashes the same, and never derived from anything the body claims
        about its principal."""

        command_id = body.get("command_id") if isinstance(body.get("command_id"), str) else None
        receipt = {"principal_id": self.principal_id, "tenant_id": self.tenant_id, "body": dict(body)}
        key = command_id or f"read:{digest(receipt)[:16]}"
        return TrustedCaller(
            principal_ref=self._principal_ref(),
            owner_contract_ref=self.owner_contract,
            command_receipt_ref=Pin("receipt", f"host:control:{key}", 0, digest(receipt)),
        )

    # ---- per-pool assembly ----------------------------------------------------------------

    def assembly(self, profile_id: str, *, tokens: int, counter: Any, output_tokens: int = NATIVE_OUTPUT_TOKENS) -> NativePlaneAssembly:
        acceptance = AssuranceSkillAcceptance(store=None, clock_ms=self.clock_ms, root_incarnation=self._root_incarnation)
        if self._orchestrator is not None:
            acceptance.store = self._orchestrator.store
        self.acceptances[profile_id] = acceptance
        # Wire-only scope (2026-09-24): the DeepSeek endpoint keeps no output beyond the wire,
        # so no prior-output reserve reader is bound — one would double-charge every earlier
        # output of the run and shrink the window over a long session.
        meter = self._meter_factory(counter, input_limit_tokens=tokens, max_output_tokens=output_tokens)
        root_id = f"host:{self.tenant_id}:{profile_id}"
        owner = profile_id == self.catalogue_owner_id
        # The SDK derives the catalogue namespace from the session root and the pool's
        # owner scope; the deployment records the same value so control requests can name it.
        namespace = f"{root_id}/{OWNER_SCOPE}/-"
        policy = _scaled_policy(f"host-native-{profile_id}", tokens=tokens, output_tokens=output_tokens, embedding=False)
        activation = {"kind": "deployment_activation", "profile_id": profile_id, "revision": 1, "tenant_id": self.tenant_id}
        refs = ProfileRefs(
            retention_policy_ref=Pin("policy", "host-retention-default", 1, digest({"retention": "default"})),
            capability_registry_ref=Pin("catalogue", namespace, 1, digest({"namespace": namespace})),
            activation_receipt_ref=Pin("receipt", f"host:native-plane:{profile_id}:activate", 0, digest(activation)),
            default_skill_policy_ref=Pin("policy", "host-skill-default", 1, digest({"skills": "default"})),
            embedding_deployment_ref=self.embedding_ref,
            catalogue_namespace_id=namespace,
        )
        # MISSION (NEXT-TG-1.0 §10): every Agent of an orchestrator pool is created from
        # the exact role-typed sources the SDK's own Mission source reader derives from the
        # claimed dispatch intent, and each new request re-checks them.  Revision 2: the
        # revision-1 STANDALONE_CHAT profile row stays in existing libraries and its old
        # Sessions keep running under it; a profile row is never rewritten in place.
        profile = ArpRuntimeProfile(
            profile_id=profile_id, profile_revision=2, owner_mode="MISSION",
            allow_lexical_degradation=True, context_policy=policy, refs=refs,
        )
        self.profiles[profile_id] = {
            "profile_id": profile_id, "max_context_tokens": tokens, "output_tokens": output_tokens,
            "counter": getattr(counter, "fingerprint", None), "count_mode": meter.count_mode,
            "embedding": "bge-m3-int8" if self.embedding is not None else "lexical-only",
            "embedding_reason": self.embedding_reason,
            "script_runner": "unavailable" if self.script_runner is None else self.script_runner_label,
            "catalogue_namespace_id": namespace, "owner_mode": "MISSION",
            "catalogue_role": "owner" if owner else "member",
        }

        def after_build(runtime: Any) -> None:
            actual = runtime.arp.catalogue.namespace_id
            if actual != namespace:  # never report a namespace the runtime does not use
                self.profiles[profile_id]["catalogue_namespace_id"] = actual
            # 2026-09-25 主流程优化条目 6: keep the live runtime so status() can report
            # the health of its background loops (a rebuild re-registers it).
            self._runtimes[profile_id] = runtime
            if owner:
                self.shared_catalogue.bind_owner(profile_id, runtime)
            else:
                self.shared_catalogue.bind_member(profile_id, runtime)

        def arp_ports(execution_db: Path) -> ArpPorts:
            root = bootstrap_root(execution_db.with_name(execution_db.name + ".arp-root"), root_id=root_id)
            return ArpPorts(
                root_dir=root.directory, profile=profile, activation_receipt=activation, clock_ms=self.clock_ms,
                meter=meter, embedding=self.embedding,
                embedding_resource_ref=None if self.embedding is None else self.embedding_ref,
                acceptance=acceptance, artifacts=self.artifacts,
                mission_sources=self.mission_sources,
                catalogue_authority=None if owner else self.shared_catalogue,
                # SCRIPT skills run in the sandbox model-written code uses; without a
                # proven sandbox they are refused by name (RUNNER_UNAVAILABLE).
                script_runner=None if self.script_runner is None else self.script_runner(
                    execution_db.with_name(execution_db.name + ".skill-runs")
                ),
            )

        return NativePlaneAssembly(arp_ports=arp_ports, authorization=self.authorization, caller_for=self.intent_caller, after_build=after_build)

    def background_health(self, profile_id: str) -> list[dict[str, Any]]:
        """The pool's background-loop health rows (empty until the runtime is built)."""

        runtime = self._runtimes.get(profile_id)
        reader = getattr(runtime, "background_health", None)
        if reader is None:
            return []
        try:
            return [row.to_json() for row in reader()]
        except Exception:  # noqa: BLE001 - status must never fail because health did
            logger.warning("native pool %s: background health unreadable", profile_id, exc_info=True)
            return []

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "available": bool(self.profiles),
            "profiles": [{**v, "background": self.background_health(k)} for k, v in self.profiles.items()],
            "embedding": "bge-m3-int8" if self.embedding is not None else "lexical-only",
            "embedding_reason": self.embedding_reason,
            "authorization_policy": self.authorization.policy_id,
            "skill_catalogue_owner": self.shared_catalogue.owner_id,
        }


def calibrated(provider: Any, counter: Any) -> Any:
    """A relay pool's provider learns the relay margin from every reported prompt count."""

    from ..runtime.deepseek_meter import CalibratingProvider, RelayDeepSeekCounter

    if provider is None or not isinstance(counter, RelayDeepSeekCounter):
        return provider
    return CalibratingProvider(provider, counter)


def pool_options(
    config: Any,
    *,
    native: NativePools,
    provider: Any,
    counter: Any,
    provider_kind: str,
    thinking_provider: Any = None,
    thinking_counter: Any = None,
) -> dict[str, Any]:
    """The native pools of one deployment, as Orchestrator runtime options.

    One pool per context size; with a thinking provider and its counter, a separate
    thinking pool per size.  ``counter`` must be certified (a DeepSeek counter, or a
    trusted test counter); without one the deployment has no pool (``profiles`` empty).
    """

    options: dict[str, Any] = {"profiles": {}}
    if counter is None or native is None or provider is None:
        return options
    counters: dict[str, Any] = {}

    def register(identifier: str, tokens: int, pool_counter: Any, base: Any, *, kind: str,
                 default_output: int = 8192) -> None:
        wanted = ContextPolicy(
            max_input_tokens=tokens, output_reserve=32768,
            max_tool_result_tokens=16384, render_slack_tokens=0,
        )
        policy = resolve_profile_context_policy(
            config, profile_id=identifier, tokenizer=pool_counter, fresh_policy=wanted,
        )
        if policy != wanted:
            raise RuntimeError(f"上下文执行库配置不一致：{identifier}")
        counters[identifier] = pool_counter
        options["profiles"][identifier] = RuntimeProfile(
            identifier, calibrated(base, pool_counter), config.model,
            provider_kind=kind, context_policy=policy, tokenizer=pool_counter,
            default_max_output_tokens=default_output, max_output_tokens_ceiling=32768,
            native_plane=native.assembly(identifier, tokens=tokens, counter=pool_counter),
        )

    for tokens in CONTEXT_INPUT_LIMITS:
        register(native_profile_id(tokens), tokens, counter, provider, kind=provider_kind)
    # Thinking-mode pools (user decision 2026-09-24: both modes supported): a separate
    # provider (thinking enabled, reasoning replayed) and a counter bound to the same mode.
    if thinking_provider is not None and thinking_counter is not None:
        for tokens in CONTEXT_INPUT_LIMITS:
            # Reasoning shares the output limit: at 8192 a thinking reviewer spent the budget
            # thinking and its verdict JSON was cut mid-string (2026-09-25 desktop run).
            register(native_profile_id(tokens, thinking=True), tokens, thinking_counter,
                     thinking_provider, kind="env", default_output=32768)
    options["provider_token_estimators"] = dict(counters)
    return options


__all__ = (
    "CONTEXT_INPUT_LIMITS",
    "NATIVE_OUTPUT_TOKENS",
    "DeploymentToolAuthorization",
    "NativePools",
    "calibrated",
    "catalogue_owner_profile_id",
    "is_native_profile",
    "native_profile_id",
    "pool_options",
)
