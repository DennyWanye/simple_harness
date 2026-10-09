# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Verifier Router (§14.1, ORCH §12.4, plan D9/D23).

The Task Contract's ``verification_policy`` names the layers that *must* run.
Layers are recorded in the §14.1 order (format → rule → critic → test); the first required
layer that FAILs or ERRORs short-circuits the rest (D23).  Layers the policy did
not request are recorded as ``NOT_REQUIRED`` — never as PASS.  A layer that was
requested but could not run (e.g. the Critic's verdict was unreadable) is
``ERROR`` and blocks acceptance exactly like a FAIL: "未运行的必需层不能算 PASS".

The Critic layer is executed by a callback supplied by the orchestrator (it needs
a dispatch intent, budget and the Agent bridge); everything else is local.
When both Critic and code_test are required, code_test executes after the deterministic
gates and before Critic so its independent output can be reviewed. Its result is
recorded at the existing code_test position.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..artifacts.workspace import Workspace
from ..contracts import Artifact, ContractError, Mission, ResultEnvelope, Task
from ..contracts.models import VERIFICATION_LAYERS
from ..governance.domains import DomainProfileV1
from ..memory.verified_knowledge import KnowledgeIndex
from .critics import CriticVerdict
from .deterministic_checks import (
    ERROR,
    FAIL,
    NOT_REQUIRED,
    PASS,
    LayerResult,
    code_test,
    format_check,
)
from .domain_handlers import handler_for
from .human_review import NEEDS_HUMAN, SUSPENDED, human_layer

if TYPE_CHECKING:
    from .assurance_local import LocalVerificationRecorder

CriticRunner = Callable[[str | None], Awaitable[CriticVerdict]]
VERIFIER_VERSION = "verifier-v1"  # step 6 (S6-09): recorded on every layer result


@dataclass(frozen=True, slots=True)
class Verdict:
    passed: bool
    layers: tuple[LayerResult, ...]
    critic: CriticVerdict | None
    short_circuited_at: str | None
    suspended: bool = False  # step 7 (D7-8'): the sixth layer waits for a person

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "layers": [layer.to_json() for layer in self.layers],
            "critic": None if self.critic is None else self.critic.to_json(),
            "short_circuited_at": self.short_circuited_at,
            "suspended": self.suspended,
        }

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [layer.to_json() for layer in self.layers if layer.status in {FAIL, ERROR}]


