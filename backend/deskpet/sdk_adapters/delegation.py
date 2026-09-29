"""Foreground delegation: the five public delegate Tools on real SDK child Runs.

NEXT-TG-1.0 §9 收口第 4 项（方案：plans/2026-09-27-desktop-next/委派工具接回-方案.md 第 2 版）。

A delegate call launches one DETACHED SDK child Run per delegated task through the
kernel's own ticket path (``issue_profile_launch_ticket`` → ``children.launch``).
The child is an ordinary ``react`` Run that answers one task:

* identity is derived from ``parent Run + Tool call + task index`` so a replayed call
  reattaches to the children it already started and never launches a second set;
* its Tool authority is frozen from the parent's: the parent's catalog ∩ the requested
  Tools, minus every delegate Tool (no recursion), minus confirm-only Tools (nobody could
  answer the prompt) and minus anything that is not read-only (first version: a child
  never writes the project);
* its provider binding is a fresh per-Run binding of the parent's frozen provider/model;
* its start snapshot is marked ``run_kind = delegated_child`` so restart recovery and the
  usage projection never mistake it for a chat Run;
* one watcher per child releases the child's authority and binding once it ends, and
  unfinished children are cancelled when the parent ends, the call is cancelled, or the
  wait times out.

The answer text is the child's last assistant message (the terminal result only carries
the state).  Long-running, accepted or effectful work still belongs to ``mission_start``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

logger = logging.getLogger(__name__)

DELEGATED_CHILD_RUN_KIND = "delegated_child"
DELEGATE_TOOL_NAMES = frozenset({"agent", "agent_parallel", "spawn_team", "spawn_subagents"})
JOIN_TOOL_NAME = "await_subagents"
DELEGATION_TOOL_NAMES = DELEGATE_TOOL_NAMES | {JOIN_TOOL_NAME}
#: 第一版子运行只开放纯查询类工具（明确白名单，不按效果类别推断）：清单里标为只读的
#: 还有会改主对话待办（todo_*）、会写文件（download_file / generate_image）、会登记产物、
#: 会弹选择器或长时间等待的工具，它们都不能交给子运行。
CHILD_TOOL_ALLOWLIST = frozenset({
    "web_search", "web_fetch", "web_crawl", "web_extract_article", "web_read_sitemap",
    "scrapling_fetch", "agent_reach_read", "gold_price_lookup",
    "file_read", "read_file", "file_grep", "grep", "file_glob", "glob", "list_directory",
    "doc_read", "image_ocr", "workspace_recall",
})
MAX_CHILDREN_PER_PARENT = 8
DEFAULT_WAIT_SECONDS = 900.0
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_MAX_PROMPT = 8000

CHILD_INSTRUCTIONS = (
    "你是主对话委派出来的子助手，只负责完成下面这一项任务。"
    "只用给你的工具；不能再委派，也不能写文件或改动项目。"
    "完成后用一段简洁的文字直接给出结果（结论、要点、必要的出处），不要寒暄。"
)


class DelegationRefused(ValueError):
    """A delegate call that must be refused with a stable code (nothing launched)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DelegatedTask:
    index: int
    task_id: str
    prompt: str
    tools: tuple[str, ...] | None


def delegated_tasks(name: str, arguments: Mapping[str, Any]) -> tuple[DelegatedTask, ...]:
    """Split one delegate call into its tasks (one child Run each)."""

    if name == "agent":
        raw = [{"prompt": arguments.get("prompt"), "tools": arguments.get("tools"),
                "task_id": arguments.get("description")}]
    elif name == "spawn_team":
        descriptions = arguments.get("task_descriptions")
        if not isinstance(descriptions, list):
            raise DelegationRefused("delegation_invalid_arguments", "task_descriptions must be a list")
        raw = [{"prompt": item} for item in descriptions]
    elif name in {"agent_parallel", "spawn_subagents"}:
        raw = arguments.get("subagents")
        if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
            raise DelegationRefused("delegation_invalid_arguments", f"{name} requires subagents")
    else:
        raise DelegationRefused("delegation_invalid_arguments", f"{name} is not a delegate Tool")
    if not raw:
        raise DelegationRefused("delegation_invalid_arguments", "at least one delegated task is required")
    if len(raw) > MAX_CHILDREN_PER_PARENT:
        raise DelegationRefused(
            "delegation_too_many_tasks",
            f"at most {MAX_CHILDREN_PER_PARENT} delegated tasks per call",
        )
    tasks = []
    for index, item in enumerate(raw):
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            raise DelegationRefused("delegation_invalid_arguments", "each delegated task needs a prompt")
        if len(prompt) > _MAX_PROMPT:
            raise DelegationRefused(
                "delegation_prompt_too_large", f"a delegated prompt exceeds {_MAX_PROMPT} characters"
            )
        requested = item.get("tools")
        if requested is not None and not (
            isinstance(requested, list) and all(isinstance(tool, str) for tool in requested)
        ):
            raise DelegationRefused("delegation_invalid_arguments", "tools must be a list of names")
        tasks.append(DelegatedTask(
            index=index,
            task_id=str(item.get("task_id") or f"task_{index}"),
            prompt=prompt,
            tools=None if not requested else tuple(dict.fromkeys(requested)),
        ))
    return tuple(tasks)


