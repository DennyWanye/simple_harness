"""ARP-EXEC-1.1.1 真实模型 12 局（TEST-PLAN §5：四类各三局），SDK 层运行器。

用法（SDK venv）：
  cd sdk/simple-harness-sdk && uv run --frozen python ../../plans/AgentRuntime/2026-09-23-arp-body/tools/arp_real_games.py <证据目录> [--games LM01,LM02,LM03,LM04] [--rounds 3]

模型：DeepSeek V4.1 Flash 经本机日卡闸口（127.0.0.1:28181），密钥只在进程内从主副本 .env 读取，不写入输出。
计量：官方 tokenizer + 官方渲染器的认证 EXACT 计数（`CertifiedDeepSeekCounter`），只算线上请求（WIRE_ONLY，2026-09-24 起无先前输出储备）；每局记用量校对（回报为正时我们的精确计数不得少于它超过安全余量）。
嵌入：无（本机 BGE-M3 不可用）→ 检索 LEXICAL_ONLY，凡依赖向量的判据记"环境未验"，不冒充 PASS。
脚本执行器：无（Host 尚未接沙箱）→ LM03 的 script 部分记"环境缺失"。
失败全部保留，不挑选成功局；每局上限：提供方调用 24 次、墙钟 30 分钟。
证据目录必须在 .local-test-evidence 下（gitignored）。
"""
from __future__ import annotations

import argparse, asyncio, json, os, re, sqlite3, sys, time, traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
REPO = HERE.parents[4]
SDK = REPO / "sdk/simple-harness-sdk"
sys.path.insert(0, str(SDK / "tests/agents/arp"))
sys.path.insert(0, str(SDK / "tests/agents"))
sys.path.insert(0, str(SDK / "tests"))

from agent_orchestrator.runtime.deepseek_meter import CalibratingProvider, RelayDeepSeekCounter, RelayToolMargin, deepseek_meter_binding
from simple_harness.agents import AgentConfig
from simple_harness.agents.arp import store
from simple_harness.agents.arp.errors import ArpError
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.ports import ArpPorts, bootstrap_root
from simple_harness.agents.arp.profile import ProfileRefs, RuntimeProfile, default_policy
from simple_harness.agents.arp.runtime import build_arp_runtime
from simple_harness.agents.arp.skill_tools import SKILL_DISCOVER_TOOL_NAME, SKILL_EXECUTE_TOOL_NAME, SKILL_LOAD_TOOL_NAME, TOOL_DISCOVER_TOOL_NAME
from simple_harness.agents.arp.strict import digest
from simple_harness.agents.arp.tools import READ_TOOL_NAME, SEARCH_TOOL_NAME
from simple_harness.agents.contracts import AgentTurnState
from simple_harness.agents.ports import AgentRuntimePorts
from simple_harness.api.runtime_plane import RuntimePlaneService
from simple_harness.execution.provider_invocations import provider_request_fingerprint
from simple_harness.providers import OpenAICompatibleProvider, Secret
from simple_harness.providers.errors import ProviderRequestRejectedError
from simple_harness.providers.reconciliation import ProviderReconciliationObservation, ProviderReconciliationState
from simple_harness.runtime.consumer_adapter import ConsumerRuntimePolicies
from simple_harness.runtime.ports import AuthorizationResult

from arp_fixture import HASH, trusted_caller
from skill_fixture import AcceptingAssurance, admit_skill, import_skill, md_bundle, native_bundle

MODEL = "deepseek-v4.1-flash"
GATE = "http://127.0.0.1:28181/v1"
ENV_FILE = Path(os.environ.get("REAL_ENV_FILE", "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.env"))
TOKENIZER = Path(os.environ.get("SH_TOKENIZER_PATH") or (Path.home() / "Library/Application Support/deskpet/models/deepseek-v41/tokenizer.json"))
LIMIT, OUT = 6144, 1024            # a deliberately small window: early facts must come back through recall
# Thinking mode (user decision 2026-09-24: both modes supported).  "disabled" is the default;
# "enabled" replays each turn's reasoning inside the run and needs a larger output cap,
# because the reasoning tokens are part of the completion.  Set by ``--thinking``.
THINKING = os.environ.get("ARP_GAMES_THINKING", "disabled")
THINKING_OUT = 2048
# The relay only thinks when an effort is sent with the switch (measured 2026-09-24: switch
# alone 0/6, switch + effort 11/11); "high" is DeepSeek's documented default effort.
THINKING_EFFORT = "high"
MARGIN: Any = None  # the relay's learned tool-preamble margin, created in main()
SAFETY = 64                        # the profile's safety reserve; also the usage-calibration tolerance
# DeepSeek V4.1 thinks by default; the runtime's continuation contract is "reasoning
# disabled" and thinking would have to be passed back inside tool loops (official guide),
# which the adapter does not do yet — so every request switches it off explicitly.
# The relay echoes the vendor-prefixed spelling; the kernel trusts usage only when the echo
# equals the bound model name, so the spelling is declared as an alias of it.
MODEL_ALIASES = ("deepseek-ai/DeepSeek-V4.1-Flash",)
MAX_CALLS, WALL_S = 24, 1800
# Round 1 (2026-09-24): without ``tool_names`` no tool reaches the wire at all (LM03: "当前未提供
# skill.discover / skill.load 工具").  Exposure is per Agent config, by registered tool name.
HISTORY_TOOLS = (SEARCH_TOOL_NAME, READ_TOOL_NAME)
SKILL_TOOLS = (SKILL_DISCOVER_TOOL_NAME, SKILL_LOAD_TOOL_NAME, SKILL_EXECUTE_TOOL_NAME, TOOL_DISCOVER_TOOL_NAME)
CONFIG = AgentConfig(name="w", instructions="你是助手。回答尽量简短。", model_profile_ref="p", tool_names=HISTORY_TOOLS)
SKILL_CONFIG = AgentConfig(name="w", instructions="你是助手。回答尽量简短。", model_profile_ref="p", tool_names=HISTORY_TOOLS + SKILL_TOOLS)


