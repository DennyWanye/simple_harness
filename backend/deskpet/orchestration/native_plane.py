# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host composition of the native runtime plane (ARP-EXEC-1.1.1, RP-E3).

The SDK's native plane refuses to guess: a pool on it needs the deployment's real
authorization port, a certified token meter, an authenticated creation caller for
every dispatch intent, and the Host's own ports (session root, Assurance acceptance
reader, artifact reader, embedding resource).  This module builds those from what the
Host really has and reports what it does not have (no BGE-M3 weights → lexical only;
no approved script executor → SCRIPT skills refused by name).  Nothing here reads a
request body: tenant, principal and policy come from deployment composition only.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_orchestrator.runtime.assembly import OWNER_SCOPE
from agent_orchestrator.runtime.native_plane import NativePlaneAssembly, RunPriorReserve, intent_caller
from agent_orchestrator.runtime.tool_gateway import ASSURANCE_EVIDENCE_TOOLS
from simple_harness.agents.arp.assurance_acceptance import AssuranceSkillAcceptance
from simple_harness.agents.arp.errors import ArpError
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.ports import ArpPorts, TrustedCaller, bootstrap_root, now_ms
from simple_harness.agents.arp.profile import ProfileRefs
from simple_harness.agents.arp.profile import RuntimeProfile as ArpRuntimeProfile
from simple_harness.agents.arp.profile import default_policy
from simple_harness.agents.arp.strict import digest
from simple_harness.runtime.ports import AuthorizationResult

logger = logging.getLogger(__name__)

NATIVE_PROFILE_PREFIX = "deepseek-native-"
BGE_M3_SUBDIR = "bge-m3-int8"
NATIVE_OUTPUT_TOKENS = 32_768


def native_profile_id(tokens: int) -> str:
    return f"{NATIVE_PROFILE_PREFIX}{tokens // 1024}k-v1"


def is_native_profile(profile_id: str | None) -> bool:
    return isinstance(profile_id, str) and profile_id.startswith(NATIVE_PROFILE_PREFIX)


class DeploymentToolAuthorization:
    """The pool's real authorization port: the deployment policy decides by tool name.

    It is not ``AllowAll``: a tool outside ``DeploymentPolicy.allowed_tools`` is denied
    with the policy named, and the policy identity is part of the port.  The Assurance
    reviewers' two read-only evidence tools are part of the policy: every role of a
    Mission runs on the Mission's pool, and a reviewer that cannot read evidence can only
    answer INCONCLUSIVE (the tool gateway still confines them to the reviewer role).
    """

    def __init__(self, allowed_tools: Sequence[str]) -> None:
        self._allowed = frozenset(str(name) for name in allowed_tools) | frozenset(ASSURANCE_EVIDENCE_TOOLS)
        self.policy_id = "host-deployment-policy:" + digest(sorted(self._allowed))[:16]

    async def request_authorization(self, request: Any) -> AuthorizationResult:
        name = getattr(getattr(request, "tool_call", None), "name", None)
        if name in self._allowed:
            return AuthorizationResult.allow()
        return AuthorizationResult.deny(f"tool {name!r} is outside {self.policy_id}")


class BgeM3EmbeddingPort:
    """BGE-M3 (INT8, local) as the native plane's embedding resource.

    The fingerprint is computed from the weight directory at assembly (configuration
    bytes plus every file name and size); the model itself loads on the first call.  A
    failing load is recorded by the SDK as a FAILED embedding receipt and the session
    degrades to lexical retrieval by the profile's rule; nothing is guessed.
    """

    pricing_mode = "NO_PROVIDER_CHARGE"

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir)
        body = hashlib.sha256()
        for path in sorted(self.model_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.model_dir).as_posix()
            body.update(relative.encode("utf-8") + b"\0")
            if path.name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
                body.update(path.read_bytes())
            body.update(str(path.stat().st_size).encode("ascii") + b"\0")
        self.fingerprint = "bge-m3-int8:" + body.hexdigest()
        self._model: Any = None
        self._load_error: Exception | None = None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._load_error is not None:
            raise self._load_error  # a failed load stays failed for this lifetime: no retry storm
        if self._model is None:
            try:
                from FlagEmbedding import BGEM3FlagModel  # heavy: torch; loaded on first use only

                self._model = BGEM3FlagModel(str(self.model_dir), use_fp16=False)
            except Exception as error:  # noqa: BLE001 - recorded by the SDK as a FAILED receipt
                self._load_error = error
                logger.warning("BGE-M3 load failed; native sessions degrade to lexical retrieval: %s", type(error).__name__)
                raise
        dense = self._model.encode(list(texts), batch_size=8, max_length=1024)["dense_vecs"]
        return [[float(x) for x in vector] for vector in dense]


def embedding_port(models_dir: Path | None) -> tuple[BgeM3EmbeddingPort | None, str | None]:
    """The BGE-M3 port when the weights are installed, else ``(None, reason)``."""

    if models_dir is None:
        return None, "模型目录未配置"
    model_dir = Path(models_dir) / BGE_M3_SUBDIR
    if not (model_dir / "config.json").is_file():
        return None, f"BGE-M3 权重不在 {model_dir}"
    try:
        import importlib.util

        if importlib.util.find_spec("FlagEmbedding") is None:
            return None, "FlagEmbedding 未安装"
    except Exception:  # noqa: BLE001 - a broken import system is a missing resource
        return None, "FlagEmbedding 不可用"
    return BgeM3EmbeddingPort(model_dir), None


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


