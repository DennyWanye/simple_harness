# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Pure host policy for Companion tool visibility and explicit confirmation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from deskpet.tools.build_identity import (
    EffectClass,
    IdempotencyClass,
    canonical_hash,
)
from deskpet.tools.capabilities import (
    PreparedToolSet,
    ToolEffectClassification,
)


POLICY_VERSION = "companion-irreversible-effect-policy-v1"
_CONFIRM_ONLY = frozenset(
    {
        EffectClass.EXTERNAL_SEND,
        EffectClass.DESTRUCTIVE,
        EffectClass.PAYMENT,
        EffectClass.CREDENTIAL,
        EffectClass.PRIVACY,
        EffectClass.UNKNOWN,
    }
)
_NO_TOOL_RUN_KINDS = frozenset({"reflection", "evaluation"})


def is_companion_owner(owner_key: str) -> bool:
    parts = str(owner_key).split(":")
    return (
        len(parts) == 3
        and parts[0] == "companion"
        and bool(parts[1])
        and bool(parts[2])
    )


@dataclass(frozen=True)
class IrreversibleEffectPolicyDecision:
    visible_names: tuple[str, ...]
    confirm_only_names: tuple[str, ...]
    classifications: tuple[ToolEffectClassification, ...]
    policy_version: str
    policy_hash: str


class IrreversibleEffectPolicy:
    """Classify one frozen catalog without consulting mutable runtime state."""

    version = POLICY_VERSION

    def classify(
        self,
        *,
        owner_key: str,
        run_kind: str,
        specs: Iterable[object],
        existing_confirm_only_names: Iterable[str] = (),
    ) -> IrreversibleEffectPolicyDecision:
        ordered = tuple(sorted(specs, key=lambda item: str(getattr(item, "name", ""))))
        restricted_read_only = str(run_kind) in _NO_TOOL_RUN_KINDS
        companion = is_companion_owner(owner_key)
        classifications: list[ToolEffectClassification] = []
        visible: list[str] = []
        forced = set(str(item) for item in existing_confirm_only_names)

        for spec in ordered:
            name = str(getattr(spec, "name", ""))
            schema_hash = str(getattr(spec, "schema_hash", ""))
            handler_id = str(getattr(spec, "stable_handler_id", ""))
            raw_effect = getattr(spec, "effect_class", EffectClass.UNKNOWN)
            raw_idempotency = getattr(spec, "idempotency", IdempotencyClass.UNKNOWN)
            try:
                effect_class = (
                    raw_effect
                    if isinstance(raw_effect, EffectClass)
                    else EffectClass(str(raw_effect))
                )
            except ValueError:
                effect_class = EffectClass.UNKNOWN
            try:
                idempotency = (
                    raw_idempotency
                    if isinstance(raw_idempotency, IdempotencyClass)
                    else IdempotencyClass(str(raw_idempotency))
                )
            except ValueError:
                idempotency = IdempotencyClass.UNKNOWN
            high_risk = effect_class in _CONFIRM_ONLY
            disposition = (
                "excluded" if restricted_read_only and high_risk else "visible"
            )
            if disposition != "excluded":
                visible.append(name)
                if companion and high_risk:
                    forced.add(name)
                    disposition = "confirm_only"
                elif name in forced:
                    disposition = "confirm_only"
            classifications.append(
                ToolEffectClassification(
                    name=name,
                    schema_hash=schema_hash,
                    stable_handler_id=handler_id,
                    effect_class=effect_class.value,
                    idempotency=idempotency.value,
                    target_normalizer_version=str(
                        getattr(spec, "target_normalizer_version", "none")
                    ),
                    disposition=disposition,
                )
            )

        visible_set = set(visible)
        confirm_only = tuple(sorted(forced & visible_set))
        classification_tuple = tuple(classifications)
        payload = {
            "policy_version": self.version,
            "owner_class": "companion" if companion else "other",
            "run_kind": str(run_kind),
            "visible_names": visible,
            "confirm_only_names": list(confirm_only),
            "classifications": [item.fingerprint_payload() for item in classification_tuple],
        }
        return IrreversibleEffectPolicyDecision(
            visible_names=tuple(visible),
            confirm_only_names=confirm_only,
            classifications=classification_tuple,
            policy_version=self.version,
            policy_hash=canonical_hash(payload),
        )

    def apply(
        self,
        prepared: PreparedToolSet,
        *,
        owner_key: str,
        run_kind: str,
        specs: Iterable[object],
    ) -> PreparedToolSet:
        decision = self.classify(
            owner_key=owner_key,
            run_kind=run_kind,
            specs=specs,
            existing_confirm_only_names=prepared.confirm_only_names,
        )
        return prepared.with_effect_policy(
            visible_names=decision.visible_names,
            confirm_only_names=decision.confirm_only_names,
            effect_policy_version=decision.policy_version,
            effect_policy_hash=decision.policy_hash,
            effect_classifications=decision.classifications,
        )


__all__ = [
    "IrreversibleEffectPolicy",
    "IrreversibleEffectPolicyDecision",
    "POLICY_VERSION",
    "is_companion_owner",
]
