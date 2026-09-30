"""Exercise the real Host factory and its exact Host-control delegate."""
import pytest

from simple_harness import HostControlAuthorityV1
from simple_harness.execution.uow import RunState
from simple_harness.runtime import StartModeDriverRouter

from deskpet.sdk_adapters.skill_install_verification import (
    ProductRootDriverRouter, SkillInstallVerificationAttemptResolver,
    SKILL_INSTALL_VERIFICATION_PURPOSE, skill_install_verification_authority_hash,
)
from tests.sdk_adapters.test_skill_install_verification_router import (
    _attempt, _Driver, _Store, _invocation,
)


# 2026-09-30 删除 test_real_main_factory_selects_sdk_react_and_original_control_delegate：
# 它借 tests.test_provider_runtime_refresh.test_human_epoch_composition_registers_three_authorities
# 真跑 main._build_product_sdk_runtime_stack，该冒烟用例依赖记忆 SDK（MemoryManager），
# 已随记忆 SDK 于 2026-09-10 删除（fb08f475）。真组装覆盖在 tests/sdk_adapters/test_composition.py。


@pytest.mark.asyncio
async def test_sdk_selector_keeps_host_verification_checks_and_never_falls_back():
    attempt = _attempt()
    ordinary, verifier = _Driver("react"), _Driver("verifier")
    control = ProductRootDriverRouter(react_driver=ordinary,
        attempt_resolver=SkillInstallVerificationAttemptResolver(_Store(attempt)),
        verification_driver_factory=lambda _: verifier)
    router = StartModeDriverRouter(ordinary, control)
    correct = HostControlAuthorityV1(SKILL_INSTALL_VERIFICATION_PURPOSE,
        attempt.attempt_id, skill_install_verification_authority_hash(attempt), attempt.attempt_generation)
    wrong = HostControlAuthorityV1(SKILL_INSTALL_VERIFICATION_PURPOSE,
        attempt.attempt_id, "d" * 64, attempt.attempt_generation)
    result = await router.start(_invocation(authority=correct), context=None, cancel=None)
    assert result.payload == {"route": "verifier"}
    rejected = await router.start(_invocation(authority=wrong), context=None, cancel=None)
    assert rejected.state is RunState.FAILED
    assert rejected.payload["reason_code"] == "verification_authority_mismatch"
    assert ordinary.calls == 0 and verifier.calls == 1


def test_harness_lock_and_manifest_match_current_candidate():
    import json, tomllib, hashlib
    from deskpet.sdk_adapters.sdk_candidate import (
        sdk_wheel_path, SDK_VERSION, SDK_WHEEL_SHA256,
        SDK_CANDIDATE_MANIFEST_FILENAME, SDK_CANDIDATE_MANIFEST_SHA256, SDK_SOURCE_COMMIT,
    )
    wheel = sdk_wheel_path()
    lock = tomllib.loads((wheel.parent.parent / "uv.lock").read_text())
    package = next(p for p in lock["package"] if p["name"] == "simple-harness-sdk")
    assert package["version"] == SDK_VERSION
    assert package["wheels"] == [{"filename": wheel.name, "hash": "sha256:" + SDK_WHEEL_SHA256}]
    manifest = wheel.parent / SDK_CANDIDATE_MANIFEST_FILENAME
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == SDK_CANDIDATE_MANIFEST_SHA256
    value = json.loads(manifest.read_text())
    assert value["commit"] == SDK_SOURCE_COMMIT
    assert value["artifacts"][wheel.name] == SDK_WHEEL_SHA256
