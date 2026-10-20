# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The Action Executor (plan D7-5 / D7-5'): the only caller of connectors.

It never writes the library itself.  Every state change goes through the Commit
Service: ``begin_handoff`` re-checks the approval binding, reserves the budget and writes
HANDED_OFF (the outbox) in one transaction *before* the connector is called; the call runs
in a worker thread (or, for an ``async def`` connector, as a task on the loop) under
``connector_timeout_seconds`` so the event loop never blocks; a definite refusal is FAILED,
anything after the hand-off that is not a matching receipt is UNKNOWN.  A call that outlives
its timeout stays "in flight" until it really ends.  UNKNOWN is never handed off blindly:
``reconcile`` asks the connector by idempotency key and only a CONFIRMED_NOT_STARTED answer
allows one more hand-off with the *same* key (ORCH §12.1–§12.2, §12.6; SDK effects /
reconciliation semantics)."""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from simple_harness.contracts import canonical_json

from ..observability.secrets import find_secrets
from ..orchestrator.action_commits import ActionCommitError
from ..storage.planning_admission_store import PlanningAdmissionStore
from . import operation_reconciliation
from .connectors import ConnectorRejected, Receipt
from .operation_profiles_generic import is_async
from .operation_reconciliation import context, stored_negative_proof

logger = logging.getLogger(__name__)

#: 一份回执规范 JSON 后的上限；超了或不是规范 JSON 的回执不算数（结果不明，去核对）。
RECEIPT_LIMIT_BYTES = 64 * 1024
#: 连接器报错文字进库前截到这么长。
ERROR_TEXT_LIMIT = 500


def checked_receipt(value: object) -> Receipt | None:
    """连接器交回的回执：必须是 :class:`Receipt`、能规范成 JSON、不超过上限；否则 ``None``。"""

    if not isinstance(value, Receipt):
        return None
    try:
        encoded = canonical_json(value.to_json())
    except (TypeError, ValueError):
        return None
    return value if len(encoded.encode("utf-8")) <= RECEIPT_LIMIT_BYTES else None


def error_text(error: BaseException) -> str:
    """连接器报错进库的样子：截短；像带密钥就只留异常类型名。"""

    text = f"{type(error).__name__}: {error}"[:ERROR_TEXT_LIMIT]
    return type(error).__name__ if find_secrets(text) else text


if TYPE_CHECKING:
    from ..governance.policies import DeploymentPolicy
    from ..orchestrator.commit_service import CommitService


def lookup_verdict(connector: Any, found: Receipt | None) -> str:
    """What a lookup's answer is worth (P3.2 plan v3 D7, review round 1 P2-2).

    A receipt is a fact whoever gives it.  "I found nothing" only means "it never started"
    when the connector's lookup is authoritative; otherwise the action stays UNKNOWN and a
    person decides — the system never repeats a real-world action on a maybe.
    """

    if found is not None:
        return "COMPLETED"
    if connector is None or not getattr(connector, "supports_reconciliation", False):
        return "STILL_UNKNOWN"
    if getattr(connector, "lookup_authority", "best_effort") != "authoritative":
        return "STILL_UNKNOWN"
    return "CONFIRMED_NOT_STARTED"


def publication_overlaps_storage(
    publish_root: Path | str, storage_roots: Sequence[Path | str]
) -> bool:
    """Physical root overlap, including symlinks and case/Unicode filename aliases.

    ``resolve`` follows existing parent symlinks but does not canonicalize filename
    case on macOS. Compare whole normalized path segments, never string prefixes.
    The conservative alias rule also protects deployments moving between filesystems.
    """

    def parts(path: Path | str) -> tuple[str, ...]:
        return tuple(
            unicodedata.normalize("NFC", part).casefold() for part in Path(path).resolve().parts
        )

    publisher = parts(publish_root)
    for root in storage_roots:
        protected = parts(root)
        shared = min(len(publisher), len(protected))
        if publisher[:shared] == protected[:shared]:
            return True
    return False


class ActionExecutor:
    def __init__(
        self,
        commit: CommitService,
        connectors: Mapping[str, Any],
        deployment: DeploymentPolicy,
        *,
        owner: str,
        lease_seconds: float | None = None,
        source_storage_roots: Sequence[Path | str] | None = None,
        require_execution_root: Callable[[], None] | None = None,
    ) -> None:
        self._commit = commit
        self._connectors = dict(connectors)
        self._deployment = deployment
        self._owner = owner
        self._execution_root_check = require_execution_root
        # The assembly supplies the actual CAS/workspace roots. A custom/embedded
        # deployment must not silently guess their location from the SQLite filename.
        self._source_storage_roots = (
            None if source_storage_roots is None else tuple(source_storage_roots)
        )
        self._timeout = float(deployment.connector_timeout_seconds)
        # the lease outlives the call's timeout: a live hand-off is never reconciled early
        self._lease = float(lease_seconds) if lease_seconds is not None else 2 * self._timeout + 5.0
        self._inflight: set[str] = set()
        self.last_refusal: dict[str, str] = {}  # action_key -> why begin_handoff said no

    def _require_execution_root(self) -> None:
        gate = self._commit._assurance_root_gate
        if gate is not None:
            gate.require_execution()
        if self._execution_root_check is not None:
            self._execution_root_check()

    def _external_call(self, callback, *args, **kwargs):
        # Check in the actual worker thread, after the asynchronous handoff gap.
        self._require_execution_root()
        return callback(*args, **kwargs)

    async def _external_async(self, callback, *args, **kwargs):
        self._require_execution_root()
        return await callback(*args, **kwargs)

    def _start(self, callback: Any, *args: Any, **kwargs: Any) -> asyncio.Future[Any]:
        """开始一次连接器调用：普通函数进线程，``async def`` 在本事件循环上跑。"""

        if is_async(callback):
            return asyncio.ensure_future(self._external_async(callback, *args, **kwargs))
        return asyncio.ensure_future(
            asyncio.to_thread(self._external_call, callback, *args, **kwargs)
        )

    @property
    def inflight(self) -> frozenset[str]:
        """Hand-offs this process is waiting on right now (D7-5': the only in-flight kind)."""

        return frozenset(self._inflight)

    def ready(self, mission_id: str) -> list[dict[str, Any]]:
        """Actions that could be handed off now: APPROVED, or L0/L1 PROPOSED."""

        return self._commit.store.list_actions(mission_id, "APPROVED", "PROPOSED")

    def _source_publish_refusal(self, action: Mapping[str, Any]) -> str | None:
        """Run inside begin_handoff's transaction, before reservation/outbox writes.

        The protected storage is shared by the whole library, including other tenants,
        completed Missions and revoked historical sources. Do not cache a negative
        result: source/domain registration can happen after this executor was built.
        """
        if action["connector"] != "file_publish":
            return None
        # Every Mission's profile now carries ``sources/`` (general task with material,
        # 2026-09-26), so the overlap check always runs; it never decodes another
        # Mission's frozen domain (one this build cannot read would refuse every publish).
        root = getattr(self._connectors.get("file_publish"), "root", None)
        if not isinstance(root, (str, Path)) or not self._source_storage_roots:
            return "source_publish_root_unavailable"
        try:
            overlaps = publication_overlaps_storage(root, self._source_storage_roots)
        except (OSError, RuntimeError, TypeError, ValueError):
            return "source_publish_root_unavailable"
        return "source_publish_root_overlap" if overlaps else None

    async def hand_off(self, action_key: str, *, rehandoff: bool = False) -> dict[str, Any] | None:
        self._require_execution_root()
        action, _reason = self._commit.begin_handoff(
            action_key,
            owner=self._owner,
            lease_seconds=self._lease,
            connectors=self._connectors,
            deployment=self._deployment,
            rehandoff=rehandoff,
            deployment_guard=self._source_publish_refusal,
        )
        if action is None:
            self.last_refusal[action_key] = _reason or ""
            return None
        connector = self._connectors[str(action["connector"])]
        self._commit._store.fault("after_handoff_before_call", "action")  # 崩溃切点 K08
        self._inflight.add(action_key)
        call = self._start(
            connector.execute,
            str(action["operation"]),
            str(action["target"]),
            dict(action["params"]),
            idempotency_key=str(action["idempotency_key"]),
        )
        try:
            receipt = await asyncio.wait_for(asyncio.shield(call), timeout=self._timeout)
        except ConnectorRejected as error:  # the service said no: nothing was applied
            self._inflight.discard(action_key)
            return self._commit.record_action_outcome(
                action_key, owner=self._owner, outcome="failed", error=error_text(error)
            )
        except Exception as error:  # noqa: BLE001 - transport, timeout: it may have happened
            self._release_when_done(action_key, call)
            return self._commit.record_action_outcome(
                action_key, owner=self._owner, outcome="unknown", error=error_text(error)
            )
        self._inflight.discard(action_key)
        self._commit._store.fault("after_external_effect", "action")  # 崩溃切点 K09
        checked = checked_receipt(receipt)
        if checked is None:
            return self._commit.record_action_outcome(
                action_key, owner=self._owner, outcome="unknown", error="receipt_invalid"
            )
        return self._commit.record_action_outcome(
            action_key, owner=self._owner, outcome="succeeded", receipt=checked
        )

    def _release_when_done(self, action_key: str, call: asyncio.Future[Any]) -> None:
        """Review P2-4: a call that outlived its timeout is still in flight until its thread
        ends — this process never asks "did it start?" before then."""

        def finished(future: asyncio.Future[Any]) -> None:
            if not future.cancelled():
                future.exception()  # retrieved: the outcome is already booked as UNKNOWN
            self._inflight.discard(action_key)

        if call.done():
            finished(call)
        else:
            call.add_done_callback(finished)

    async def _rehandoff_scoped(self, key: str, runtime: Any) -> dict[str, Any]:
        action = self._commit.store.get_action(key) or {}
        if self._fenced(action):
            # 封口的连接器已经拒绝这个键：同键重交注定被拒。直接记"证实没生效"，
            # 由系统按已批准的效果换新键重做。
            reason = "lookup_fenced_key"
        else:
            again = await self.hand_off(key, rehandoff=True)
            if again is not None:
                return again
            reason = self.last_refusal.get(key, "current_gate_refused")
        return self._commit.finish_proven_nonapplication(
            key, reason=reason, service_authority=runtime.service_authority
        )

    def _fenced(self, action: Mapping[str, Any]) -> bool:
        connector = self._connectors.get(str(action.get("connector")))
        return bool(getattr(connector, "lookup_fenced", False))

    def _failed_unproven(self, action: Mapping[str, Any]) -> bool:
        """FAILED, handed off at least once, operation-linked, no stored proof, and not
        already waiting for a person."""

        return (
            action.get("state") == "FAILED"
            and not action.get("needs_human")
            and operation_reconciliation.action_outcome_unresolved(self._commit.store, action)
        )

    async def _lookup(
        self, connector: Any, action: Mapping[str, Any]
    ) -> tuple[str, Receipt | None, bool]:
        """按幂等键问一次连接器：（判定，合格回执，这次查询有没有得到可信的答案）。"""

        if connector is None or not getattr(connector, "supports_reconciliation", False):
            return "STILL_UNKNOWN", None, False
        try:
            found = await asyncio.wait_for(
                self._start(connector.lookup, str(action["idempotency_key"])),
                timeout=self._timeout,
            )
        except Exception:  # noqa: BLE001 - the service cannot answer: still unknown
            return "STILL_UNKNOWN", None, False
        receipt = checked_receipt(found)
        if receipt is None and found is not None:  # 交回了东西却不是合格回执：不能当"没有记录"
            return "STILL_UNKNOWN", None, False
        return lookup_verdict(connector, receipt), receipt, True

    async def reconcile_one(
        self, action_key: str, *, allow_rehandoff: bool = False
    ) -> dict[str, Any] | None:
        """Observe one exact Action; TaskGraph convergence never re-sends it."""
        self._require_execution_root()

        store = self._commit.store
        if store.connection.in_transaction:
            raise ActionCommitError("action reconciliation cannot run inside a Store transaction")
        action = store.get_action(action_key)
        if action is None:
            raise ActionCommitError("action reconciliation source is unavailable")
        failed = action["state"] == "FAILED"
        if action["state"] not in {"UNKNOWN", "HANDED_OFF"} and not (
            failed and self._failed_unproven(action)
        ):
            return action

        def require_current() -> None:
            current = store.get_action(action_key)
            fields = (
                "mission_id",
                "action_id",
                "version",
                "params_hash",
                "idempotency_key",
                "handoffs",
            )
            if current is None or any(current.get(field) != action.get(field) for field in fields):
                raise ActionCommitError("action changed during reconciliation")

        key = str(action["action_key"])
        if key in self._inflight:
            return None
        if (
            action["state"] == "HANDED_OFF"
            and float(action.get("lease_expires_at") or 0) > store.now
        ):
            return None  # a live hand-off may still be on its way
        runtime = getattr(self._commit, "_operation_materialization_runtime", None)
        if runtime is not None:
            if stored_negative_proof(self._commit.store, action):
                return await self._rehandoff_scoped(key, runtime) if allow_rehandoff else action
        connector = self._connectors.get(str(action["connector"]))
        if failed and not self._fenced(action):
            verdict, receipt, looked_up = "STILL_UNKNOWN", None, False
        else:
            # 封口证明要用执行器问到的答案（连接器的 lookup 可能是 async，适配器问不了）
            verdict, receipt, looked_up = await self._lookup(connector, action)
            if failed:  # a FAILED action's outcome is booked; only a proof is sought below
                verdict = "STILL_UNKNOWN"
        # New Operation links require a scoped, registered proof. Empty lookup,
        # timeout and an expired local lease cannot establish nonapplication.

        link = PlanningAdmissionStore(self._commit.store).get_operation_action_link_for_action(key)
        runtime = getattr(self._commit, "_operation_materialization_runtime", None)
        if link is not None and verdict != "COMPLETED":
            verdict = "STILL_UNKNOWN"
            if runtime is not None:
                try:
                    with self._commit.store.read_view():
                        resolved, _profile, adapter, events = context(
                            self._commit.store, runtime, action
                        )
                    # 通用连接器的封口证明用上面这次查询的答案，不再问第二遍
                    lookup = (
                        {"found": receipt, "looked_up": True}
                        if looked_up and getattr(adapter, "takes_lookup", False)
                        else {}
                    )
                    evidence = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._external_call,
                            adapter.observe,
                            action=action,
                            resolved=resolved,
                            handoff_events=events,
                            now_ms=int(self._commit.store.now * 1000),
                            **lookup,
                        ),
                        timeout=self._timeout,
                    )
                    with store.transaction():
                        require_current()
                        updated = self._commit.record_scoped_reconciliation(
                            key,
                            evidence=evidence,
                            adapter=adapter,
                            service_authority=runtime.service_authority,
                        )
                except Exception:  # unavailable/incomplete protocol evidence stays unresolved
                    logger.debug("reconcile_one_read_view_failed", exc_info=True)
                    if failed:
                        return self._commit.record_reconciliation_unavailable(key)
                    with store.transaction():
                        require_current()
                        updated = self._commit.record_reconciliation(key, verdict="STILL_UNKNOWN")
                else:
                    # A scoped proof does not authorize a send. The normal current
                    # authority/approval/budget/cap gates still execute below.
                    if (
                        allow_rehandoff
                        and updated["state"] == "UNKNOWN"
                        and updated.get("reconcile") == "CONFIRMED_NOT_STARTED"
                    ):
                        updated = await self._rehandoff_scoped(key, runtime)
                    return updated
        if failed:
            # No operation runtime here: nothing can be proven this round, and nothing moved
            # — not reported as settled, so an idle loop stays idle (核验 2026-10-03).
            return None
        with store.transaction():
            require_current()
            updated = self._commit.record_reconciliation(key, verdict=verdict, receipt=receipt)
        if (
            allow_rehandoff
            and updated["state"] == "UNKNOWN"
            and updated.get("reconcile") == "CONFIRMED_NOT_STARTED"
        ):
            again = await self.hand_off(key, rehandoff=True)
            updated = again or self._commit.store.get_action(key) or updated
        return updated


__all__ = (
    "ActionExecutor",
    "checked_receipt",
    "error_text",
    "lookup_verdict",
    "publication_overlaps_storage",
)
