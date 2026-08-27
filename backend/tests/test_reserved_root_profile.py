from __future__ import annotations

import pytest

from deskpet.harness.contracts import (
    PreparedRunContextV1,
    ReservedRootProfileSelectionV1,
)
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec


def _selection() -> ReservedRootProfileSelectionV1:
    return ReservedRootProfileSelectionV1(
        profile_key="skill.install.verify",
        catalog_generation=7,
        driver_kind="skill-install-verifier",
        purpose="skill.install.verify",
        intent_id="intent-1",
        operation_id="operation-1",
        content_hash="a" * 64,
    )


def test_reserved_selection_is_host_only_and_fingerprint_bound() -> None:
    selected = PreparedRunContextV1(
        persistence_required=True, reserved_root_profile=_selection()
    )
    ordinary = PreparedRunContextV1(persistence_required=True)
    assert selected.prepared_fingerprint != ordinary.prepared_fingerprint
    assert selected.reserved_root_profile.to_dict()["purpose"] == "skill.install.verify"
    with pytest.raises(TypeError, match="Host-issued"):
        PreparedRunContextV1(  # type: ignore[arg-type]
            persistence_required=True, reserved_root_profile={"profile_key": "x"}
        )


def test_reserved_profile_is_not_model_spawnable_and_requires_exact_generation() -> None:
    profiles = ProfileRegistry(
        (
            ProfileSpec("agent.general", "general", "react"),
            ProfileSpec(
                "skill.install.verify", None, "skill-install-verifier",
                launch_policy="reserved_control",
            ),
        ),
        generation=7,
    )
    assert "skill.install.verify" not in profiles.model_spawnable
    resolved = profiles.resolve(
        "skill.install.verify", generation=7, launch_policy="reserved_control"
    )
    assert resolved.driver_kind == "skill-install-verifier"
    with pytest.raises(ValueError, match="stale"):
        profiles.resolve(
            "skill.install.verify", generation=6,
            launch_policy="reserved_control",
        )
    with pytest.raises(ValueError, match="cannot launch"):
        profiles.resolve(
            "skill.install.verify", generation=7,
            launch_policy="model_spawnable",
        )