class VerifierRouter:
    def __init__(
        self,
        *,
        test_timeout: float = 120.0,
        local_code_execution: bool = True,
        executor: Any = None,
        domain: Any = None,
    ) -> None:
        self._test_timeout = test_timeout
        self._local_code_execution = local_code_execution  # host support 0.9.8
        self._executor = executor  # P3.2 D2: what code_test runs through
        self._domain = domain  # P3.3 D1: the Mission's frozen domain profile

    async def verify(
        self,
        *,
        mission: Mission,
        task: Task,
        envelope: ResultEnvelope,
        artifacts: Sequence[Artifact],
        verification_copy: Workspace,
        client_result_id: str | None,
        run_critic: CriticRunner,
        recorder: Callable[[LayerResult], Awaitable[None]] | None = None,
        tampered: Sequence[str] = (),
        knowledge: KnowledgeIndex | None = None,
        human: Mapping[str, Any] | None = None,
        reuse: Mapping[str, LayerResult] | None = None,
        needs_human_allowed: bool = True,
        domain: DomainProfileV1 | None = None,
        local_check_recorder_factory: Callable[[dict[str, Any]], LocalVerificationRecorder] | None = None,
    ) -> Verdict:
        actual_domain = domain if domain is not None else self._domain
        handler = handler_for(actual_domain)
        local_check_recorder = None
        frozen_input_hash = None

        def actual_local_inputs() -> dict[str, Any]:
            from .assurance_local import freeze_verifier_inputs

            return freeze_verifier_inputs(
                mission=mission, task=task, envelope=envelope, artifacts=artifacts,
                verification_copy=verification_copy, client_result_id=client_result_id,
                tampered=tampered, knowledge=knowledge,
                local_code_execution=self._local_code_execution, domain=actual_domain,
            )

        if local_check_recorder_factory is not None:
            from ..assurance.codec import fingerprint

            actual_inputs = actual_local_inputs()
            frozen_input_hash = fingerprint(actual_inputs)
            local_check_recorder = local_check_recorder_factory(actual_inputs)
        required = set(task.verification_policy)
        if not self._local_code_execution and any(
            c.startswith("pytest:") for c in task.success_criteria
        ):
            # host support review round 2 P2-5: a Task from before the switch keeps a pytest
            # criterion nobody can judge here; the rule layer must run and FAIL it, whatever
            # the policy says — a Critic's PASS alone may never complete it
            required.add("rule_check")
        layers: list[LayerResult] = []
        critic: CriticVerdict | None = None
        short_at: str | None = None
        test_output: str | None = None
        prepared_code_test: LayerResult | None = None
        escalated = False  # any required verifier can ask for a person
        suspended = False

        async def record(result: LayerResult) -> None:
            layers.append(result)
            if recorder is not None:
                await recorder(result)

        def local(layer: str, execute: Callable[[], LayerResult]) -> LayerResult:
            if local_check_recorder is None:
                return execute()

            def bound_execute() -> LayerResult:
                from ..assurance.codec import AssuranceError, fingerprint

                if fingerprint(actual_local_inputs()) != frozen_input_hash:
                    raise AssuranceError("CHECK_INPUT_CHANGED")
                result = execute()
                if fingerprint(actual_local_inputs()) != frozen_input_hash:
                    raise AssuranceError("CHECK_INPUT_CHANGED")
                return result

            return local_check_recorder.run(layer, bound_execute)

        def run_rules() -> LayerResult:
            result = handler.rules(
                envelope, task, artifacts=artifacts, verification_copy=verification_copy,
                tampered=tampered, knowledge=knowledge,
                local_code_execution=self._local_code_execution, domain=actual_domain,
            )
            return result

        for layer in VERIFICATION_LAYERS:
            if layer == "human_review" and escalated:
                required.add("human_review")  # needs_human forces the sixth layer (D7-8')
            # Preserve the actual execution evidence even when the later Critic
            # rejects content. A test that already ran must never become SKIPPED.
            if layer == "code_test" and prepared_code_test is not None:
                await record(prepared_code_test)
                if short_at is None and prepared_code_test.status in {FAIL, ERROR}:
                    short_at = layer
                continue
            if short_at is not None:
                await record(
                    LayerResult(
                        layer,
                        NOT_REQUIRED if layer not in required else "SKIPPED",
                        "short-circuited",
                        {},
                    )
                )
                continue
            if layer not in required:
                await record(
                    LayerResult(layer, NOT_REQUIRED, "not in the Task verification policy", {})
                )
                continue
            reusable = reuse is not None and layer in reuse
            if local_check_recorder is not None and layer in {"format_check", "rule_check"}:
                # Until a persisted exact local receipt is revalidated, perform
                # the side-effect-free check again. Old layer cache is not proof.
                reusable = False
            if reusable and reuse is not None:  # D7-8': resume reuses what actually passed
                result = reuse[layer]
                if layer == "critic_review":
                    critic = CriticVerdict.from_json(result.detail)
            elif layer == "format_check":
                result = local(layer, lambda: format_check(envelope, client_result_id=client_result_id))
            elif layer == "rule_check":
                result = local(layer, run_rules)
            elif layer == "critic_review":
                # The recorded layer order is a durable contract. Execute the required
                # test here, after format/rule passed, but record it in its usual slot.
                # Only the verifier's run (or a durable reused layer) supplies stdout;
                # the Worker's claimed run_tests output is never an input here.
                if "code_test" in required and self._local_code_execution:
                    reused_test = None if reuse is None else reuse.get("code_test")
                    if reused_test is not None and (
                        local_check_recorder is None
                        or "assurance_executor_check_ref" in reused_test.detail
                    ):
                        # An executor run already imported as a receipt is reused as
                        # is (never re-executed); an old cached layer without one is
                        # not proof on the assured lane and runs again.
                        prepared_code_test = reused_test
                    else:
                        prepared_code_test = await code_test(
                            task,
                            verification_copy=verification_copy,
                            timeout=self._test_timeout,
                            executor=self._executor,
                            result_id=envelope.id,
                            artifacts=artifacts,
                        )
                        if local_check_recorder is not None:
                            prepared_code_test = local_check_recorder.record_executor(
                                "code_test", prepared_code_test
                            )
                    runs = prepared_code_test.detail.get("runs", [])
                    test_output = "\n".join(
                        str(run.get("stdout", "")) for run in runs if isinstance(run, Mapping)
                    )
                if prepared_code_test is not None and prepared_code_test.status != PASS:
                    # A failing required deterministic check needs no model judgment.
                    # The failure is recorded in the code_test slot below.
                    result = LayerResult(
                        layer, "SKIPPED", "code_test failed before critic review", {}
                    )
                else:
                    try:
                        critic = await run_critic(test_output)
                        result = LayerResult(
                            layer,
                            PASS if critic.passed else FAIL,
                            "critic found no blocker"
                            if critic.passed
                            else "critic found a blocker: "
                            + "; ".join(
                                # Assurance review findings carry ``description`` (2026-09-25
                                # desktop run showed "blocker: None; None").
                                str(f.get("detail") or f.get("description") or f.get("criterion_id") or "?")
                                for f in critic.findings
                                if f.get("severity") == "blocker"
                            ),
                            critic.to_json(),
                        )
                        if critic.passed and critic.needs_human:  # D7-8': not a PASS, not a FAIL
                            if needs_human_allowed:
                                result = LayerResult(
                                    layer,
                                    NEEDS_HUMAN,
                                    "the Critic cannot reliably judge; a person decides",
                                    critic.to_json(),
                                )
                                escalated = True
                            else:
                                result = LayerResult(
                                    layer,
                                    FAIL,
                                    "the Critic asked for a person again; one escalation per Task",
                                    critic.to_json(),
                                )
                    except ContractError as error:
                        # 带码的错误（审阅调用被打断）把码放进明细：下游按码判，不读这句话
                        code = getattr(error, "code", None)
                        result = LayerResult(layer, ERROR, f"critic verdict unusable: {error}",
                                             {"code": code} if isinstance(code, str) and code else {})
            elif layer == "code_test" and not self._local_code_execution:
                # host support 0.9.8: a Task from before the switch still asks for the layer;
                # it cannot run here, which is an ERROR — never a PASS or NOT_REQUIRED
                result = LayerResult(
                    layer,
                    ERROR,
                    "local_code_execution is off in this deployment: model-written tests are not run on this machine",
                    {"undeployed": True, "local_code_execution": False},
                )
            elif layer == "code_test":
                # step 3 (D3-9'): Mission-level pytest targets are judged on the
                # integrated tree, not against one Task's partial workspace
                result = prepared_code_test or await code_test(
                    task,
                    verification_copy=verification_copy,
                    timeout=self._test_timeout,
                    executor=self._executor,
                    result_id=envelope.id,
                    artifacts=artifacts,
                )
            elif layer == "human_review":  # step 7 (D7-8'): the sixth layer
                result = human_layer(human)
                suspended = result.status == SUSPENDED
            else:  # formal_check is not deployed in this build
                result = LayerResult(
                    layer, ERROR, "layer not deployed in this build", {"undeployed": True}
                )
            if result.status == NEEDS_HUMAN:
                if not needs_human_allowed and layer != "critic_review":
                    result = LayerResult(
                        layer,
                        FAIL,
                        "one human escalation per Task; quota already used",
                        {**dict(result.detail), "escalation_quota_exhausted": True},
                    )
                else:
                    escalated = True
            await record(result)
            if result.status in {FAIL, ERROR}:
                short_at = layer
        human_passed = any(done.layer == "human_review" and done.status == PASS for done in layers)
        passed = (
            short_at is None
            and not suspended
            and all(
                layer.status == PASS or (layer.status == NEEDS_HUMAN and human_passed)
                for layer in layers
                if layer.layer in required
            )
        )
        return Verdict(
            passed=passed,
            layers=tuple(layers),
            critic=critic,
            short_circuited_at=short_at,
            suspended=suspended,
        )


__all__ = ("VERIFIER_VERSION", "CriticRunner", "Verdict", "VerifierRouter")