def read_key() -> str:
    for line in ENV_FILE.read_text().splitlines():
        m = re.match(r"^\s*(?:export\s+)?DEEPSEEKER_APIKEY\s*=(.*)$", line)
        if m:
            return m.group(1).strip().strip("'\"")
    raise SystemExit("no key")


class GameAuthorization:
    """A named policy port for the games (not AllowAll): records every request, allows
    the runtime's model tools, denies everything else by name."""

    policy_id = "arp-real-games-policy"
    # The runtime's builtin model tools, by their registered (underscore) names.
    ALLOWED = {SEARCH_TOOL_NAME, READ_TOOL_NAME, SKILL_DISCOVER_TOOL_NAME, SKILL_LOAD_TOOL_NAME, SKILL_EXECUTE_TOOL_NAME, TOOL_DISCOVER_TOOL_NAME}

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.denied: list[str] = []

    async def request_authorization(self, request: Any) -> AuthorizationResult:
        name = str(getattr(getattr(request, "tool_call", None), "name", "?"))
        self.requests.append(name)
        if name in self.ALLOWED:
            return AuthorizationResult.allow()
        self.denied.append(name)
        return AuthorizationResult.deny(f"{name} is outside {self.policy_id}")


def game_profile(name: str) -> RuntimeProfile:
    policy = default_policy(f"real-game-{name}")
    policy.update(
        max_context_tokens=LIMIT, output_reserve_tokens=OUT, safety_reserve_tokens=SAFETY, tool_headroom_tokens=256,
        recent_min_tokens=768, recall_max_tokens=1536, fixed_soft_max_tokens=2048,
        section_soft_caps={"A": 512, "B": 1024, "C": 1024, "D": 1024, "E": 512},
        embedding_chunk_tokens=128, embedding_overlap_tokens=16, max_scan_rows_per_page=8, max_recall_items=16,
        embedding_required_for_activation=False,
    )
    refs = ProfileRefs(
        retention_policy_ref=Pin("policy", "retention-default", 1, HASH),
        capability_registry_ref=Pin("catalogue", "realm/real-games", 1, HASH),
        activation_receipt_ref=Pin("receipt", "deploy:activate:real-games", 0, HASH),
        default_skill_policy_ref=Pin("policy", "skill-default", 1, HASH),
        embedding_deployment_ref=Pin("deployment", "none", 1, HASH),
        catalogue_namespace_id="realm/real-games",
    )
    return RuntimeProfile(profile_id=f"real-game-{name}", profile_revision=1, owner_mode="STANDALONE_CHAT",
                          allow_lexical_degradation=True, context_policy=policy, refs=refs)


class TransportGate:
    """The deployment's own transport record around the real provider: which request ids were
    physically sent.  ``hold`` parks the next call *before* anything leaves the process (the
    kernel has already recorded the handoff), so a kill there is the "prepared, handed off,
    never sent" crash window.  The record outlives a Harness (it is the deployment's, not the
    process') and is what the reconciliation port answers from."""

    def __init__(self, inner, record: dict) -> None:  # type: ignore[no-untyped-def]
        self.inner = inner; self.record = record
        record.setdefault("sent", set()); record.setdefault("held", set()); record.setdefault("hold_next", False)
        record.setdefault("held_event", asyncio.Event()); record.setdefault("wire_hashes", {})
        record.setdefault("physical_calls", 0); record.setdefault("cap_refusals", 0)
        record.setdefault("replay", [])

    @property
    def target(self):  # type: ignore[no-untyped-def]
        return self.inner.target

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self.inner, name)

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        key = request.request_id.value
        if self.record["hold_next"]:
            self.record["hold_next"] = False; self.record["held"].add(key); self.record["held_event"].set()
            await asyncio.sleep(3600)  # the process dies before this returns; nothing was sent
        # Per-game ceiling (TEST-PLAN §5: 24 original Provider calls).  The record is the
        # deployment's, shared across a crash/rebuild, so a restart never resets it.  The
        # 25th call is refused before anything leaves the process.
        if self.record["physical_calls"] >= MAX_CALLS:
            self.record["cap_refusals"] += 1
            raise ProviderRequestRejectedError(public_message=f"per-game provider call cap {MAX_CALLS} reached")
        self.record["physical_calls"] += 1
        self.record["sent"].add(key)
        # Thinking mode evidence: what the wire request actually carries for every earlier
        # assistant message (the replayed private reasoning, key present even when empty).
        assistants = [m for m in request.messages if getattr(m.role, "value", m.role) == "assistant"]
        texts = [m.metadata.get("provider_reasoning_content") for m in assistants]
        self.record["replay"].append({
            "request": key, "assistants": len(assistants), "with_key": sum(t is not None for t in texts),
            "nonempty": sum(bool(t) for t in texts), "chars": sum(len(t or "") for t in texts),
        })
        # What actually goes on the wire, hashed exactly as the composer hashes its plan.
        self.record["wire_hashes"].setdefault(key, []).append(provider_request_fingerprint(request))
        return await self.inner.invoke(request, cancel=cancel)


class RunnerReconciliation:
    """``ProviderReconciliationPort`` of this deployment: a request the gate never put on the
    wire is CONFIRMED_NOT_STARTED (by the gate's own record); anything that left the process
    towards a stateless gateway is STILL_UNKNOWN — never guessed."""

    def __init__(self, record: dict) -> None:
        self.record = record; self.observations: list[dict] = []

    async def observe(self, invocation):  # type: ignore[no-untyped-def]
        key = invocation.request_id.value
        if key in self.record["held"] and key not in self.record["sent"]:
            state, evidence = ProviderReconciliationState.CONFIRMED_NOT_STARTED, f"runner-transport:not-sent:{key}"
        else:
            state, evidence = ProviderReconciliationState.STILL_UNKNOWN, f"runner-transport:unknown:{key}"
        self.observations.append({"invocation": invocation.invocation_id[:12], "state": str(state), "evidence": evidence})
        return ProviderReconciliationObservation(state, evidence)


