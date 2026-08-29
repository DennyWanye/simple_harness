"""Stable human identity derivation for Relay and local-only domains."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .authority import RevocationBarrier, get_process_revocation_barrier
from .contracts import OwnerRef
from .identity_gate import FrozenOwnerIdentity, IdentityReadyGate

_IDENTITY_DOMAIN = b"deskpet-companion-profile-v1\0"


@dataclass(frozen=True, slots=True)
class HumanIdentity:
    profile_id: str
    identity_namespace_hash: str
    identity_kind: str


def _namespaced_hash(namespace: str, stable_id: str) -> str:
    # 2026-08-09：`relay` 命名空间随托管登录一并移除，只剩本地身份。命名空间前缀
    # 保留在摘要里——它是既有 profile 的哈希输入，去掉会让存量身份全部重算。
    if namespace not in {"local"}:
        raise ValueError("unsupported identity namespace")
    value = stable_id.strip()
    if not value:
        raise ValueError("stable identity is required")
    return hashlib.sha256(
        _IDENTITY_DOMAIN
        + namespace.encode("ascii")
        + b"\0"
        + value.encode("utf-8")
    ).hexdigest()


def load_or_create_local_identity(user_data_dir: str | Path) -> HumanIdentity:
    root = Path(user_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _validate_private_identity_directory(root)
    identity_path = root / "companion-local-identity.json"
    try:
        os.lstat(identity_path)
    except FileNotFoundError:
        stable_id = str(uuid.uuid4())
        payload = json.dumps(
            {"schema_version": 1, "local_identity_id": stable_id},
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            descriptor = os.open(
                identity_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                identity_path.unlink(missing_ok=True)
                raise
    raw = json.loads(_read_validated_identity_file(identity_path))
    stable_id = str(raw["local_identity_id"])
    uuid.UUID(stable_id)
    digest = _namespaced_hash("local", stable_id)
    return HumanIdentity(
        profile_id="legacy_local_profile",
        identity_namespace_hash=digest,
        identity_kind="local",
    )


def _validate_private_identity_directory(root: Path) -> None:
    """Reject identity roots another local principal can replace files in."""

    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PermissionError("local identity directory must be a real directory")
    if hasattr(os, "getuid"):
        if info.st_uid != os.getuid():
            raise PermissionError("local identity directory has another owner")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError("local identity directory is shared-writable")


def _read_validated_identity_file(identity_path: Path) -> str:
    """Open the identity without following links and validate the opened inode."""

    before = os.lstat(identity_path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PermissionError("local identity must be a regular non-symlink file")
    if hasattr(os, "getuid"):
        if before.st_uid != os.getuid():
            raise PermissionError("local identity has another owner")
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise PermissionError("local identity must have mode 0600")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(identity_path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PermissionError("local identity must be a regular file")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PermissionError("local identity changed while opening")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class OwnerExecutableProjectionPort(Protocol):
    async def close_owner_ingress(
        self, owner: OwnerRef, *, binding_epoch: int
    ) -> object: ...

    async def cleanup_owner_runtime(
        self, owner: OwnerRef, *, binding_epoch: int
    ) -> object: ...

    async def activate_owner(
        self, owner: OwnerRef, *, owner_key: str, binding_epoch: int
    ) -> object: ...


class OwnerRuntimeLifecyclePort(Protocol):
    async def recover(self, owner: OwnerRef) -> object: ...

    async def start(self, owner: OwnerRef) -> bool: ...

    async def pause(self, *, timeout: float | None = None) -> None: ...


class ReadyOwnerProjection:
    """Task 2 default; Task 7 replaces this with the production projection."""

    async def close_owner_ingress(
        self, owner: OwnerRef, *, binding_epoch: int
    ) -> object:
        return {"ready": True}

    async def cleanup_owner_runtime(
        self, owner: OwnerRef, *, binding_epoch: int
    ) -> object:
        return {"ready": True}

    async def activate_owner(
        self, owner: OwnerRef, *, owner_key: str, binding_epoch: int
    ) -> object:
        return {"ready": True}


class ProfileBindingCoordinator:
    """Serializes owner switches and acknowledges only a ready projection."""

    def __init__(
        self,
        *,
        store: Any,
        gate: IdentityReadyGate,
        projection: OwnerExecutableProjectionPort | None = None,
        runtime: OwnerRuntimeLifecyclePort | None = None,
        runtime_start_enabled: bool = False,
        barrier: RevocationBarrier | None = None,
        device_scope: str = "desktop",
    ) -> None:
        self.store = store
        self.gate = gate
        self.projection = projection or ReadyOwnerProjection()
        self.runtime = runtime
        self.runtime_start_enabled = runtime_start_enabled
        self.barrier = barrier or get_process_revocation_barrier()
        self.device_scope = device_scope
        self._process_owner: OwnerRef | None = None

    def __deepcopy__(self, _memo):
        # The coordinator owns the process-wide RevocationBarrier and Store.
        return self

    @staticmethod
    def owner_key(owner: OwnerRef) -> str:
        return f"companion:{owner.profile_id}:{owner.profile_generation}"

    async def bind(
        self,
        *,
        identity: HumanIdentity,
        profile_generation: int,
        expected_binding_epoch: int,
        control_facts: Mapping[str, Any],
    ) -> FrozenOwnerIdentity:
        owner = OwnerRef(identity.profile_id, profile_generation)
        previous = self.store.get_profile_binding(device_scope=self.device_scope)
        previous_owner = (
            OwnerRef(
                str(previous["profile_id"]),
                int(previous["profile_generation"]),
            )
            if previous is not None
            else None
        )
        first_process_bind = self._process_owner is None
        owner_changed = previous_owner is not None and previous_owner != owner
        if owner_changed:
            # A profile switch is unready before any close/cleanup awaits.
            try:
                self.gate.unbind(
                    expected_binding_epoch=int(previous["binding_epoch"])
                )
            except ValueError:
                # A new process starts unready even when the durable binding
                # still says ready; there is no in-process epoch to clear.
                if self.gate.ready:
                    raise
        if (
            self.runtime is not None
            and owner_changed
        ):
            await self.runtime.pause()
        async with self.barrier.exclusive():
            if previous_owner is not None and previous_owner != owner:
                await self.projection.close_owner_ingress(
                    previous_owner,
                    binding_epoch=int(previous["binding_epoch"]),
                )
            result = self.store.bind_profile_with_control_command(
                device_scope=self.device_scope,
                profile_id=owner.profile_id,
                profile_generation=owner.profile_generation,
                identity_namespace_hash=identity.identity_namespace_hash,
                expected_binding_epoch=expected_binding_epoch,
                control_facts=control_facts,
            )
        binding_epoch = int(result["binding_epoch"])
        needs_activation = bool(result["changed_owner"]) or first_process_bind
        if needs_activation:
            if previous_owner is not None:
                await self.projection.cleanup_owner_runtime(
                    previous_owner,
                    binding_epoch=int(previous["binding_epoch"]),
                )
            activation = await self.projection.activate_owner(
                owner,
                owner_key=self.owner_key(owner),
                binding_epoch=binding_epoch,
            )
            if isinstance(activation, Mapping) and not bool(
                activation.get("ready", False)
            ):
                raise RuntimeError("owner_projection_not_ready")
            if self.runtime is not None:
                await self.runtime.recover(owner)
                runtime_started = False
                try:
                    if self.runtime_start_enabled:
                        prebound_start = getattr(
                            self.runtime,
                            "start_prebound",
                            None,
                        )
                        started = (
                            await prebound_start(
                                owner,
                                binding_epoch=binding_epoch,
                            )
                            if callable(prebound_start)
                            else await self.runtime.start(owner)
                        )
                        if not started:
                            raise RuntimeError(
                                "companion_runtime_not_started"
                            )
                        runtime_started = True
                    self.store.mark_profile_binding_ready(
                        device_scope=self.device_scope,
                        owner=owner,
                        expected_binding_epoch=binding_epoch,
                    )
                except BaseException:
                    if runtime_started:
                        await self.runtime.pause()
                    raise
            else:
                self.store.mark_profile_binding_ready(
                    device_scope=self.device_scope,
                    owner=owner,
                    expected_binding_epoch=binding_epoch,
                )
        elif previous is None or previous["status"] != "ready":
            self.store.mark_profile_binding_ready(
                device_scope=self.device_scope,
                owner=owner,
                expected_binding_epoch=binding_epoch,
            )
        frozen = FrozenOwnerIdentity(
            owner=owner,
            owner_key=self.owner_key(owner),
            binding_epoch=binding_epoch,
        )
        try:
            self.gate.bind(frozen)
        except BaseException:
            if self.runtime is not None and self.runtime_start_enabled:
                await self.runtime.pause()
            raise
        self._process_owner = owner
        return frozen

    async def unbind(
        self,
        *,
        expected_binding_epoch: int,
        control_facts: Mapping[str, Any],
    ) -> None:
        previous = self.store.get_profile_binding(device_scope=self.device_scope)
        if previous is not None:
            self.gate.unbind(expected_binding_epoch=expected_binding_epoch)
        if self.runtime is not None:
            await self.runtime.pause()
        async with self.barrier.exclusive():
            if previous is not None:
                owner = OwnerRef(
                    str(previous["profile_id"]),
                    int(previous["profile_generation"]),
                )
                await self.projection.close_owner_ingress(
                    owner, binding_epoch=expected_binding_epoch
                )
            result = self.store.unbind_profile_with_control_command(
                device_scope=self.device_scope,
                expected_binding_epoch=expected_binding_epoch,
                control_facts=control_facts,
            )
        owner = result["owner"]
        await self.projection.cleanup_owner_runtime(
            owner, binding_epoch=expected_binding_epoch
        )
        self._process_owner = None

    async def delete_current_profile(self, *, reason_code: str) -> bool:
        """Tombstone the bound generation under the process-wide barrier."""

        previous = self.store.get_profile_binding(device_scope=self.device_scope)
        if previous is None:
            return False
        owner = OwnerRef(
            str(previous["profile_id"]),
            int(previous["profile_generation"]),
        )
        binding_epoch = int(previous["binding_epoch"])
        self.gate.unbind(expected_binding_epoch=binding_epoch)
        if self.runtime is not None:
            await self.runtime.pause()
        async with self.barrier.exclusive():
            await self.projection.close_owner_ingress(
                owner, binding_epoch=binding_epoch
            )
            deleted = self.store.delete_profile_generation(
                owner, reason_code=reason_code
            )
        await self.projection.cleanup_owner_runtime(
            owner, binding_epoch=binding_epoch
        )
        self._process_owner = None
        return bool(deleted)


__all__ = [
    "HumanIdentity",
    "OwnerExecutableProjectionPort",
    "OwnerRuntimeLifecyclePort",
    "ProfileBindingCoordinator",
    "ReadyOwnerProjection",
    "load_or_create_local_identity",
]
