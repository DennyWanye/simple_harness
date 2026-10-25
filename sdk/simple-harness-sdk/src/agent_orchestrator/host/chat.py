# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""快速聊天：SDK 的轻量运行档（AgentOS 迁移第 4 块，2026-10-21）。

同一个引擎——执行池里的 ReAct 循环、工具网关、预算账本——不进任务编排：没有规划器、没有审阅员，
一句话一个回合。聊天池是一个单独的执行池（``owner_mode="STANDALONE_CHAT"``，自己的执行库与
工作区，放在数据目录 ``chat/`` 下）：任务池只建"由调度意图创建"的 Agent，每次模型调用都要对上
一张调度意图；聊天的 Agent 由宿主当事人直接创建，没有意图可对。

- 一个 ``session_id`` 一个 Agent：历史在 SDK 执行库里，第二句能看到第一句。
- 工具只有应用注册的只读工具（``DomainTool``）；名单在会话第一句定下。有副作用的事不在聊天里做。
- 每回合先在全局月账上预留（模型调用上限 × (窗口 + 输出上限)），回合结束按模型报告的真实用量结清；
  报告缺失的调用按预留上限结清（可以多算不可以少算）。
- ``require_tool``：模型首轮没调工具就提醒一次；仍不调如实返回 ``tool_not_used``。
- 台账 ``chat-turns.jsonl``（回合、用量、模型调用数、耗时）；同一幂等键原样返回，不再调模型。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from simple_harness.agents import AgentConfig, AgentLimits
from simple_harness.agents.context.budget import ContextPolicy
from simple_harness.agents.contracts import AgentTurnState
from simple_harness.agents.runtime import agent_id_for
from simple_harness.contracts import thaw_json

from ..contracts import Budget
from ..deployment.native_pools import NATIVE_OUTPUT_TOKENS, calibrated, native_profile_id
from ..governance.budgets import BudgetExhausted, UsageFact
from ..orchestrator.budget_accounts import global_account_id
from ..runtime.assembly import (
    OWNER_SCOPE,
    assemble_orchestrator_runtime,
    resolve_profile_context_policy,
)
from ..runtime.model_router import RuntimeProfile
from ..runtime.provider_budget_guard import valid_usage
from ..runtime.tool_gateway import WorkspaceBinding

logger = logging.getLogger(__name__)

LEDGER_NAME = "chat-turns.jsonl"
CHAT_INSTRUCTIONS = (
    "[role:chat]\n"
    "你是应用里的对话助手，直接回答用户。\n"
    "能用工具查到的事实先查再答；查不到就说查不到，不编造。\n"
    "你只有只读工具。要改、删、发送、购买、移动文件、点击界面这类有副作用的事，你不能做："
    "告诉用户这需要建一个任务，并用一句话写清任务目标。\n"
    "用用户的语言回答，简短、具体。"
)
NUDGE_TEXT = "请先用工具查，再根据查到的结果回答。"
_POLL_SECONDS = 0.05
_POLL_MAX_SECONDS = 0.5
_CANCEL_GRACE_SECONDS = 30.0
_MAX_GENERATIONS = 1000  # 一个会话最多放弃多少个 Agent（每次宿主在回合中途被关才会多一个）


