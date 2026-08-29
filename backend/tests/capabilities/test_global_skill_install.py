from __future__ import annotations

from deskpet.capabilities.contracts import canonical_global_owner_key
from deskpet.capabilities.skill_install import GlobalSkillInstallAuthority


def test_global_owner_key_is_stable_and_identity_isolated() -> None:
    first = canonical_global_owner_key("a" * 64)
    assert first == canonical_global_owner_key("a" * 64)
    assert first != canonical_global_owner_key("b" * 64)
    assert first.startswith("user:v2:")


def test_global_install_authority_contains_no_project_scope() -> None:
    authority = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="principal-a",
        identity_namespace_hash="a" * 64,
    )
    assert authority.owner_scope_key.startswith("user:v2:")
    assert not hasattr(authority, "project_id")
