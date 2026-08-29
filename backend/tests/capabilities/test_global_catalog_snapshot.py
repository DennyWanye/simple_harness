from __future__ import annotations

from deskpet.capabilities.run_catalog import PreparedRunCatalogLease


def _lease(entries):
    lease = object.__new__(PreparedRunCatalogLease)
    object.__setattr__(lease, "lease_entries", tuple(entries))
    return lease


def test_user_global_entries_are_frozen_and_project_independent() -> None:
    selected = {
        "scope": "user",
        "scope_key": "user:v2:" + "a" * 64,
        "owner_key": "principal-a",
    }
    entry = {
        "entry_kind": "pack",
        "pack_id": "global-proof",
        "version": "1.0.0",
        "manifest_hash": "b" * 64,
        "selected_binding": selected,
    }
    lease = _lease((entry,))
    assert lease.user_global_pack_entries(
        owner_key="principal-a", user_scope_key=selected["scope_key"]
    ) == (entry,)
    assert lease.user_global_pack_entries(
        owner_key="principal-b", user_scope_key=selected["scope_key"]
    ) == ()
