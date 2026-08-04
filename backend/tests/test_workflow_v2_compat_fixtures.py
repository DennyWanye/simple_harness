from __future__ import annotations

from deskpet.workflows.definitions.v2.deep_research import DEEP_RESEARCH_V2


def test_deep_research_v2_manifest_and_callable_bundle_remain_frozen():
    manifest = DEEP_RESEARCH_V2.manifest
    assert manifest.definition_hash == "c0fb5261da2cf31dabd2131b44b963947444d2dcbd03a4c5bbc0a63f286dfa80"
    assert manifest.callable_source_hash == "ef57fee8617ded6550a1cc47c65b22cdb8adc8aeaf4c7c564f6af5fc9f6b7031"
    assert manifest.prompt_hash == "a75393ad1c463a64c237c5d1579c2d91163ab9e0c34c842150206a5d26f7db7f"
    assert manifest.policy_hash == "dea7495161b674d0bd8c913361856c644e5758c4f03f58e58c9ca9db5b1b4a4a"
    assert manifest.state_hash == "f3ce983c7a7245fed2e17084c71f94cb01223235486ac459c382c47bc9cf4352"


def test_deep_research_v2_implementation_bundle_matches_checkout_lock_variant():
    expected_by_lock = {
        "1013a7b449853880a44dd4219d1fe8090dff38055fd4a42d6902887693e4c8ef":
            "a5c1626cccf41351bdc9531f735f42f53278eb426c5462f1217c3692a022b9e2",
    }
    manifest = DEEP_RESEARCH_V2.manifest
    assert manifest.dependency_lock_hash in expected_by_lock
    assert manifest.implementation_bundle_hash == expected_by_lock[manifest.dependency_lock_hash]