class ChatRequestError(RuntimeError):
    """一个被拒绝的聊天请求；``code`` 稳定，供上层按码处理。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _text(body: Mapping[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ChatRequestError("invalid_request", f"{name} 必须是非空字符串")
    return value


def _usage_of(record: Any) -> tuple[int, int] | None:
    """一次调用的可用用量；没报告、报 0（有的线路每次都回 0/0）、字段不合规都算"未知"，
    与任务池同一口径（可以多算不可以少算）。"""

    if str(record.state) not in {"succeeded", "failed"}:
        return None
    try:
        return valid_usage(record)
    except Exception:  # noqa: BLE001 - 字段不合规：不当零
        return None


class ChatService:
    """宿主的聊天入口：自己的执行池 + 宿主的工具、预算与当事人。"""

    instructions = CHAT_INSTRUCTIONS
    reservation_prefix = "chat:"

    def __init__(self, host: Any) -> None:
        self._host = host
        self.root: Path = host.root / "chat"
        self.ledger_path: Path = host.root / LEDGER_NAME
        self.profile_id: str | None = None
        self.reason: str | None = "尚未启动"
        self._assembled: Any = None
        self._open_turns: dict[str, tuple[Any, str]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._runs: dict[str, str] = {}  # 运行号 → 会话号（输出流标作用域用）

    # ---- 生命周期 ----------------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._assembled is not None

    async def open(self) -> None:
        host = self._host
        # 与任务池同一个计数器实例：上界计数器的 meter_factory 只认自己，另建一个就起不来
        # （2026-10-21 Sol 真实模型局发现）
        counter = host._pool_counter
        if counter is None or host.provider is None or host.native is None:
            self.reason = "没有认证计数器或模型提供方"
            return
        tokens = int(host.settings.context_input_tokens)
        profile_id = native_profile_id(
            tokens, model=getattr(counter, "profile_slug", None), chat=True
        )
        profile_id = self._profile_id(profile_id)
        config = self._configuration(replace(host.config, evidence_root=self.root))
        wanted = ContextPolicy(
            max_input_tokens=tokens,
            output_reserve=NATIVE_OUTPUT_TOKENS,
            max_tool_result_tokens=16384,
            render_slack_tokens=0,
        )
        policy = resolve_profile_context_policy(
            config, profile_id=profile_id, tokenizer=counter, fresh_policy=wanted
        )
        if policy != wanted:
            raise RuntimeError(f"上下文执行库配置不一致：{profile_id}")
        profile = RuntimeProfile(
            profile_id,
            calibrated(host.provider, counter),
            config.model,
            provider_kind=host._provider_kind or ("env" if host.model is not None else "fixtures"),
            context_policy=policy,
            tokenizer=counter,
            default_max_output_tokens=8192,
            max_output_tokens_ceiling=NATIVE_OUTPUT_TOKENS,
            native_plane=self._native_plane(profile_id, tokens, counter),
        )
        assembled = assemble_orchestrator_runtime(
            config, profiles={profile_id: profile}, default_profile=profile_id
        )
        await assembled.__aenter__()
        self._assembled, self.profile_id, self.reason = assembled, profile_id, None
        # 上次宿主关掉时还在跑的回合：叫醒它跑完（结果进执行库；它的预留已按上限结清）
        try:
            await self._recover_pending_turns()
        except Exception:  # noqa: BLE001 - 恢复失败不挡新回合
            logger.exception("chat pool recovery failed")
        self._reconcile_reservations()

    async def _recover_pending_turns(self) -> None:
        await self._pool().runtime.recover_pending_turns()

    def _profile_id(self, profile_id: str) -> str:
        return profile_id

    def _configuration(self, config: Any) -> Any:
        return config

    def _native_plane(self, profile_id: str, tokens: int, counter: Any) -> Any:
        return self._host.native.assembly(
            profile_id, tokens=tokens, counter=counter, owner_mode="STANDALONE_CHAT"
        )

    def _registered_tools(self) -> tuple[str, ...]:
        return tuple(self._host.tools)

    def _budget_subject(self, agent: Any, input_id: str) -> str:
        return f"{self.reservation_prefix}{agent.agent_id}:{input_id}"

    def _seen_before(self, pool: Any, agent: Any, input_id: str) -> set[str]:
        return set(self._invocations(pool, agent.run_id))

    async def close(self) -> None:
        assembled, self._assembled = self._assembled, None
        self._open_turns.clear()
        self.reason = "已关闭"
        if assembled is not None:
            try:
                await assembled.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.exception("chat pool close failed")

    def scope_of(self, run_id: str) -> dict[str, Any] | None:
        """输出流问：这个运行是不是某个聊天会话。"""

        session_id = self._runs.get(run_id)
        if session_id is None:
            return None
        current = self._open_turns.get(session_id)
        return {
            "scope": "chat",
            "session_id": session_id,
            "turn_id": None if current is None else current[1],
        }

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "profile_id": self.profile_id,
            "reason": self.reason,
            "root": str(self.root),
            "open_turns": len(self._open_turns),
        }

    # ---- 一回合 --------------------------------------------------------------------------

    def _reconcile_reservations(self) -> None:
        """上次没结清的聊天预留（回合在途时宿主被关、结算时账本打不开）：按上限结清——
        可以多算不可以少算，也不能让月账永远占着。"""

        try:
            store, ledger = self._ledger()
        except ChatRequestError:
            return
        with store.transaction():
            rows = store.connection.execute(
                "SELECT subject_id FROM budget_reservations"
                " WHERE subject_id LIKE ? AND state = 'RESERVED'",
                (self.reservation_prefix + "%",),
            ).fetchall()
            for (subject,) in rows:
                ledger.settle_at_upper_bound(subject_id=str(subject))
        if rows:
            logger.warning("chat: %d leftover reservations settled at the upper bound", len(rows))

    def _pool(self) -> Any:
        if self._assembled is None or self.profile_id is None:
            raise ChatRequestError("chat_unavailable", self.reason or "聊天不可用")
        return self._assembled.pool(self.profile_id)

    async def turn(
        self,
        body: Mapping[str, Any],
        *,
        on_delta: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        session_id = _text(body, "session_id")
        text = _text(body, "text")
        input_id = _text(body, "idempotency_key")
        replay = self._find_turn(session_id, input_id)
        if replay is not None:
            return replay
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            replay = self._find_turn(session_id, input_id)
            if replay is not None:
                return replay
            return await self._turn(session_id, text, input_id, body, on_delta)

    async def _turn(
        self,
        session_id: str,
        text: str,
        input_id: str,
        body: Mapping[str, Any],
        on_delta: Callable[[Mapping[str, Any]], None] | None,
    ) -> dict[str, Any]:
        pool = self._pool()
        require_tool = bool(body.get("require_tool", False))
        agent, allowed, history_reset = await self._agent(session_id, body.get("tools"))
        self._bind(pool, agent, session_id, allowed)
        subject = self._budget_subject(agent, input_id)
        gateway = self._assembled.gateway
        calls_before = len(gateway.calls)
        seen_before = self._seen_before(pool, agent, input_id)
        # 提醒是第二个回合，再有一整份调用额度：预留按两回合算
        self._reserve(
            subject, int(self._host.settings.chat_max_model_calls) * (2 if require_tool else 1)
        )
        started = time.perf_counter()
        nudged = tool_not_used = False
        first_output: list[float] = []
        result: Any = None
        unsubscribe = self._subscribe(agent, session_id, on_delta, started, first_output)
        try:
            result = await self._complete(session_id, agent, text, input_id)
            if (
                require_tool
                and result.state is AgentTurnState.COMMITTED
                and not self._tool_calls(gateway, agent.run_id, calls_before)
            ):
                nudged = True
                result = await self._complete(session_id, agent, NUDGE_TEXT, f"{input_id}:nudge")
                tool_not_used = not self._tool_calls(gateway, agent.run_id, calls_before)
        finally:
            unsubscribe()
            tool_calls = self._tool_calls(gateway, agent.run_id, calls_before)
            facts, model_calls, unknown = self._close_books(
                subject, pool, agent.run_id, seen_before, result, len(tool_calls)
            )
        output = result.public_output
        row = {
            "at": time.time(),
            "session_id": session_id,
            "input_id": input_id,
            "turn_id": result.turn_id,
            "agent_id": agent.agent_id,
            "state": str(result.state),
            # 这一句换了新 Agent（上一个卡在结果不明的模型调用上）：之前的对话它看不到了
            "history_reset": history_reset,
            "text": output.content
            if output is not None and isinstance(output.content, str)
            else "",
            "error": None if result.error is None else dict(result.error),
            "tool_calls": tool_calls,
            "model_calls": model_calls,
            "usage": {
                "input_tokens": sum(f.input_tokens for f in facts),
                "output_tokens": sum(f.output_tokens for f in facts),
                "unknown": unknown,
            },
            "model": pool.profile.model,
            "nudged": nudged,
            "tool_not_used": tool_not_used,
            "first_output_ms": None if not first_output else int(first_output[0] * 1000),
            "total_ms": int((time.perf_counter() - started) * 1000),
        }
        self._append(row)
        return row

    def _subscribe(
        self,
        agent: Any,
        session_id: str,
        on_delta: Callable[[Mapping[str, Any]], None] | None,
        started: float,
        first_output: list[float],
    ) -> Callable[[], None]:
        """把这一回合挂到输出流上：每段正文转给 ``on_delta``，第一段的时间记下来。"""

        stream = getattr(self._host, "output_stream", None)
        if on_delta is None or stream is None:
            return lambda: None

        def forward(event: Mapping[str, Any]) -> None:
            if event.get("kind") != "piece":
                return
            if not first_output:
                first_output.append(time.perf_counter() - started)
            current = self._open_turns.get(session_id)
            on_delta(
                {
                    "session_id": session_id,
                    "turn_id": None if current is None else current[1],
                    "generation": event.get("generation"),
                    "seq": event.get("seq"),
                    "text": event.get("text"),
                }
            )

        return stream.subscribe(forward, run_id=agent.run_id)

    async def _agent(self, session_id: str, tools: Any) -> tuple[Any, tuple[str, ...], bool]:
        """会话的 Agent：第一句建、以后打开。工具名单在第一句定下。
        返回 ``(agent, 工具名单, 是否换了新 Agent)``。"""

        host = self._host
        registered = self._registered_tools()
        if tools is None:
            wanted: tuple[str, ...] | None = None
        else:
            if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
                raise ChatRequestError("invalid_request", "tools 必须是工具名列表")
            unknown = [t for t in tools if t not in registered]
            if unknown:
                raise ChatRequestError("invalid_request", f"未注册的工具：{unknown}")
            wanted = tuple(dict.fromkeys(tools))
        runtime = self._pool().runtime
        generation, binding = self._session_generation(runtime, session_id)
        reset = False
        if binding is not None and await self._given_up(runtime, binding):
            # 上次宿主关掉时这个 Agent 有一次模型调用在线路上、结果丢了：内核如实记"结果不明"，
            # 这一回合永远等不到答案（重放会再计费一次，不做）。和任务池一样放弃这个执行者，
            # 这个会话换一个新 Agent 接着聊；之前的对话历史随旧 Agent 留在执行库里。
            generation += 1
            binding = None
            reset = True
            logger.warning("chat session %s: agent given up, generation %d", session_id, generation)
        creation_key = self._creation_key(session_id, generation)
        agent_id = agent_id_for(OWNER_SCOPE, creation_key)
        if binding is not None:
            frozen = AgentConfig.from_json(thaw_json(binding.config_json))
            if wanted is not None and tuple(frozen.tool_names) != wanted:
                raise ChatRequestError(
                    "chat_tools_frozen", "这个会话的工具名单在第一句时已定下，不能换；换个会话号"
                )
            self._runs[agent_id] = session_id
            return await runtime.open(agent_id), tuple(frozen.tool_names), False
        allowed = registered if wanted is None else wanted
        if reset:  # 新一代沿用上一代的工具名单（名单在会话第一句定下，换代也不能换）
            previous = runtime.binding(
                agent_id_for(OWNER_SCOPE, self._creation_key(session_id, generation - 1))
            )
            if previous is not None:
                inherited = tuple(AgentConfig.from_json(thaw_json(previous.config_json)).tool_names)
                if wanted is not None and wanted != inherited:
                    raise ChatRequestError(
                        "chat_tools_frozen",
                        "这个会话的工具名单在第一句时已定下，不能换；换个会话号",
                    )
                allowed = inherited
        settings = host.settings
        config = AgentConfig(
            name=f"chat {session_id}"[:128],
            instructions=self.instructions,
            model_profile_ref=str(self.profile_id),
            tool_names=allowed,
            limits=AgentLimits(
                max_model_calls_per_turn=int(settings.chat_max_model_calls),
                max_tool_calls_per_turn=int(settings.chat_max_tool_calls),
                turn_deadline_seconds=float(settings.chat_turn_seconds),
            ),
        )
        caller = host.native.control_caller({"command_id": f"chat-create:{creation_key}"})
        agent = await runtime.create(config, creation_key=creation_key, caller=caller)
        self._runs[agent.run_id] = session_id
        return agent, allowed, reset

    @staticmethod
    def _creation_key(session_id: str, generation: int) -> str:
        return f"chat:{session_id}" if generation == 1 else f"chat:{session_id}:g{generation}"

    def _session_generation(self, runtime: Any, session_id: str) -> tuple[int, Any]:
        """这个会话现在用到第几代 Agent（放弃过几次）与它的绑定；没建过就是 (1, None)。"""

        generation, binding = 1, None
        for candidate in range(1, _MAX_GENERATIONS + 1):
            found = runtime.binding(
                agent_id_for(OWNER_SCOPE, self._creation_key(session_id, candidate))
            )
            if found is None:
                break
            generation, binding = candidate, found
        return generation, binding

    @staticmethod
    async def _given_up(runtime: Any, binding: Any) -> bool:
        """这个 Agent 还能不能接着用：已关闭，或开着的回合卡在结果不明的模型调用上，就不能。"""

        if binding.lifecycle != "open":
            return True
        turn = runtime.uow.read_open_agent_turn(binding.run_id)
        if turn is None:
            return False
        agent = await runtime.open(binding.agent_id)
        snapshot = agent.turn_snapshot(turn.turn_id)
        return bool(snapshot.blocked) and (snapshot.blocker or {}).get("kind") == "provider"

    def _bind(self, pool: Any, agent: Any, session_id: str, allowed: tuple[str, ...]) -> None:
        workspace_id = f"chat-{session_id}"
        self._assembled.workspaces.create(workspace_id, seed={})
        self._assembled.gateway.bind(
            agent.run_id,
            WorkspaceBinding(
                attempt_id=workspace_id,
                view="chat",
                writable=True,
                allowed_tools=allowed,
                context_policy=pool.profile.context_policy,
                tokenizer=pool.profile.tokenizer,
                mission_id=None,
            ),
        )

    async def _complete(self, session_id: str, agent: Any, text: str, input_id: str) -> Any:
        receipt = await agent.submit(text, input_id=input_id)
        self._open_turns[session_id] = (agent, receipt.turn_id)
        try:
            deadline = time.monotonic() + float(self._host.settings.chat_turn_seconds) + 30.0
            result = await self._wait(agent, receipt.turn_id, deadline)
            if result is None:
                await agent.cancel_turn(
                    receipt.turn_id, command_id=f"chat-timeout:{receipt.turn_id}"
                )
                result = await self._wait(
                    agent, receipt.turn_id, time.monotonic() + _CANCEL_GRACE_SECONDS
                )
            if result is None:
                raise ChatRequestError("chat_timeout", "这一回合超时，且没能取消；稍后再试")
            return result
        finally:
            self._open_turns.pop(session_id, None)

    @staticmethod
    async def _wait(agent: Any, turn_id: str, deadline: float) -> Any:
        pause = _POLL_SECONDS
        while True:
            result = agent.get_result(turn_id)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(pause)
            pause = min(_POLL_MAX_SECONDS, pause * 1.5)

    @staticmethod
    def _tool_calls(gateway: Any, run_id: str, since: int) -> list[dict[str, Any]]:
        return [
            {
                "tool": str(record.get("tool")),
                "call_id": str(record.get("call_id")),
                "outcome": str(record.get("outcome") or "unknown"),
            }
            for record in gateway.calls[since:]
            if record.get("run_id") == run_id
        ]

    # ---- 用量与预算 ----------------------------------------------------------------------

    @staticmethod
    def _invocations(pool: Any, run_id: str) -> dict[str, Any]:
        uow = pool.runtime.uow
        with uow.database.transaction(read_only=True) as connection:
            rows = connection.execute(
                "SELECT invocation_id FROM provider_invocations WHERE run_id=?"
                " ORDER BY claimed_at, invocation_id",
                (run_id,),
            ).fetchall()
            return {
                str(row[0]): uow.read_effective_provider_invocation(str(row[0])) for row in rows
            }

    def _usage(
        self, pool: Any, run_id: str, seen_before: set[str]
    ) -> tuple[list[UsageFact], int, bool]:
        facts: list[UsageFact] = []
        unknown = False
        calls = 0
        for invocation_id, record in self._invocations(pool, run_id).items():
            if invocation_id in seen_before or record is None or record.handoff_attempt == 0:
                continue
            calls += 1
            usage = _usage_of(record)
            if usage is None:
                unknown = True
                facts.append(UsageFact(f"provider-invocation:{invocation_id}", 0, 0, unknown=True))
            else:
                facts.append(UsageFact(f"provider-invocation:{invocation_id}", *usage))
        return facts, calls, unknown

    def _close_books(
        self,
        subject: str,
        pool: Any,
        run_id: str,
        seen_before: set[str],
        result: Any,
        tool_calls: int,
    ) -> tuple[list[UsageFact], int, bool]:
        """回合结束结清预留。回合没到终态（超时、宿主在关）或读不到用量、账本打不开：
        都按上限结清；这里不抛，原来的异常照样往外走。"""

        facts: list[UsageFact] = []
        model_calls, unknown = 0, True
        settled = False
        try:
            facts, model_calls, unknown = self._usage(pool, run_id, seen_before)
            terminal = result is not None and result.state in (
                AgentTurnState.COMMITTED,
                AgentTurnState.FAILED,
            )
            self._settle(subject, facts, unknown or not terminal, tool_calls)
            settled = True
        except Exception:  # noqa: BLE001
            logger.exception("chat turn settlement failed; settling at the upper bound")
        if not settled:
            try:
                store, ledger = self._ledger()
                with store.transaction():
                    ledger.settle_at_upper_bound(subject_id=subject)
            except Exception:  # noqa: BLE001 - 账本也打不开：下次启动 _reconcile_reservations 补
                logger.exception("chat upper-bound settlement failed; reconciled at next open")
        return facts, model_calls, unknown

    def _ledger(self) -> tuple[Any, Any]:
        orchestrator = self._host.orchestrator
        if orchestrator is None:
            raise ChatRequestError("chat_unavailable", "编排服务不可用，预算账本打不开")
        return orchestrator.store, orchestrator.commit.ledger

    def _reserve(self, subject: str, model_calls: int) -> None:
        host = self._host
        store, ledger = self._ledger()
        tokens = model_calls * (int(host.settings.context_input_tokens) + NATIVE_OUTPUT_TOKENS)
        with store.transaction():
            account = global_account_id(store.now)
            ledger.open_account(
                account_id=account,
                scope="global",
                parent_id=None,
                mission_id="global",
                limits=Budget(max_tokens=host.settings.global_monthly_max_tokens),
            )
            try:
                ledger.reserve(
                    account_id=account,
                    subject_id=subject,
                    tokens=tokens,
                    counts_attempt=False,
                    mission_id="chat",
                )
            except BudgetExhausted as error:
                raise ChatRequestError(
                    "budget_exhausted",
                    f"本月 token 配额不够这一回合（需要预留 {error.requested}，"
                    f"剩 {error.remaining}）",
                ) from error

    def _settle(self, subject: str, facts: list[UsageFact], unknown: bool, tool_calls: int) -> None:
        store, ledger = self._ledger()
        with store.transaction():
            ledger.import_usage(subject_id=subject, mission_id="chat", facts=facts)
            if unknown:
                ledger.settle_at_upper_bound(subject_id=subject)
            else:
                ledger.settle(subject_id=subject, tool_calls=tool_calls)

    # ---- 取消与台账 ----------------------------------------------------------------------

    async def cancel(self, body: Mapping[str, Any]) -> dict[str, Any]:
        session_id = _text(body, "session_id")
        entry = self._open_turns.get(session_id)
        if entry is None:
            return {"session_id": session_id, "cancelled": False, "reason": "no_open_turn"}
        agent, turn_id = entry
        receipt = await agent.cancel_turn(turn_id, command_id=f"chat-cancel:{session_id}:{turn_id}")
        state = str(receipt.state)
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "cancelled": state in {"cancelled", "pending"},
            "state": state,
        }

    def _append(self, row: Mapping[str, Any]) -> None:
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("chat ledger write failed", exc_info=True)

    def _rows(self) -> list[dict[str, Any]]:
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _find_turn(self, session_id: str, input_id: str) -> dict[str, Any] | None:
        for row in reversed(self._rows()):
            if row.get("session_id") == session_id and row.get("input_id") == input_id:
                return row
        return None

    def history(self, session_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = [row for row in self._rows() if row.get("session_id") == session_id]
        return rows[-max(1, int(limit)) :]


__all__ = ("CHAT_INSTRUCTIONS", "LEDGER_NAME", "NUDGE_TEXT", "ChatRequestError", "ChatService")