class Harness:
    """One game's runtime on its own directory; can be killed and rebuilt (crash window)."""

    def __init__(self, directory: Path, name: str, *, key: str, acceptance: Any = None, fault: Any = None, transport: dict | None = None, owner: str = "arp-real-games") -> None:
        self.dir = directory; self.name = name; self.key = key; self.acceptance = acceptance; self.fault = fault
        self.transport = transport if transport is not None else {}; self.owner = owner
        self.reconciliation = RunnerReconciliation(self.transport)
        self.authorization = GameAuthorization()
        self.failures: list[dict] = []
        # The gate forwards to a relay, not the official endpoint: an upper-bound counter with
        # the relay's learned tool-preamble margin (user decision 2026-09-24), shared by every
        # game of this process and persisted beside the evidence.
        effort = THINKING_EFFORT if THINKING == "enabled" else None
        self.counter = RelayDeepSeekCounter(TOKENIZER, model=MODEL, thinking=THINKING, reasoning_effort=effort, margin=MARGIN)
        self.runtime: Any = None; self.client: Any = None

    async def start(self) -> None:
        import httpx
        self.client = httpx.AsyncClient()
        provider = TransportGate(CalibratingProvider(OpenAICompatibleProvider(self.client, GATE, MODEL, Secret(self.key), timeout=600.0, allow_private_http=True, stream=True, thinking=THINKING, reasoning_effort=(THINKING_EFFORT if THINKING == "enabled" else None), response_model_aliases=MODEL_ALIASES), self.counter), self.transport)
        root = bootstrap_root(self.dir / "root", root_id=f"real-games:{self.name}")
        meter = deepseek_meter_binding(self.counter, input_limit_tokens=LIMIT, max_output_tokens=OUT)
        policies = ConsumerRuntimePolicies("unpriced_local", False, "fail_closed", provider_reconciliation=self.reconciliation)
        ports = AgentRuntimePorts(provider=provider, authorization=self.authorization, database_path=str(self.dir / "runtime.db"), model=MODEL,
                                  owner_id=self.owner, tokenizer=self.counter, default_max_output_tokens=OUT, max_output_tokens_ceiling=OUT, policies=policies)
        arp = ArpPorts(root_dir=root.directory, profile=game_profile(self.name), activation_receipt={"kind": "deployment_activation", "profile_id": f"real-game-{self.name}", "revision": 1},
                       meter=meter, embedding=None, acceptance=self.acceptance, script_runner=None, fault=self.fault)
        self.runtime = build_arp_runtime(ports, arp)
        await self.runtime.__aenter__()

    async def stop(self) -> None:
        if self.runtime is not None:
            try:
                await self.runtime.__aexit__(None, None, None)
            finally:
                try: self.runtime.uow.database.close()
                except Exception: pass
                self.runtime = None
        if self.client is not None:
            await self.client.aclose(); self.client = None

    async def kill(self) -> None:
        """Crash window: close the library without a graceful runtime exit."""
        try: self.runtime.uow.database.close()
        except Exception: pass
        try: await asyncio.wait_for(self.runtime.__aexit__(None, None, None), timeout=5)
        except Exception: pass
        self.runtime = None
        if self.client is not None:
            await self.client.aclose(); self.client = None

    @property
    def conn(self):  # type: ignore[no-untyped-def]
        return self.runtime.uow.database.connection

    async def create(self, key: str, config: AgentConfig = CONFIG):  # type: ignore[no-untyped-def]
        return await self.runtime.create(config, creation_key=key, caller=trusted_caller(f"create-{key}"))

    async def turn(self, agent, text: str, input_id: str, timeout: float = 600):  # type: ignore[no-untyped-def]
        receipt = await agent.submit(text, input_id=input_id)
        result = await agent.wait_turn(receipt.turn_id, timeout=timeout)
        if result.state is not AgentTurnState.COMMITTED:
            self.failures.append({"input_id": input_id, "error": None if result.error is None else dict(result.error)})
        return result

    # ---- ledger readers --------------------------------------------------------------------

    def calls(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM provider_invocations").fetchone()[0]

    def invocations(self) -> list[dict]:
        rows = self.conn.execute("SELECT invocation_id, run_id, request_id, state, request_fingerprint, rehandoff_count, substr(usage_json,1,240) AS usage FROM provider_invocations ORDER BY claimed_at").fetchall()
        return [dict(r) for r in rows]

    def context_rows(self) -> list[dict]:
        rows = self.conn.execute("SELECT context_id, agent_id, turn_id, provider_request_ordinal, journal_highwater, manifest_hash, planned_request_hash, original_request_key, input_charge, input_budget, created_at_ms FROM arp_context_requests ORDER BY created_at_ms").fetchall()
        return [dict(r) for r in rows]

    def manifest(self, request_key: str):  # type: ignore[no-untyped-def]
        return store.read_context_by_request_key(self.conn, request_key)

    def recall(self, manifest):  # type: ignore[no-untyped-def]
        ref = manifest.manifest.get("retrieval_receipt_ref")
        return None if not ref else store.read_context_recall(self.conn, ref["id"])

    def token_receipt(self, manifest) -> dict:  # type: ignore[no-untyped-def]
        found: dict = {}
        def pick(node):  # type: ignore[no-untyped-def]
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("count_mode", "input_limit_scope", "wire_input_tokens", "prior_output_reserve_tokens", "request_hash", "coverage") and k not in found:
                        found[k] = v
                    pick(v)
            elif isinstance(node, list):
                for v in node: pick(v)
        pick(dict(manifest.manifest)); return found

    def journal_text(self, agent_id: str) -> str:
        import dataclasses
        parts = []
        for rec in self.runtime.uow.read_agent_journal(agent_id):
            body = {f.name: getattr(rec, f.name) for f in dataclasses.fields(rec)} if dataclasses.is_dataclass(rec) else {"repr": repr(rec)}
            parts.append(json.dumps(body, ensure_ascii=False, default=str))
        return "\n".join(parts)

    def invariants(self, agents: dict[str, str]) -> dict:
        """Hard invariants recorded for every game."""
        inv: dict[str, Any] = {}
        # Calls that physically reached the model (the transport's own count, across restarts);
        # ledger rows also include refused / never-sent claims.
        inv["provider_calls"] = self.transport.get("physical_calls", 0); inv["ledger_invocations"] = self.calls()
        inv["cap_refusals"] = self.transport.get("cap_refusals", 0)
        inv["calls_within_cap"] = inv["provider_calls"] <= MAX_CALLS
        inv["authorization_requests"] = list(self.authorization.requests); inv["authorization_denied"] = list(self.authorization.denied)
        inv["no_misauthorization"] = not self.authorization.denied
        # Request-hash drift: every request that reached the transport (the gate's own
        # record, hashed like the composer's plan) must equal the frozen manifest's planned
        # hash — including a re-handoff of the same request after recovery.
        drift = []; sent_requests = 0
        wire_hashes = self.transport.get("wire_hashes", {})
        for row in self.context_rows():
            for sent in wire_hashes.get(row["original_request_key"], []):
                sent_requests += 1
                if sent != row["planned_request_hash"]:
                    drift.append({"request": row["original_request_key"], "planned": row["planned_request_hash"], "wire": sent})
        inv["wire_requests_checked"] = sent_requests
        inv["request_hash_drift"] = drift; inv["no_request_hash_drift"] = not drift
        leak = []
        for label, agent_id in agents.items():
            text = self.journal_text(agent_id)
            for needle in (self.key, "127.0.0.1:28181", "Bearer "):
                if needle and needle in text:
                    leak.append({"agent": label, "needle": "api_key" if needle == self.key else needle})
        inv["credential_or_endpoint_leak"] = leak; inv["no_leak"] = not leak
        counts = {}
        for (t,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'arp_%'"):
            counts[t] = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        inv["arp_table_counts"] = counts
        inv["meter"] = {"count_mode": "EXACT", "tokenizer": self.counter.fingerprint[:40], "input_limit_scope": "WIRE_ONLY"}
        # Usage calibration (rule 2026-09-24: may overcount, never undercount).  When the
        # gateway reports a positive prompt count it is the server's truth: our exact wire
        # count may not fall below it by more than the safety reserve.  A zero or missing
        # report cannot be calibrated and is only counted.
        calibration: dict[str, Any] = {"checked": 0, "unreported": 0, "max_reported_minus_planned": None, "undercounts": []}
        for request_key, usage_json in self.conn.execute("SELECT request_id, usage_json FROM provider_invocations WHERE state IN ('succeeded','failed')"):
            try:
                reported = ((json.loads(usage_json) or {}).get("usage") or {}).get("input_tokens")
            except (TypeError, ValueError):
                reported = None
            if type(reported) is not int or reported <= 0:
                calibration["unreported"] += 1; continue
            frozen = self.manifest(str(request_key))
            planned = None if frozen is None else self.token_receipt(frozen).get("wire_input_tokens")
            if type(planned) is not int:
                continue
            calibration["checked"] += 1
            diff = reported - planned
            best = calibration["max_reported_minus_planned"]
            calibration["max_reported_minus_planned"] = diff if best is None else max(best, diff)
            if diff > SAFETY:
                calibration["undercounts"].append({"request": str(request_key), "reported": reported, "planned": planned})
        inv["usage_calibration"] = calibration; inv["no_undercount"] = not calibration["undercounts"]
        # Reasoning replay (thinking mode): every assistant message on the wire carries the
        # reasoning key, and once real reasoning was returned a later request replays it.
        replay = list(self.transport.get("replay", []))
        stored = self.conn.execute(
            "SELECT count(*) FROM provider_invocations WHERE state='succeeded'"
            " AND length(coalesce(json_extract(response_json,'$.continuation.reasoning_content'),''))>0"
        ).fetchone()[0]
        summary = {"requests": len(replay), "stored_nonempty_reasoning": stored,
                   "requests_missing_key": sum(r["with_key"] < r["assistants"] for r in replay),
                   "requests_replaying_real_reasoning": sum(r["nonempty"] > 0 for r in replay),
                   "max_replayed_chars": max((r["chars"] for r in replay), default=0)}
        inv["reasoning_replay"] = summary
        inv["reasoning_replay_ok"] = THINKING != "enabled" or (
            summary["requests_missing_key"] == 0 and (stored == 0 or summary["requests_replaying_real_reasoning"] > 0)
        )
        inv["turn_failures"] = list(self.failures)
        return inv


def fillers(n: int, topic_seed: str) -> list[str]:
    topics = ["今天的天气有些闷热，午后可能下雨", "咖啡店新出了燕麦拿铁，口味偏甜", "地铁二号线周末检修，改乘公交", "隔壁团队在讨论年度预算的表格格式",
              "健身房换了新的跑步机，坡度更细", "书店在做绘本展，孩子们很喜欢", "小区门口的花坛种了新的月季", "办公室的打印机又卡纸了三次"]
    # Round 1 (2026-09-24) showed ~100-token fillers never push the early fact out of a
    # 6144 window (ask turn: 1643 wire tokens of a 3439 budget, recall found nothing to do).
    # Each filler is now ~450 tokens so the sixteen of them exceed the budget and the early
    # groups must come back through recall.
    return [f"{topic_seed}闲聊第 {i} 条：{topics[i % len(topics)]}。这一条和工程约定无关，只是记录一下当天的琐事，内容较长以占用上下文窗口，" * 9 + f"编号 {i}。" for i in range(n)]


# ------------------------------------------------------------------------------------------------
# LM01 长上下文早期信息
# ------------------------------------------------------------------------------------------------
async def lm01(h: Harness, game: dict) -> None:
    a = await h.create("A"); b = await h.create("B")
    secret_a = "本工程的暗号是「蓝鲸七号」；还有一条约束：所有报告发出前必须先经过李工审阅。"
    secret_b = "本工程的暗号是「赤狐三号」；约束：报告必须先经过王工审阅。"
    r = await h.turn(a, "请记住：" + secret_a + " 只需回复“记住了”。", "a0"); game["turns"].append(("A", "a0", str(r.state)))
    r = await h.turn(b, "请记住：" + secret_b + " 只需回复“记住了”。", "b0"); game["turns"].append(("B", "b0", str(r.state)))
    for i, text in enumerate(fillers(16, "A"), 1):
        r = await h.turn(a, text + " 只需回复“好的”。", f"a{i}"); game["turns"].append(("A", f"a{i}", str(r.state)))
    r = await h.turn(b, fillers(1, "B")[0] + " 只需回复“好的”。", "b1"); game["turns"].append(("B", "b1", str(r.state)))
    before = h.context_rows()
    ask = await h.turn(a, "我们最早约定的工程暗号是什么？那条关于审阅的约束是什么？请说明你是从哪条历史记录查到的（可用 session_history_search 工具）。", "ask")
    game["turns"].append(("A", "ask", str(ask.state)))
    reply = ask.public_output.content if ask.public_output is not None and isinstance(ask.public_output.content, str) else ""
    game["reply"] = reply
    rows = [r for r in h.context_rows() if r not in before and r["agent_id"] == a.agent_id]
    manifest = h.manifest(rows[0]["original_request_key"]) if rows else None
    recall = h.recall(manifest) if manifest is not None else None
    body = manifest.manifest if manifest is not None else {}
    stats = {}
    if recall is not None:
        stats = {"phase": recall.phase, "outcome": (recall.result or {}).get("outcome"), "status": (recall.result or {}).get("status"),
                 "mode": ((recall.result or {}).get("coverage") or {}).get("mode"), "scan_pages": (recall.checkpoint or {}).get("scan_pages_committed"),
                 "result_pages": (recall.checkpoint or {}).get("result_pages_committed"), "candidates": len((recall.result or {}).get("candidate_items") or []),
                 "recalled_chunks": len(body.get("recalled_chunk_ids") or [])}
        recalled = {i["chunk_id"]: i for i in (recall.result or {}).get("candidate_items") or []}
        stats["recalled_outside_recent"] = all(not (set(recalled[c]["source_group_ids"]) & set(body.get("recent_group_ids") or [])) for c in body.get("recalled_chunk_ids") or [] if c in recalled)
        stats["all_from_A_session"] = all(i.get("session_id", recall.session_id) == recall.session_id for i in recalled.values())
    game["recall"] = stats; game["token_receipt"] = h.token_receipt(manifest) if manifest is not None else {}
    game["oracle"] = {
        "answer_has_secret": "蓝鲸七号" in reply, "answer_has_constraint": "李工" in reply,
        "no_cross_agent_secret": "赤狐" not in reply and "王工" not in reply,
        "recall_ready": stats.get("phase") == "READY", "scan_more_than_one_page": (stats.get("scan_pages") or 0) >= 2,
        "recalled_from_early_groups": bool(stats.get("recalled_chunks")) and bool(stats.get("recalled_outside_recent")),
        "vector_lane": "环境未验（无嵌入资源，LEXICAL_ONLY）",
    }
    game["pass"] = all(v for k, v in game["oracle"].items() if isinstance(v, bool))
    game["agents"] = {"A": a.agent_id, "B": b.agent_id}


# ------------------------------------------------------------------------------------------------
# LM02 settings / 恢复
# ------------------------------------------------------------------------------------------------
def _req(verb: str, payload: Any, *, subject: str, command_id: str | None = None, expected_revision: int | None = None) -> dict:
    return {"schema_version": 1, "verb": verb, "command_id": command_id, "subject_id": subject, "expected_revision": expected_revision, "cursor": None, "limit": 8, "payload_ref": None, "payload": payload}


async def lm02(h: Harness, game: dict, *, key: str, rebuild) -> None:  # type: ignore[no-untyped-def]
    a = await h.create("A")
    r = await h.turn(a, "请把 1 到 5 的平方按“1→1, 2→4”的格式列出来，一行写完。", "t1"); game["turns"].append(("A", "t1", str(r.state)))
    if r.state is not AgentTurnState.COMMITTED:
        raise RuntimeError(f"turn t1 failed: {h.failures[-1]}")
    first = h.context_rows()[-1]; first_manifest_hash = first["manifest_hash"]
    service = RuntimePlaneService(h.runtime); caller = trusted_caller("host")
    session = store.read_live_session(h.conn, a.agent_id)
    got = await service.handle(_req("agent_context_settings_get", {"session_id": session.session_id}, subject=a.agent_id), caller=caller)
    view = got["items"][0]; policy = dict(view["configured_policy"]); policy["recall_max_tokens"] += 64
    submitted = await service.handle(_req("agent_context_policy_submit", {"schema_version": 1, "policy": policy, "expected_policy_ref": view["effective_policy_ref"]}, subject=a.agent_id, command_id="p1", expected_revision=0), caller=caller)
    assert submitted["error"] is None, submitted["error"]
    updated = await service.handle(_req("agent_context_settings_update", {"schema_version": 1, "session_id": session.session_id, "candidate_policy_ref": submitted["items"][0]["policy_ref"],
                                                                            "expected_effective_policy_ref": view["effective_policy_ref"], "expected_adoption_revision": view["adoption_revision"]},
                                        subject=a.agent_id, command_id="u1", expected_revision=view["adoption_revision"]), caller=caller)
    assert updated["error"] is None, updated["error"]
    adoption_after = updated["items"][0]["adoption_revision"]
    r = await h.turn(a, "再把 6 到 8 的平方列出来。", "t2"); game["turns"].append(("A", "t2", str(r.state)))
    second = h.context_rows()[-1]
    m2 = h.manifest(second["original_request_key"]); adoption_used = int(m2.manifest.get("adoption_revision", -1))
    first_again = next(row for row in h.context_rows() if row["original_request_key"] == first["original_request_key"])
    # Crash window: the third turn's request is frozen (PREPARED) and handed off, but the
    # transport gate holds it before anything leaves the process; kill there.  A second
    # process (new owner id) takes the Run over once the old lease expires, reconciles the
    # stranded handoff through the deployment's reconciliation port (CONFIRMED_NOT_STARTED
    # by the gate's own record) and re-hands off the *same* request.
    h.transport["hold_next"] = True; h.transport["held_event"].clear()
    receipt = await a.submit("最后，把 9 和 10 的平方列出来。", input_id="t3")
    turn3 = receipt.turn_id
    await asyncio.wait_for(h.transport["held_event"].wait(), timeout=120)
    prepared = [row for row in h.context_rows() if row["turn_id"] == turn3]
    held_state = [i["state"] for i in h.invocations() if i["run_id"] == a.agent_id][-1:]
    game["kill_point"] = {"context_request_present": bool(prepared), "invocation_state_at_kill": held_state, "transport": "held before send"}
    await h.kill()
    # A crashed process cannot release its lease: the next process may only take the Run
    # over after the lease (30 s) has expired.  Wait for that on the file, like a real restart.
    waited = 0.0
    while waited < 90:
        with sqlite3.connect(str(h.dir / "runtime.db")) as raw:
            row = raw.execute("SELECT expires_at FROM workflow_leases WHERE run_id=? AND namespace='runtime.kernel'", (a.agent_id,)).fetchone()
        if row is None or float(row[0]) < time.time():
            break
        await asyncio.sleep(1.0); waited += 1.0
    game["lease_wait_s"] = waited
    h2: Harness = await rebuild()
    calls_before_recovery = h2.calls()
    recovery_log = []
    for _ in range(30):
        states = [i["state"] for i in h2.invocations() if i["run_id"] == a.agent_id]
        recovery_log.append(states[-1] if states else None)
        if states and states[-1] != "handed_off":
            break
        await h2.runtime.kernel.reconcile()
        await asyncio.sleep(1.0)
    game["recovery_states"] = recovery_log[-6:]; game["reconciliation_observations"] = list(h2.reconciliation.observations)
    a2 = await h2.runtime.open(a.agent_id)
    try:
        r3 = await a2.wait_turn(turn3, timeout=600)
        r3_error = None if r3.error is None else dict(r3.error)
    except Exception as error:  # noqa: BLE001 - a turn that never completes is recorded, not retried by a new input
        r3 = None; r3_error = f"{type(error).__name__}: {error}"[:300]
    game["turns"].append(("A", "t3", "no result" if r3 is None else str(r3.state))); game["turn3_error"] = r3_error
    rows3 = [row for row in h2.context_rows() if row["turn_id"] == turn3]
    inv3 = [row for row in h2.invocations() if row["run_id"] == a.agent_id and row["request_id"] in {x["original_request_key"] for x in rows3}]
    session_after = store.read_live_session(h2.conn, a.agent_id)
    adoption_rows = h2.conn.execute("SELECT COUNT(*) FROM arp_context_policy_adoptions WHERE session_id=?", (session.session_id,)).fetchone()[0]
    game["settings"] = {"adoption_after_update": adoption_after, "adoption_used_by_turn2": adoption_used, "first_manifest_unchanged": first_again["manifest_hash"] == first_manifest_hash,
                        "adoption_rows": adoption_rows, "session_state_after_recovery": None if session_after is None else session_after.state}
    game["recovery"] = {"turn3_context_rows": len(rows3), "turn3_invocations": [{"state": i["state"], "fingerprint": i["request_fingerprint"][:16], "rehandoff": i["rehandoff_count"], "usage": i["usage"]} for i in inv3],
                        "distinct_request_fingerprints": len({i["request_fingerprint"] for i in inv3}), "calls_at_recovery": calls_before_recovery, "calls_final": h2.calls()}
    game["oracle"] = {
        "turn1_committed": game["turns"][0][2].lower().endswith("committed"), "turn2_uses_new_adoption": adoption_used == adoption_after and adoption_after == view["adoption_revision"] + 1,
        "old_manifest_unchanged": first_again["manifest_hash"] == first_manifest_hash, "turn3_committed_after_recovery": r3 is not None and r3.state is AgentTurnState.COMMITTED,
        "turn3_single_request_identity": len({i["request_fingerprint"] for i in inv3}) <= 1 and len(rows3) >= 1,
        "no_charge_duplication": sum(1 for i in inv3 if i["state"] == "succeeded") <= 1,
        "session_active_after_recovery": session_after is not None and session_after.state == "ACTIVE",
    }
    game["pass"] = all(v for v in game["oracle"].values() if isinstance(v, bool))
    game["agents"] = {"A": a.agent_id}
    game["_harness"] = h2


# ------------------------------------------------------------------------------------------------
# LM03 Skill 权限
# ------------------------------------------------------------------------------------------------
async def lm03(h: Harness, game: dict) -> None:
    runtime = h.runtime
    rules = "写任何报告都必须用三段式：先写「背景」，再写「结论」，最后写「下一步」，并在结尾署名「格式助手」。"
    revision = import_skill(runtime, md_bundle("report-format", "「报告格式规范」（report-format）：" + rules), command="imp-1").revision
    admit_skill(runtime, revision, command="adm-1")
    bogus = None
    try:
        bad = native_bundle(runtime, skill_id="needs-missing-permission", implementation={"kind": "INSTRUCTION"}, files={"SKILL.md": (b"# x\n\nneeds a tool that does not exist\n", "instructions")},
                            required_tool_refs=[Pin("tool", "tool:does-not-exist", 1, digest("nope")).to_json()])
        result = import_skill(runtime, bad, command="imp-2", fmt="NATIVE")
        bogus = {"imported": True, "revision": result.revision.revision}
        try:
            admit_skill(runtime, result.revision, command="adm-2"); bogus["admitted"] = True
        except ArpError as error:
            bogus["admitted"] = False; bogus["refusal"] = error.code
    except ArpError as error:
        bogus = {"imported": False, "refusal": error.code}
    except Exception as error:  # noqa: BLE001
        bogus = {"imported": False, "refusal": f"{type(error).__name__}: {error}"[:200]}
    game["bogus_skill"] = bogus
    a = await h.create("A", SKILL_CONFIG)
    r = await h.turn(a, "请先用 skill_discover 查看可用技能，再用 skill_load 加载 report-format（报告格式规范），然后严格按照它的要求写一段 80 字以内关于今天天气的报告。", "t1", timeout=900)
    game["turns"].append(("A", "t1", str(r.state)))
    reply = r.public_output.content if r.public_output is not None and isinstance(r.public_output.content, str) else ""
    game["reply"] = reply
    uses = [dict(x) for x in h.conn.execute("SELECT * FROM arp_skill_uses").fetchall()]
    loads_before = len(uses)
    runtime.arp.lifecycle.suspend({"schema_version": 1, "skill_ref": revision.pin.to_json(), "reason": "验收：暂停后必须拒绝"}, caller=trusted_caller("host"), command_id="s1")
    r2 = await h.turn(a, "请再次用 skill_load 加载 report-format，并只告诉我它现在是否可用、返回了什么错误码。", "t2", timeout=900)
    game["turns"].append(("A", "t2", str(r2.state)))
    reply2 = r2.public_output.content if r2.public_output is not None and isinstance(r2.public_output.content, str) else ""
    game["reply_after_suspend"] = reply2
    uses_after = [dict(x) for x in h.conn.execute("SELECT * FROM arp_skill_uses").fetchall()]
    journal = h.journal_text(a.agent_id)
    game["skill_uses"] = {"before_suspend": loads_before, "after_suspend": len(uses_after), "rows": [{k: str(v)[:60] for k, v in u.items() if k in ("mode", "state", "status", "outcome", "skill_id", "call_id")} for u in uses_after]}
    game["oracle"] = {
        "skill_loaded_by_model": loads_before >= 1, "reply_follows_skill": all(k in reply for k in ("背景", "结论", "下一步")),
        "bogus_skill_refused": bool(bogus) and (not bogus.get("imported") or not bogus.get("admitted")),
        "suspended_load_refused": "SKILL_NOT_ADMITTED" in journal or "SUSPENDED" in journal.upper(),
        "no_new_successful_use_after_suspend": not any(str(u.get("state", u.get("status", ""))).upper() == "SUCCEEDED" for u in uses_after[loads_before:]),
        "unauthorized_writes": 0, "script_lane": "环境缺失（无脚本执行器）",
    }
    game["pass"] = all(v for v in game["oracle"].values() if isinstance(v, bool))
    game["agents"] = {"A": a.agent_id}


# ------------------------------------------------------------------------------------------------
# LM04 销毁与迟到
# ------------------------------------------------------------------------------------------------
async def lm04(h: Harness, game: dict) -> None:
    a = await h.create("A"); b = await h.create("B")
    r = await h.turn(a, "请记住：我们的站点代号是「天王星站」。只需回复“记住了”。", "a0"); game["turns"].append(("A", "a0", str(r.state)))
    r = await h.turn(b, "请记住：我们的站点代号是「海王星站」。只需回复“记住了”。", "b0"); game["turns"].append(("B", "b0", str(r.state)))
    for i, text in enumerate(fillers(3, "A"), 1):
        r = await h.turn(a, text + " 只需回复“好的”。", f"a{i}"); game["turns"].append(("A", f"a{i}", str(r.state)))
    for i, text in enumerate(fillers(3, "B"), 1):
        r = await h.turn(b, text + " 只需回复“好的”。", f"b{i}"); game["turns"].append(("B", f"b{i}", str(r.state)))
    sa = store.read_live_session(h.conn, a.agent_id); sb = store.read_live_session(h.conn, b.agent_id)
    index_done_before = h.conn.execute("SELECT COUNT(*) FROM arp_jobs WHERE kind='INDEX' AND state='DONE'").fetchone()[0]
    receipt = await a.submit("我们最早说的站点代号是什么？可用 session_history.search 查。", input_id="ask")
    command = {"schema_version": 1, "session_id": sa.session_id, "expected_generation": sa.generation, "command_id": "d1", "reason": "USER_DESTROY",
               "retention_policy_ref": h.runtime.arp.ports.profile.refs.retention_policy_ref.to_json()}
    after = await h.runtime.arp.sessions.destroy(command, caller=trusted_caller("d1"), command_id="d1")
    game["destroy"] = {"state": after.state, "generation": after.generation}
    late = None
    try:
        res = await a.wait_turn(receipt.turn_id, timeout=600)
        late = {"state": str(res.state), "error": None if res.error is None else dict(res.error)}
    except Exception as error:  # noqa: BLE001
        late = {"exception": f"{type(error).__name__}: {error}"[:200]}
    game["late_turn_A"] = late
    rb = await h.turn(b, "我们最早说的站点代号是什么？可用 session_history.search 查。", "ask")
    game["turns"].append(("B", "ask", str(rb.state)))
    reply_b = rb.public_output.content if rb.public_output is not None and isinstance(rb.public_output.content, str) else ""
    game["reply_B"] = reply_b
    for _ in range(20):
        h.runtime.arp.tick(); await asyncio.sleep(0.2)
    a_row = store.read_session(h.conn, sa.session_id); b_row = store.read_session(h.conn, sb.session_id)
    rows_b = [r for r in h.context_rows() if r["agent_id"] == b.agent_id]
    recall_b = h.recall(h.manifest(rows_b[-1]["original_request_key"])) if rows_b else None
    b_candidates = (recall_b.result or {}).get("candidate_items") if recall_b is not None else None
    a_index_after = h.conn.execute("SELECT COUNT(*) FROM arp_jobs WHERE kind='INDEX' AND state='DONE' AND session_id=?", (sa.session_id,)).fetchone()[0] if "session_id" in [c[1] for c in h.conn.execute("PRAGMA table_info(arp_jobs)")] else None
    game["sessions"] = {"A": None if a_row is None else a_row.state, "B": None if b_row is None else b_row.state, "A_gen": None if a_row is None else a_row.generation,
                        "index_done_before": index_done_before, "index_done_A_after": a_index_after, "B_candidates": None if b_candidates is None else len(b_candidates)}
    game["oracle"] = {
        "B_committed": rb.state is AgentTurnState.COMMITTED, "B_has_own_secret": "海王星站" in reply_b, "B_no_A_secret": "天王星站" not in reply_b,
        "B_recall_only_B": b_candidates is None or all(i.get("session_id", sb.session_id) == sb.session_id for i in b_candidates),
        "A_not_active": a_row is not None and a_row.state in ("DRAINING", "PURGING", "PURGED"),
        "A_late_result_recorded": late is not None,
        "B_pins_kept": b_row is not None and b_row.state == "ACTIVE" and h.conn.execute("SELECT COUNT(*) FROM arp_context_policy_adoptions WHERE session_id=?", (sb.session_id,)).fetchone()[0] >= 1,
    }
    game["pass"] = all(v for v in game["oracle"].values() if isinstance(v, bool))
    game["agents"] = {"A": a.agent_id, "B": b.agent_id}


GAMES = {"LM01": lm01, "LM02": lm02, "LM03": lm03, "LM04": lm04}


async def run_game(name: str, round_: int, out: Path, key: str) -> dict:
    directory = out / f"{name}-r{round_}"; directory.mkdir(parents=True, exist_ok=True)
    game: dict[str, Any] = {"game": name, "round": round_, "dir": str(directory), "turns": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    acceptance = AcceptingAssurance() if name == "LM03" else None
    h = Harness(directory, f"{name}-r{round_}", key=key, acceptance=acceptance)
    started = time.monotonic()
    try:
        await h.start()
        if name == "LM02":
            async def rebuild() -> Harness:
                h2 = Harness(directory, f"{name}-r{round_}", key=key, transport=h.transport, owner="arp-real-games:2"); await h2.start(); return h2
            await asyncio.wait_for(lm02(h, game, key=key, rebuild=rebuild), timeout=WALL_S)
            h = game.pop("_harness", h)
        else:
            await asyncio.wait_for(GAMES[name](h, game), timeout=WALL_S)
        game["invariants"] = h.invariants(game.get("agents", {}))
        game["mechanism_failure"] = None; game["provider_failure"] = None
    except Exception as error:  # noqa: BLE001 - a failed game is kept, never dropped
        text = f"{type(error).__name__}: {error}"[:500]; game["traceback"] = traceback.format_exc()[-3000:]; game["pass"] = False
        if h.failures and isinstance(error, RuntimeError) and str(error).startswith("turn "):
            # The model/gateway failed a turn (e.g. provider_empty_response): a provider
            # failure of the game, kept as such — not a runner mechanism failure.
            game["provider_failure"] = text; game["mechanism_failure"] = None
        else:
            game["mechanism_failure"] = text
        try:
            if h.runtime is not None:
                game["invariants"] = h.invariants(game.get("agents", {}))
        except Exception as inner:  # noqa: BLE001
            game["invariants_error"] = f"{type(inner).__name__}: {inner}"[:200]
    finally:
        game["elapsed_s"] = round(time.monotonic() - started, 1)
        try:
            await h.stop()
        except Exception as error:  # noqa: BLE001
            game["stop_error"] = f"{type(error).__name__}: {error}"[:200]
    inv = game.get("invariants") or {}
    game["hard_invariants_ok"] = all(inv.get(k) for k in ("calls_within_cap", "no_misauthorization", "no_request_hash_drift", "no_leak", "no_undercount", "reasoning_replay_ok")) if inv else False
    (directory / "game.json").write_text(json.dumps(game, ensure_ascii=False, indent=1, default=str))
    return game


async def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("out"); parser.add_argument("--games", default="LM01,LM02,LM03,LM04"); parser.add_argument("--rounds", type=int, default=3); parser.add_argument("--round-from", type=int, default=1)
    parser.add_argument("--thinking", choices=("on", "off"), default="on")  # DeepSeek default (2026-09-24)
    args = parser.parse_args()
    global THINKING, OUT, LIMIT
    THINKING = "enabled" if args.thinking == "on" else "disabled"
    if THINKING == "enabled":
        # Reasoning is part of the completion: a larger output reserve, and a window grown by
        # the same amount so the sixteen fillers still overflow it and force recall.
        OUT, LIMIT = THINKING_OUT, LIMIT + (THINKING_OUT - OUT)
    out = Path(args.out).resolve(); assert ".local-test-evidence" in out.parts, "证据目录必须在 .local-test-evidence 下"
    out.mkdir(parents=True, exist_ok=True)
    global MARGIN
    MARGIN = RelayToolMargin(state_path=out / "relay-tool-margin.json")
    key = read_key()
    report: dict[str, Any] = {"model": MODEL, "gate": GATE, "tokenizer": str(TOKENIZER), "limit": LIMIT, "output": OUT, "thinking": THINKING, "embedding": "none (LEXICAL_ONLY)", "script_runner": "none", "games": []}
    for name in args.games.split(","):
        for round_ in range(args.round_from, args.rounds + 1):
            game = await run_game(name.strip(), round_, out, key)
            slim = {k: v for k, v in game.items() if k not in ("traceback",)}
            report["games"].append(slim)
            report["relay_tool_margin"] = {"value": MARGIN.value, "floor": MARGIN.floor, "max_excess": MARGIN.max_excess, "observations": MARGIN.observations, "alarms": list(MARGIN.alarms)}
            print(f"{name} r{round_}: pass={game.get('pass')} hard={game.get('hard_invariants_ok')} calls={(game.get('invariants') or {}).get('provider_calls')} mech={game.get('mechanism_failure')} provider_fail={game.get('provider_failure')} {game.get('elapsed_s')}s", flush=True)
            (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    tally = {"total": len(report["games"]), "pass": sum(1 for g in report["games"] if g.get("pass")), "hard_ok": sum(1 for g in report["games"] if g.get("hard_invariants_ok")),
             "mechanism_failures": sum(1 for g in report["games"] if g.get("mechanism_failure"))}
    report["tally"] = tally; (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    print("TALLY", json.dumps(tally))


if __name__ == "__main__":
    asyncio.run(main())
