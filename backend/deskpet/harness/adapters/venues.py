"""Kernel run client used by the companion background run adapter.

The dormant venue-neutral product chain (``ProductVenueRunAdapter``) was
removed in code-quality phase 8; the SDK execution path replaced it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

from deskpet.execution.contracts import (
    ActorContext,
    AdmissionSpec,
    ProviderLaunchSnapshot,
    RunNotFound,
    RunRef,
)
from deskpet.execution.delivery_contracts import RunEvent
from deskpet.harness.contracts import PreparedRunContextV1
from deskpet.harness.kernel import HostContext, RunKernel, RunRequest, root_run_identity
from deskpet.harness.ports import DecisionSignal, UserContinuationSignal


@dataclass(frozen=True, slots=True)
class VenueRunHandle:
    run_id: str
    events: AsyncIterator[RunEvent]
    _client: KernelRunClient
    _host: HostContext
    _session_id: str

    async def signal(self, signal: Mapping[str, object]) -> object:
        return await self._client.signal(self._ref(), self._host, signal)

    async def cancel(self, reason: str) -> object:
        return await self._client.cancel(self._ref(), self._host, reason)

    async def close(self) -> None:
        await self._client.close(self._ref(), self._host)

    def _ref(self) -> Mapping[str, object]:
        return {"run_id": self.run_id, "expected_session_id": self._session_id}


class KernelRunClient:
    def __init__(self, kernel: RunKernel) -> None:
        self._kernel = kernel

    @staticmethod
    def _parse_request(request: Mapping[str, object]) -> RunRequest:
        text = str(request.get("text") or "").strip()
        if not text:
            raise ValueError("run request text is required")
        request_id = str(request.get("request_id") or uuid.uuid4().hex)
        turn_id = str(request.get("turn_id") or uuid.uuid4().hex)
        raw_payload = request.get("payload", {})
        if not isinstance(raw_payload, Mapping):
            raise ValueError("run request payload must be a mapping")
        raw_tools = request.get("proposed_tools", ())
        if isinstance(raw_tools, str) or not isinstance(raw_tools, (list, tuple)):
            raise ValueError("proposed_tools must be a sequence of names")
        raw_messages = request.get("canonical_messages", ())
        if (
            isinstance(raw_messages, (str, bytes))
            or not isinstance(raw_messages, (list, tuple))
            or any(not isinstance(message, Mapping) for message in raw_messages)
        ):
            raise ValueError("canonical_messages must be a sequence of mappings")
        admission = request.get("admission")
        provider_snapshot = request.get("provider_launch_snapshot")
        if admission is not None and not isinstance(admission, AdmissionSpec):
            raise ValueError("admission must be a typed AdmissionSpec")
        if provider_snapshot is not None and not isinstance(
            provider_snapshot, ProviderLaunchSnapshot
        ):
            raise ValueError("provider launch snapshot must be typed")
        if "prepared" in request or "prepared_run_context" in request:
            raise ValueError("run request payload cannot construct prepared host context")
        return RunRequest(
            text=text,
            request_id=request_id,
            turn_id=turn_id,
            venue=str(request.get("venue") or "text"),
            mode=str(request.get("mode") or "general"),
            workspace_context=bool(request.get("workspace_context", False)),
            proposed_tools=tuple(str(name) for name in raw_tools),
            canonical_messages=tuple(dict(message) for message in raw_messages),
            payload=dict(raw_payload),
            admission=admission,
            provider_launch_snapshot=provider_snapshot,
        )

    async def start(
        self,
        request: Mapping[str, object],
        host: HostContext,
        *,
        prepared: PreparedRunContextV1 | None = None,
    ) -> VenueRunHandle:
        if prepared is not None and not isinstance(prepared, PreparedRunContextV1):
            raise TypeError("prepared must be a host-issued PreparedRunContextV1")
        parsed = self._parse_request(request)
        handle = await self._kernel.start(parsed, host, prepared=prepared)
        actor = host.actor(root_run_id=handle.root_run_id)
        return VenueRunHandle(
            run_id=handle.ref.run_id,
            events=self._kernel.observe(handle.ref, actor),
            _client=self,
            _host=host,
            _session_id=host.session_id,
        )

    async def start_blocked(
        self,
        request: Mapping[str, object],
        host: HostContext,
        *,
        reason_code: str,
        evidence_refs: Sequence[str],
        prepared: PreparedRunContextV1 | None = None,
    ) -> VenueRunHandle:
        if prepared is not None and not isinstance(prepared, PreparedRunContextV1):
            raise TypeError("prepared must be a host-issued PreparedRunContextV1")
        parsed = self._parse_request(request)
        handle = await self._kernel._start_blocked(
            parsed,
            host,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            prepared=prepared,
        )
        actor = host.actor(root_run_id=handle.root_run_id)
        return VenueRunHandle(
            run_id=handle.ref.run_id,
            events=self._kernel.observe(handle.ref, actor),
            _client=self,
            _host=host,
            _session_id=host.session_id,
        )

    async def resume(
        self, request_id: str, turn_id: str, host: HostContext
    ) -> VenueRunHandle | None:
        _, ref = root_run_identity(host.session_id, request_id, turn_id)
        actor = host.actor(root_run_id=ref.run_id)
        try:
            handle = await self._kernel.recover(ref, actor)
        except RunNotFound:
            return None
        return VenueRunHandle(
            handle.ref.run_id,
            self._kernel.observe(handle.ref, actor),
            self,
            host,
            host.session_id,
        )

    async def signal(
        self,
        ref: Mapping[str, object],
        actor: HostContext,
        signal: Mapping[str, object],
    ) -> object:
        run_ref = self._ref(ref)
        trusted_actor = await self._actor_for_ref(run_ref, actor)
        if str(signal.get("kind") or "") == "user_continuation":
            return await self._kernel.signal(
                run_ref,
                trusted_actor,
                UserContinuationSignal(
                    run_ref.run_id,
                    task_scope_id=str(signal.get("task_scope_id") or ""),
                    message_ref=str(signal.get("message_ref") or ""),
                    content=str(signal.get("content") or ""),
                    expected_boundary_version=int(signal.get("expected_boundary_version") or 0),
                ),
            )
        decision_id = str(signal.get("decision_id") or "").strip()
        if not decision_id:
            raise ValueError("decision_id is required")
        response = signal.get("response")
        if not isinstance(response, Mapping):
            response = {"value": response}
        return await self._kernel.signal(
            run_ref,
            trusted_actor,
            DecisionSignal(
                run_ref.run_id,
                decision_id,
                response,
                nonce=(None if signal.get("nonce") is None else str(signal["nonce"])),
                version=(None if signal.get("version") is None else int(signal["version"])),
            ),
        )

    async def cancel(
        self,
        ref: Mapping[str, object],
        actor: HostContext,
        reason: str,
    ) -> object:
        run_ref = self._ref(ref)
        trusted_actor = await self._actor_for_ref(run_ref, actor)
        return await self._kernel.cancel(run_ref, trusted_actor, reason)

    async def close(
        self,
        ref: Mapping[str, object],
        actor: HostContext,
    ) -> None:
        run_ref = self._ref(ref)
        trusted_actor = await self._actor_for_ref(run_ref, actor)
        await self._kernel.close(run_ref, trusted_actor)

    async def _actor_for_ref(
        self,
        ref: RunRef,
        host: HostContext,
    ) -> ActorContext:
        """Resolve and bind the authoritative root through a controlled Kernel seam.

        The first rootless lookup still enforces principal, session, and auth epoch.
        Only the persisted root is then bound before any mutation. This deliberate
        private call keeps RunKernel's public surface limited to its six lifecycle
        operations; never substitute ``ref.run_id`` because a child ID is not a root.
        """

        record = await self._kernel._query(ref, host.actor())
        return host.actor(root_run_id=record.context.root_run_id)

    @staticmethod
    def _ref(value: Mapping[str, object]) -> RunRef:
        run_id = str(value.get("run_id") or "").strip()
        session_id = str(value.get("expected_session_id") or "").strip()
        if not run_id or not session_id:
            raise ValueError("run_id and expected_session_id are required")
        return RunRef(run_id, session_id)


__all__ = [
    "KernelRunClient",
    "VenueRunHandle",
]
