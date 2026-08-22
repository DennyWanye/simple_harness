# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Trusted product identity binding for the official Agent Memory contract."""

from __future__ import annotations

from pathlib import Path
import time
import uuid

import aiosqlite
from deskpet.companion.control_ingress import (
    LocalAuthSnapshotProvider,
    TrustedAuthSnapshotProvider,
    validate_auth_snapshot,
)
from simple_harness.runtime import AgentIdentity


class MemoryIdentityUnavailable(RuntimeError):
    code = "trusted_memory_identity_unavailable"


class MemorySessionRebind(RuntimeError):
    code = "memory_session_identity_rebind"


class ProductMemoryIdentityResolver:
    """Create stable household bindings and reject session identity rebinding."""

    def __init__(self, state_db_path: str | Path) -> None:
        self._path = Path(state_db_path)

    async def bind(self, *, session_id: str, trusted_actor_id: str) -> AgentIdentity:
        session = str(session_id).strip()
        actor = str(trusted_actor_id).strip()
        if not session or not actor:
            raise MemoryIdentityUnavailable(MemoryIdentityUnavailable.code)
        now = time.time()
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            deployment_row = await (
                await db.execute(
                    "SELECT instance_id FROM state_db_identity WHERE singleton=1"
                )
            ).fetchone()
            if deployment_row is None:
                raise MemoryIdentityUnavailable("state_db_identity_unavailable")
            deployment = str(deployment_row[0])
            binding = await (
                await db.execute(
                    "SELECT household_id FROM memory_identity_bindings WHERE actor_id=?",
                    (actor,),
                )
            ).fetchone()
            if binding is None:
                household = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO memory_identity_bindings(actor_id,household_id,created_at) VALUES(?,?,?)",
                    (actor, household, now),
                )
            else:
                household = str(binding[0])
            existing = await (
                await db.execute(
                    "SELECT deployment_id,household_id,actor_id FROM memory_session_identities WHERE session_id=?",
                    (session,),
                )
            ).fetchone()
            expected = (deployment, household, actor)
            if existing is not None and tuple(map(str, existing)) != expected:
                raise MemorySessionRebind(MemorySessionRebind.code)
            if existing is None:
                await db.execute(
                    "INSERT INTO memory_session_identities(session_id,deployment_id,household_id,actor_id,created_at) VALUES(?,?,?,?,?)",
                    (session, deployment, household, actor, now),
                )
            await db.commit()
        return AgentIdentity(deployment, household, actor, session)

    async def resolve(self, session_id: str) -> AgentIdentity:
        async with aiosqlite.connect(self._path) as db:
            row = await (
                await db.execute(
                    "SELECT deployment_id,household_id,actor_id,session_id FROM memory_session_identities WHERE session_id=?",
                    (str(session_id).strip(),),
                )
            ).fetchone()
        if row is None:
            raise MemoryIdentityUnavailable(MemoryIdentityUnavailable.code)
        return AgentIdentity(*(str(value) for value in row))


class ValidatedLocalMemoryIdentityAuthority:
    """Bind Memory identity exclusively from validated product auth state."""

    def __init__(
        self,
        state_db_path: str | Path,
        *,
        user_data_dir: str | Path,
        auth_provider: TrustedAuthSnapshotProvider | None = None,
    ) -> None:
        self._resolver = ProductMemoryIdentityResolver(state_db_path)
        self._user_data_dir = Path(user_data_dir)
        self._auth_provider = auth_provider or LocalAuthSnapshotProvider()

    @property
    def resolver(self) -> ProductMemoryIdentityResolver:
        return self._resolver

    async def bind(self, *, session_id: str) -> AgentIdentity:
        snapshot = await self._auth_provider.current_snapshot()
        human = validate_auth_snapshot(
            snapshot,
            user_data_dir=str(self._user_data_dir),
        )
        actor = str(human.identity_namespace_hash).strip()
        if not actor or actor == human.profile_id or actor == "legacy_local_profile":
            raise MemoryIdentityUnavailable("validated_actor_identity_invalid")
        return await self._resolver.bind(
            session_id=session_id,
            trusted_actor_id=actor,
        )


__all__ = (
    "MemoryIdentityUnavailable",
    "MemorySessionRebind",
    "ProductMemoryIdentityResolver",
    "ValidatedLocalMemoryIdentityAuthority",
)