def delegated_child_run_id(parent_run_id: str, call_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{parent_run_id}\0{call_id}\0{index}".encode()).hexdigest()
    return f"delegate-{digest[:32]}"


def child_tool_names(authority: Any, requested: Sequence[str] | None) -> tuple[str, ...]:
    """The child's Tools: parent ∩ requested, read-only, never delegate/confirm-only."""

    eligible = {
        name
        for name, spec in authority.specs.items()
        if name in CHILD_TOOL_ALLOWLIST
        and not spec.confirm_only
        and spec.effect_class == "read_only"
        and authority.dispatch_kinds.get(name) not in {"staged", "control"}
    }
    if requested is None:
        return tuple(sorted(eligible))
    unknown = sorted(set(requested) - eligible)
    if unknown:
        raise DelegationRefused(
            "delegation_tool_not_available",
            "a delegated child can only use this conversation's lookup Tools "
            "(web search/fetch, file read/search, document read, OCR); "
            f"not available: {', '.join(unknown)}",
        )
    return tuple(sorted(set(requested)))


def is_delegated_child_start(start: object) -> bool:
    start_input = start.get("input") if isinstance(start, Mapping) else None
    metadata = start_input.get("context_metadata") if isinstance(start_input, Mapping) else None
    return isinstance(metadata, Mapping) and metadata.get("run_kind") == DELEGATED_CHILD_RUN_KIND


def _waiting_delegate_parent_ids(uow: Any) -> tuple[str, ...]:
    names = sorted(DELEGATION_TOOL_NAMES)
    rows = uow.database.connection.execute(
        "SELECT DISTINCT effects.run_id FROM execution_effects AS effects "
        "JOIN runs ON runs.run_id = effects.run_id "
        "WHERE effects.state = 'unknown' AND runs.state = 'waiting' "
        f"AND effects.tool_name IN ({','.join('?' * len(names))})",
        names,
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def waiting_runs_blocked_on_delegation(uow: Any, *, exclude: frozenset[str] | set[str] = frozenset()) -> tuple[Any, ...]:
    """WAITING Runs held by an UNKNOWN delegate call (same idea as the Provider F06 list).

    The SDK's recoverable lists omit WAITING Runs; this one is woken once its delegate
    call is reconciled, so its per-Run binding and Tool authority must be restored first.
    """

    found = []
    for run_id in _waiting_delegate_parent_ids(uow):
        if run_id in exclude:
            continue
        record = uow.read_run(run_id)
        if record is not None:
            found.append(record)
    return tuple(found)


def _state(record: Any) -> str:
    state = getattr(record, "state", "")
    return str(getattr(state, "value", state)).lower()


def last_assistant_text(context_port: Any, run_id: str) -> str | None:
    from simple_harness import RunId

    if context_port is None:
        return None
    try:
        context = context_port.load(RunId(run_id))
    except Exception:  # noqa: BLE001 - an absent context is "no answer", not a crash
        return None
    texts = [
        message.content
        for message in context.messages
        if str(getattr(message.role, "value", message.role)) == "assistant"
        and isinstance(message.content, str)
        and message.content.strip()
    ]
    return texts[-1] if texts else None


@dataclass
class _Child:
    run_id: str
    parent_run_id: str
    task_id: str
    watcher: asyncio.Task[None] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class ProductDelegationService:
    """Launch, join, cancel and reconcile delegated child Runs of foreground Runs."""

    def __init__(
        self,
        *,
        uow_getter: Callable[[], Any],
        runtime_getter: Callable[[], Any],
        tool_authorities: Any,
        binding_resolver: Any,
        context_port_getter: Callable[[], Any],
        on_parent_woken: Callable[[str], object] | None = None,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        stall_seconds: float = 120.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._uow_getter = uow_getter
        self._runtime_getter = runtime_getter
        self._authorities = tool_authorities
        self._bindings = binding_resolver
        self._context_port_getter = context_port_getter
        self._on_parent_woken = on_parent_woken
        self._wait_seconds = float(wait_seconds)
        self._stall_seconds = float(stall_seconds)
        self._clock = clock
        self._children: dict[str, dict[str, _Child]] = {}
        self._parent_of: dict[str, str] = {}
        self._launch_lock = asyncio.Lock()
        self._background: set[asyncio.Task[None]] = set()
        add_listener = getattr(tool_authorities, "add_terminal_listener", None)
        if callable(add_listener):
            add_listener(self._on_authority_terminal)

    # ---- public Tool entry points -------------------------------------------------

    async def delegate(self, name: str, arguments: Mapping[str, Any], *, execution_context: Any) -> dict[str, Any]:
        parent_run_id = str(getattr(execution_context, "run_id", "") or "")
        call_id = str(getattr(execution_context, "call_id", "") or "")
        if not parent_run_id or not call_id:
            raise RuntimeError("delegation requires the SDK Run and call identity")
        try:
            tasks = delegated_tasks(name, arguments)
            launched = []
            async with self._launch_lock:
                known = set(self._children.get(parent_run_id, {}))
                fresh = [
                    task for task in tasks
                    if delegated_child_run_id(parent_run_id, call_id, task.index) not in known
                ]
                if len(known) + len(fresh) > MAX_CHILDREN_PER_PARENT:
                    raise DelegationRefused(
                        "delegation_child_limit",
                        f"this conversation turn already has {len(known)} delegated children "
                        f"(limit {MAX_CHILDREN_PER_PARENT}); collect them with await_subagents "
                        "or do the rest yourself",
                    )
                # 先把整批的工具请求都核对完，全部通过才启动：不留"报拒绝、其实启动了一半"。
                parent_authority = self._authorities.resolve(parent_run_id)
                for task in tasks:
                    child_tool_names(parent_authority, task.tools)
                for task in tasks:
                    launched.append(await self._launch(parent_run_id, call_id, task))
        except DelegationRefused as refused:
            return {"ok": False, "error": refused.code, "error_code": refused.code,
                    "public_message": refused.message, "retriable": False}
        if name == "spawn_subagents":
            return {
                "ok": True,
                "run_ids": [child.run_id for child in launched],
                "tasks": [{"task_id": child.task_id, "run_id": child.run_id} for child in launched],
                "next_action": "Call await_subagents to collect their results.",
            }
        results = await self._join(launched, deadline=self._clock() + self._wait_seconds, cancel_on_exit=True)
        return {"ok": True, "results": results}

    async def await_subagents(self, *, arguments: Mapping[str, Any], context: Any, operation_key: str = "") -> dict[str, Any]:
        parent_run_id = str(getattr(context, "run_id", "") or "")
        owned = self._children.get(parent_run_id, {})
        requested = [str(item) for item in (arguments.get("run_ids") or ()) if str(item)]
        uow = self._uow_getter()
        for run_id in requested:
            if run_id in owned:
                continue
            record = uow.read_run(run_id) if uow is not None else None
            if record is None or record.parent_run_id != parent_run_id:
                return {"ok": False, "error": "delegation_child_not_owned",
                        "error_code": "delegation_child_not_owned",
                        "public_message": f"{run_id} is not a delegated child of this conversation turn",
                        "retriable": False}
            self._adopt(run_id, parent_run_id, task_id=run_id)
            owned = self._children.get(parent_run_id, {})
        children = [owned[run_id] for run_id in requested] if requested else list(owned.values())
        if not children:
            return {"ok": True, "results": [], "public_message": "no delegated children to collect"}
        return {"ok": True, "results": await self._join(
            children, deadline=self._clock() + self._wait_seconds, cancel_on_exit=False
        )}

    # ---- restart / reconciliation -------------------------------------------------

    def adopt_recovered(self, run_id: str, parent_run_id: str) -> None:
        """A recoverable child found at startup: watch it again (no relaunch)."""

        self._adopt(run_id, parent_run_id, task_id=run_id)

    async def observe(self, effect: Any) -> Any | None:
        """Settle an UNKNOWN delegate/join call from its children (``None`` = not ours).

        Never answers CONFIRMED_NOT_STARTED: re-running a call after a restart re-mints
        its task grant, and the Host authorization record then rejects the same call
        under a new identity (a general Host gap, recorded separately).  Instead:

        * children still running → STILL_UNKNOWN; their watchers settle the parent with
          COMPLETED as soon as they end (``settle_waiting_parent``);
        * children all ended → COMPLETED with their answers;
        * the call was interrupted before its children existed → COMPLETED with a
          *failed* result that tells the model to call again (a new call, new children).
        """

        from simple_harness import thaw_json
        from simple_harness.tools import ToolResult
        from simple_harness.tools.reconciliation import (
            ReconciliationObservation,
            ReconciliationState,
        )

        name = str(getattr(effect, "tool_name", "") or "")
        if name not in DELEGATION_TOOL_NAMES:
            return None
        parent_run_id = str(getattr(getattr(effect, "run_id", ""), "value", getattr(effect, "run_id", "")))
        call_id = str(getattr(getattr(effect, "call_id", ""), "value", getattr(effect, "call_id", "")))
        arguments = thaw_json(effect.arguments)
        arguments = arguments if isinstance(arguments, Mapping) else {}
        uow = self._uow_getter()
        if name == JOIN_TOOL_NAME:
            requested = [str(item) for item in (arguments.get("run_ids") or ()) if str(item)]
            ids = requested or list(self._durable_children(parent_run_id))
            pairs = [(run_id, run_id, uow.read_run(run_id)) for run_id in ids]
            pairs = [(task_id, run_id, record) for task_id, run_id, record in pairs
                     if record is not None and record.parent_run_id == parent_run_id]
        else:
            try:
                tasks = delegated_tasks(name, arguments)
            except DelegationRefused:
                tasks = ()
            pairs = []
            for task in tasks:
                run_id = delegated_child_run_id(parent_run_id, call_id, task.index)
                pairs.append((task.task_id, run_id, uow.read_run(run_id)))
            if not pairs or any(record is None for _, _, record in pairs):
                started = [run_id for _, run_id, record in pairs if record is not None]
                return ReconciliationObservation(
                    ReconciliationState.COMPLETED, f"delegation:{call_id}:interrupted",
                    ToolResult.failed(
                        effect.call_id, "delegation_interrupted",
                        "The delegation was interrupted before its children started (the app "
                        "restarted). Call the delegate tool again if the work is still needed."
                        + (f" Already started: {', '.join(started)} (collect with await_subagents)."
                           if started else ""),
                    ),
                )
            if name == "spawn_subagents":
                value = {"ok": True, "run_ids": [run_id for _, run_id, _ in pairs],
                         "tasks": [{"task_id": task_id, "run_id": run_id} for task_id, run_id, _ in pairs],
                         "next_action": "Call await_subagents to collect their results."}
                return ReconciliationObservation(
                    ReconciliationState.COMPLETED, f"delegation:{call_id}:launched",
                    ToolResult.succeeded(effect.call_id, value),
                )
        running = [(task_id, run_id) for task_id, run_id, record in pairs if _state(record) not in _TERMINAL]
        if running:
            for task_id, run_id in running:
                self._adopt(run_id, parent_run_id, task_id=task_id)
            return ReconciliationObservation(ReconciliationState.STILL_UNKNOWN, f"delegation:{call_id}:running")
        results = [self._result(run_id, task_id) for task_id, run_id, _ in pairs]
        return ReconciliationObservation(
            ReconciliationState.COMPLETED, f"delegation:{call_id}:settled",
            ToolResult.succeeded(effect.call_id, {"ok": True, "results": results}),
        )

    def _durable_children(self, parent_run_id: str) -> tuple[str, ...]:
        uow = self._uow_getter()
        rows = uow.database.connection.execute(
            "SELECT run_id FROM runs WHERE parent_run_id = ? AND run_id LIKE 'delegate-%' "
            "ORDER BY created_at, run_id",
            (parent_run_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    async def settle_waiting_parent(self, parent_run_id: str) -> bool:
        """Resolve a WAITING parent's UNKNOWN delegate calls and let the kernel wake it.

        The SDK blocks a Run whose Tool outcome is UNKNOWN until a reconciliation
        resolution is recorded; the Host reconciles only Provider calls on its own, so
        without this a parent restarted mid-``agent`` would wait forever.  Only
        COMPLETED observations are recorded (see ``observe``).
        """

        from simple_harness.execution.recovery import ResolutionOutcome
        from simple_harness.tools.reconciliation import ReconciliationState

        try:
            uow = self._uow_getter()
            parent = uow.read_run(parent_run_id) if uow is not None else None
            if parent is None or _state(parent) != "waiting":
                return False
            settled = False
            for effect in uow.list_unknown_effects_for_run(parent_run_id):
                if effect.tool_name not in DELEGATION_TOOL_NAMES:
                    continue
                observed = await self.observe(effect)
                if observed is None or observed.state is not ReconciliationState.COMPLETED:
                    continue
                uow.record_tool_reconciliation(
                    effect,
                    outcome=ResolutionOutcome.COMPLETED,
                    result=observed.result,
                    evidence_ref=observed.evidence_ref,
                    now=self._clock(),
                )
                settled = True
            if settled:
                runtime = self._runtime_getter()
                if runtime is not None:
                    await runtime.reconcile()
                if self._on_parent_woken is not None:
                    # 主对话的恢复展示在父运行转入等待时就退出了；唤醒后要重新挂上，
                    # 结论才能写回会话。
                    self._on_parent_woken(parent_run_id)
                logger.info("delegation_parent_settled parent=%s", parent_run_id)
            return settled
        except Exception:  # noqa: BLE001 - retried on the next child end / startup
            logger.warning("delegation_parent_settle_failed parent=%s", parent_run_id, exc_info=True)
            return False

    def _schedule_settle(self, parent_run_id: str) -> None:
        task = asyncio.get_running_loop().create_task(
            self._settle_when_parked(parent_run_id), name=f"delegation-settle:{parent_run_id}")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _settle_when_parked(self, parent_run_id: str, *, attempts: int = 60,
                                  interval: float = 1.0) -> None:
        """After a child ends: settle its parent once the parent is parked on the call.

        A parent re-driven after a restart observes its call as still running and only
        then moves to WAITING; a child ending inside that window must not be missed.
        In the normal in-process path the parent's own call is live (not UNKNOWN) and
        this returns at once.
        """

        for _ in range(attempts):
            try:
                uow = self._uow_getter()
                parent = uow.read_run(parent_run_id) if uow is not None else None
                if parent is None or _state(parent) in _TERMINAL:
                    return
                held = [effect for effect in uow.list_unknown_effects_for_run(parent_run_id)
                        if effect.tool_name in DELEGATION_TOOL_NAMES]
                if not held and _state(parent) != "waiting":
                    return
                if _state(parent) == "waiting" and await self.settle_waiting_parent(parent_run_id):
                    return
                if not held:
                    return
            except Exception:  # noqa: BLE001 - keep trying within the bound
                logger.warning("delegation_settle_retry parent=%s", parent_run_id, exc_info=True)
            await asyncio.sleep(interval)

    async def settle_after_startup(self, *, attempts: int = 120, interval: float = 1.0) -> int:
        """Once the runtime is ready, wake every WAITING parent held by a delegate call."""

        for _ in range(attempts):
            try:
                if self._runtime_getter() is not None and self._uow_getter() is not None:
                    break
            except Exception:  # noqa: BLE001 - runtime not published yet
                pass
            await asyncio.sleep(interval)
        else:
            return 0
        count = 0
        for parent_run_id in _waiting_delegate_parent_ids(self._uow_getter()):
            count += int(await self.settle_waiting_parent(parent_run_id))
        return count

    def start_recovery(self) -> None:
        task = asyncio.get_running_loop().create_task(
            self.settle_after_startup(), name="delegation-startup-settle")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def cancel_children(self, parent_run_id: str) -> None:
        for child in list(self._children.get(parent_run_id, {}).values()):
            await self._cancel(child.run_id)

    # ---- internals ----------------------------------------------------------------

    async def _launch(self, parent_run_id: str, call_id: str, task: DelegatedTask) -> _Child:
        from simple_harness import Message, MessageRole, freeze_json
        from simple_harness.execution.contracts.children import (
            AttachmentPolicy,
            ProfileLaunchTicket,
            child_launch_fingerprint,
        )
        from simple_harness.runtime.child_runs import ChildLaunchRequest, ProfileLaunchTicketRef
        from simple_harness.runtime.start_snapshot import StartSnapshot

        child_run_id = delegated_child_run_id(parent_run_id, call_id, task.index)
        uow = self._uow_getter()
        runtime = self._runtime_getter()
        if uow is None or runtime is None:
            raise RuntimeError("delegation runtime is unavailable")
        existing = uow.read_run(child_run_id)
        if existing is not None:
            if existing.parent_run_id != parent_run_id:
                raise RuntimeError("delegated child identity belongs to another parent")
            return self._adopt(child_run_id, parent_run_id, task_id=task.task_id)

        parent_authority = self._authorities.resolve(parent_run_id)
        parent_binding = self._bindings.registry.resolve(parent_run_id)
        parent_start = uow.read_start_snapshot(parent_run_id)
        if parent_binding is None or not isinstance(parent_start, Mapping):
            raise RuntimeError("delegation parent binding is unavailable")
        parent_metadata = (parent_start.get("input") or {}).get("context_metadata") or {}
        if parent_metadata.get("run_kind") == DELEGATED_CHILD_RUN_KIND:
            raise DelegationRefused("delegation_not_permitted", "a delegated child cannot delegate")
        tools = child_tool_names(parent_authority, task.tools)
        if not tools:
            raise DelegationRefused(
                "delegation_tool_not_available",
                "this conversation has no read-only Tools a delegated child could use",
            )

        request_id = f"{child_run_id}:request"
        binding = self._bindings.create_binding(
            run_id=child_run_id,
            session_id=parent_binding.session_id,
            request_id=request_id,
            snapshot_id=parent_binding.snapshot_id,
            provider_id=parent_binding.provider_id,
            provider_incarnation_id=parent_binding.provider_incarnation_id,
            provider_config_revision=parent_binding.provider_config_revision,
            binding_epoch=parent_binding.binding_epoch,
            model_id=parent_binding.model_id,
            model_params=parent_binding.model_params,
            context_window=parent_binding.context_window,
            catalog_generation=parent_binding.catalog_generation,
            catalog_fingerprint=parent_binding.catalog_fingerprint,
        )
        try:
            authority = self._authorities.prepare_run(
                run_id=child_run_id,
                session_id=parent_authority.session_id,
                request_id=request_id,
                root_run_id=parent_authority.root_run_id,
                task_scope_id=parent_authority.task_work_context.task_scope_id,
                workspace_root=parent_authority.task_work_context.workspace_root,
                catalog=_child_catalog(parent_authority, tools),
                inventory=_child_inventory(parent_authority, tools),
                principal_id=parent_authority.principal_id,
                binding_version=int(parent_authority.workspace_resolution.get(
                    "binding_version", parent_authority.task_work_context.binding_version)),
                workspace_resolution=parent_authority.workspace_resolution,
            )
        except BaseException:
            self._bindings.mark_terminal(child_run_id, "failed")
            raise
        payload = {
            "input": {"text": task.prompt},
            "messages": [
                Message(MessageRole.SYSTEM, CHILD_INSTRUCTIONS).to_dict(),
                Message(MessageRole.USER, task.prompt).to_dict(),
            ],
            "capability_snapshot": {"tools": list(tools)},
            "context_metadata": {
                "run_kind": DELEGATED_CHILD_RUN_KIND,
                "parent_run_id": parent_run_id,
                "delegation_call_id": call_id,
                "task_id": task.task_id,
                "session_id": parent_authority.session_id,
                "root_run_id": parent_authority.root_run_id,
                "request_id": request_id,
                "task_scope_id": parent_authority.task_work_context.task_scope_id,
                "workspace_root": authority.task_work_context.workspace_root,
                "workspace_binding_version": authority.task_work_context.binding_version,
                "snapshot_id": binding.snapshot_id,
                "binding_epoch": binding.binding_epoch,
                "context_window": binding.context_window,
                # 0 = 用量投影只记这次调用与费用，不写主会话的上下文用量样本
                # （``ProviderProjectionEnvelopeV1.has_trusted_usage`` 要求 > 0）。
                "effective_ceiling": 0,
                "run_binding": binding.to_record(),
                "tool_authority": authority.run_start_record(),
            },
        }
        parent_snapshot = StartSnapshot.from_json(parent_start)
        snapshot = replace(
            parent_snapshot,
            turn_id=f"{child_run_id}:turn",
            input=freeze_json(payload),
            provider_budget_fingerprint=binding.budget_fingerprint,
            conversation=None,
            context_preparation_mode=None,
            context_stage_id=None,
            context_stage_hash=None,
            prepared_context=None,
            start_mode="ordinary",
            host_control_authority=None,
            host_control_user_id=None,
            initial_route_receipt=None,
            initial_route_receipt_hash=None,
        ).to_json()
        launch = {
            "profile_key": parent_snapshot.profile_key,
            "driver_kind": parent_snapshot.driver_kind,
            "catalog_generation": parent_snapshot.tool_catalog_generation,
            "delegation_call_id": call_id,
            "child_run_id": child_run_id,
        }
        ticket_id = f"{child_run_id}:ticket"
        child = self._adopt(child_run_id, parent_run_id, task_id=task.task_id, watch=False)
        try:
            uow.issue_profile_launch_ticket(
                ProfileLaunchTicket(
                    ticket_id, parent_run_id, parent_snapshot.profile_key,
                    parent_snapshot.tool_catalog_generation, child_launch_fingerprint(launch),
                ),
                now=self._clock(),
            )
            await runtime.children.launch(ChildLaunchRequest(
                ProfileLaunchTicketRef(ticket_id, parent_snapshot.tool_catalog_generation),
                f"{child_run_id}:launch",
                child_run_id,
                request_id,
                AttachmentPolicy.DETACHED,
                launch,
                snapshot,
            ))
        except BaseException:
            if uow.read_run(child_run_id) is None:
                self._forget(child)
                self._release(child_run_id, "failed")
            raise
        child.watcher = asyncio.create_task(self._watch(child), name=f"delegation-watch:{child_run_id}")
        logger.info("delegation_child_launched child=%s parent=%s tools=%d", child_run_id, parent_run_id, len(tools))
        return child

    def _adopt(self, run_id: str, parent_run_id: str, *, task_id: str, watch: bool = True) -> _Child:
        owned = self._children.setdefault(parent_run_id, {})
        child = owned.get(run_id)
        if child is None:
            child = _Child(run_id=run_id, parent_run_id=parent_run_id, task_id=task_id)
            owned[run_id] = child
            self._parent_of[run_id] = parent_run_id
        if watch and child.watcher is None:
            child.watcher = asyncio.get_running_loop().create_task(
                self._watch(child), name=f"delegation-watch:{run_id}"
            )
        return child

    def _forget(self, child: _Child) -> None:
        self._children.get(child.parent_run_id, {}).pop(child.run_id, None)
        self._parent_of.pop(child.run_id, None)

    async def _watch(self, child: _Child) -> None:
        """Only a terminal (or vanished) child ends the watch; read errors retry.

        A child parked in WAITING (e.g. its model call came back UNKNOWN after a
        timeout or a restart) has no chat-side fallback: the watcher asks the kernel to
        reconcile once after ``stall_seconds``, and cancels the child after another
        ``stall_seconds`` so its parent always gets an answer.
        """

        interval = 0.2
        waiting_since: float | None = None
        nudged = False
        while True:
            try:
                uow = self._uow_getter()
                record = uow.read_run(child.run_id) if uow is not None else None
            except Exception:  # noqa: BLE001 - e.g. a briefly locked ledger: read again
                logger.warning("delegation_watch_read_failed child=%s", child.run_id, exc_info=True)
            else:
                if record is None or _state(record) in _TERMINAL:
                    self._release(child.run_id, _state(record) if record is not None else "failed")
                    child.done.set()
                    self._schedule_settle(child.parent_run_id)
                    return
                if _state(record) != "waiting":
                    waiting_since, nudged = None, False
                else:
                    now = self._clock()
                    waiting_since = now if waiting_since is None else waiting_since
                    if now - waiting_since >= self._stall_seconds and not nudged:
                        nudged = True
                        waiting_since = now
                        logger.warning("delegation_child_stalled_reconcile child=%s", child.run_id)
                        try:
                            runtime = self._runtime_getter()
                            if runtime is not None:
                                await runtime.reconcile()
                        except Exception:  # noqa: BLE001
                            logger.warning("delegation_child_reconcile_failed child=%s", child.run_id,
                                           exc_info=True)
                    elif now - waiting_since >= self._stall_seconds and nudged:
                        logger.warning("delegation_child_stalled_cancel child=%s", child.run_id)
                        await self._cancel(child.run_id)
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 2.0)

    def _release(self, run_id: str, state: str) -> None:
        terminal = state if state in _TERMINAL else "failed"
        for release in (self._authorities.mark_terminal, self._bindings.mark_terminal):
            try:
                release(run_id, terminal)
            except KeyError:
                pass
            except Exception:  # noqa: BLE001 - cleanup must reach both registries
                logger.warning("delegation_child_release_failed child=%s", run_id, exc_info=True)

    async def _join(self, children: Sequence[_Child] | Mapping[str, _Child], *, deadline: float,
                    cancel_on_exit: bool) -> list[dict[str, Any]]:
        items = list(children.values()) if isinstance(children, Mapping) else list(children)
        finished = False
        interrupted = False
        try:
            remaining = max(0.0, deadline - self._clock())
            waits = [asyncio.ensure_future(child.done.wait()) for child in items]
            try:
                await asyncio.wait_for(asyncio.gather(*waits), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                for pending in waits:
                    pending.cancel()
            finished = all(child.done.is_set() for child in items)
            return [self._result(child.run_id, child.task_id) for child in items]
        except asyncio.CancelledError:
            interrupted = True
            raise
        finally:
            # 超时：取消没结束的子运行。调用被中断：只有父运行确实被取消/结束才取消；
            # 关闭应用造成的中断不取消，子运行重启后接着跑，由重放接上。
            if cancel_on_exit and not finished and items and (
                not interrupted or self._parent_stopping(items[0].parent_run_id)
            ):
                for child in items:
                    if not child.done.is_set():
                        await self._cancel(child.run_id)

    def _result(self, run_id: str, task_id: str) -> dict[str, Any]:
        uow = self._uow_getter()
        record = uow.read_run(run_id) if uow is not None else None
        state = _state(record) if record is not None else "missing"
        value = last_assistant_text(self._context_port_getter(), run_id) if state == "completed" else None
        error = None
        if state not in _TERMINAL:
            error = "the delegated child has not finished yet; it was stopped or can be collected later"
        elif state != "completed":
            error = f"the delegated child ended {state}"
        elif value is None:
            error = "the delegated child finished without an answer"
        return {"task_id": task_id, "run_id": run_id, "status": state, "value": value, "error": error}

    async def _cancel(self, run_id: str) -> None:
        try:
            from simple_harness import RunId

            runtime = self._runtime_getter()
            uow = self._uow_getter()
            record = uow.read_run(run_id) if uow is not None else None
            if runtime is None or record is None or _state(record) in _TERMINAL:
                return
            await runtime.client.cancel(RunId(run_id))
        except Exception:  # noqa: BLE001 - a racing terminal / closing runtime is fine
            logger.warning("delegation_child_cancel_failed child=%s", run_id, exc_info=True)

    def _parent_stopping(self, parent_run_id: str) -> bool:
        try:
            uow = self._uow_getter()
            record = uow.read_run(parent_run_id) if uow is not None else None
        except Exception:  # noqa: BLE001
            return False
        return record is not None and _state(record) in _TERMINAL | {"cancel_requested"}

    def _on_authority_terminal(self, record: Any) -> None:
        run_id = str(getattr(record, "run_id", "") or "")
        owned = self._children.get(run_id)
        if not owned:
            return
        unfinished = [child for child in owned.values() if not child.done.is_set()]
        if unfinished and not self._parent_stopping(run_id):
            # 关闭应用时 Host 也会释放父运行授权，但父运行并没有结束：子运行留着，重启后接着跑。
            return
        if not unfinished:
            for child in self._children.pop(run_id, {}).values():
                self._parent_of.pop(child.run_id, None)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("delegation_parent_ended_off_loop parent=%s", run_id)
            return
        task = loop.create_task(self.cancel_children(run_id), name=f"delegation-cancel:{run_id}")
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def _child_catalog(parent: Any, tools: Sequence[str]) -> dict[str, Any]:
    specs = []
    for name in tools:
        schema = parent.specs[name].schema
        specs.append({
            "name": name,
            "description": schema["description"],
            "input_schema": json.loads(json.dumps(schema["parameters"])),
        })
    return {
        "generation": parent.catalog_generation,
        "content_fingerprint": parent.catalog_fingerprint,
        "specs": specs,
        "schema_fingerprints": {name: parent.specs[name].schema_hash for name in tools},
    }


def _child_inventory(parent: Any, tools: Sequence[str]) -> list[dict[str, Any]]:
    by_name = {item["name"]: item for item in parent.run_start_record()["inventory"]}
    return [dict(by_name[name]) for name in tools]


__all__ = (
    "CHILD_INSTRUCTIONS",
    "DELEGATED_CHILD_RUN_KIND",
    "DELEGATE_TOOL_NAMES",
    "DELEGATION_TOOL_NAMES",
    "DelegationRefused",
    "MAX_CHILDREN_PER_PARENT",
    "ProductDelegationService",
    "child_tool_names",
    "delegated_child_run_id",
    "delegated_tasks",
    "is_delegated_child_start",
    "last_assistant_text",
    "waiting_runs_blocked_on_delegation",
)