class HostNativePlane:
    """Everything the Host supplies to run pools on the native plane."""

    def __init__(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        allowed_tools: Sequence[str],
        models_dir: Path | None,
        meter_factory: Callable[..., Any],
        clock_ms: Callable[[], int] = now_ms,
    ) -> None:
        self.tenant_id = tenant_id
        self.principal_id = principal_id
        self.clock_ms = clock_ms
        self.authorization = DeploymentToolAuthorization(allowed_tools)
        self.owner_contract = Pin(
            "policy", f"host-owner:{tenant_id}", 1,
            digest({"tenant_id": tenant_id, "authorization": self.authorization.policy_id}),
        )
        # One acceptance reader per pool: the SDK binds a reader to the pool's own Skill
        # lifecycle (``bind_lifecycle``), so a shared reader would answer for the last pool.
        self.acceptances: dict[str, AssuranceSkillAcceptance] = {}
        self.embedding, self.embedding_reason = embedding_port(models_dir)
        self.embedding_ref = Pin(
            "deployment", "bge-m3-int8-local", 1,
            digest({"fingerprint": None if self.embedding is None else self.embedding.fingerprint}),
        )
        self._meter_factory = meter_factory
        self._orchestrator: Any = None
        self.readers: dict[str, RunPriorReserve] = {}
        self.profiles: dict[str, dict[str, Any]] = {}

    # ---- late bindings (the Orchestrator exists only after the profiles do) ----------------

    def bind_orchestrator(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator
        for acceptance in self.acceptances.values():
            acceptance.store = orchestrator.store

    def _root_incarnation(self) -> Any:
        gate = getattr(self._orchestrator, "_assurance_root_gate", None)
        if gate is None:
            raise ArpError("SOURCE_UNAVAILABLE", "the Assurance root gate is not installed on this deployment")
        return gate.require_execution()

    def artifacts(self, ref: Pin) -> bytes:
        assembled = getattr(self._orchestrator, "assembled", None)
        if assembled is None:
            raise ArpError("SOURCE_UNAVAILABLE", "artifact store is not assembled")
        from agent_orchestrator.artifacts.store import ArtifactStoreError

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
        reader = RunPriorReserve()
        self.readers[profile_id] = reader
        acceptance = AssuranceSkillAcceptance(store=None, clock_ms=self.clock_ms, root_incarnation=self._root_incarnation)
        if self._orchestrator is not None:
            acceptance.store = self._orchestrator.store
        self.acceptances[profile_id] = acceptance
        meter = self._meter_factory(counter, input_limit_tokens=tokens, max_output_tokens=output_tokens, prior_reserve=reader)
        root_id = f"host:{self.tenant_id}:{profile_id}"
        # The SDK derives the catalogue namespace from the session root and the pool's
        # owner scope; the Host records the same value so control requests can name it.
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
        # STANDALONE_CHAT: the orchestrator creates every Agent from a frozen dispatch
        # intent (config + input hash) and needs no Mission-mode source set; MISSION owner
        # mode requires the exact TaskGraph / InputManifest / Assurance sources, which the
        # SDK refuses by name until they exist (creation.py §2) — a recorded follow-up.
        profile = ArpRuntimeProfile(
            profile_id=profile_id, profile_revision=1, owner_mode="STANDALONE_CHAT",
            allow_lexical_degradation=True, context_policy=policy, refs=refs,
        )
        self.profiles[profile_id] = {
            "profile_id": profile_id, "max_context_tokens": tokens, "output_tokens": output_tokens,
            "counter": getattr(counter, "fingerprint", None), "count_mode": meter.count_mode,
            "embedding": "bge-m3-int8" if self.embedding is not None else "lexical-only",
            "embedding_reason": self.embedding_reason, "script_runner": "unavailable",
            "catalogue_namespace_id": namespace, "owner_mode": "STANDALONE_CHAT",
        }

        def after_build(runtime: Any) -> None:
            reader.bind(runtime)
            actual = runtime.arp.catalogue.namespace_id
            if actual != namespace:  # never report a namespace the runtime does not use
                self.profiles[profile_id]["catalogue_namespace_id"] = actual

        def arp_ports(execution_db: Path) -> ArpPorts:
            root = bootstrap_root(execution_db.with_name(execution_db.name + ".arp-root"), root_id=root_id)
            return ArpPorts(
                root_dir=root.directory, profile=profile, activation_receipt=activation, clock_ms=self.clock_ms,
                meter=meter, embedding=self.embedding,
                embedding_resource_ref=None if self.embedding is None else self.embedding_ref,
                acceptance=acceptance, artifacts=self.artifacts,
                # No approved script executor is bound in this slice: SCRIPT skills are
                # refused by name (RUNNER_UNAVAILABLE); the sandbox executor is async and
                # a sync adapter is RP-E follow-up work.
                script_runner=None,
            )

        return NativePlaneAssembly(arp_ports=arp_ports, authorization=self.authorization, caller_for=self.intent_caller, after_build=after_build)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "available": bool(self.profiles),
            "profiles": [dict(v) for v in self.profiles.values()],
            "embedding": "bge-m3-int8" if self.embedding is not None else "lexical-only",
            "embedding_reason": self.embedding_reason,
            "authorization_policy": self.authorization.policy_id,
        }


__all__ = (
    "BgeM3EmbeddingPort",
    "DeploymentToolAuthorization",
    "HostNativePlane",
    "embedding_port",
    "is_native_profile",
    "native_profile_id",
)
