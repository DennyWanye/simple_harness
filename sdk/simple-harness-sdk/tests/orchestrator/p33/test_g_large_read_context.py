# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Oracle before implementation: large reads are a frozen runtime capability.

An 18KB-class original document must reach the actual Provider in 2–3 reads,
without preview substitution, loss of CRLF/Unicode, or larger total input budget.
Both the full wire byte bound and the configured tokenizer's TOOL bound apply.
Cold restart cannot substitute a new policy/tokenizer/schema for an existing pool.
These are scripted Provider/context controls, never a claim about model quality.

HTN 补齐阶段 A′：执行池只有原生执行池一种。
* "真实执行者分 2～3 次读完原文"改在产品同形世界里跑主循环：任务带一份来源（建任务时登记），
  脚本化执行者在真实原生执行池里逐页读、再写交付文件；执行池的分词器就是部署的计数器，所以
  原来按分词器分的两档合成一条。
* 其余用例的执行池换成产品同形的原生执行池（``NativePools.assembly``），只装配、不建任务。
* "没有上下文策略的旧执行池"产品造不出来（每个原生执行池都带上下文策略）：
  ``test_legacy_runtime_cannot_be_silently_upgraded`` 与身份用例的 ``legacy`` 一档删除。
"""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import pytest
from fixtures_provider import MODEL, RoleScriptedProvider
from test_g_workspace_paging import PATH, _message, _read, _wire

from agent_orchestrator.deployment.native_pools import NativePools
from agent_orchestrator.runtime.assembly import (
    OrchestratorConfig,
    assemble_orchestrator_runtime,
    resolve_profile_context_policy,
)
from agent_orchestrator.runtime.model_router import RuntimeProfile
from agent_orchestrator.runtime.tool_gateway import (
    CRITIC_TOOLS,
    WorkspaceBinding,
)
from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, worker_reply
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from simple_harness.agents.context.budget import ContextPolicy
from simple_harness.agents.context.tokenizer import (
    UpperBoundTokenizer,
    count_message,
)

TEXT = ("# 完整来源\r\n" + "条件不变😀 abcdefghijklmnop\r\n" * 440
        + "| 结论 | 限制 |\r\n| --- | --- |\r\n| 可用 | 不允许对外发布 |\r\n")
POLICY = ContextPolicy(max_tool_result_tokens=16384, render_slack_tokens=0)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _profile(provider, *, tokenizer=None, policy=POLICY):
    """产品同形的原生执行池；每次调用是一份新部署（重启）。

    原生执行池的分词器就是部署的计量器（ARP 要求计量绑定包着执行池的分词器），默认是测试
    计数器；``tokenizer`` 换的是部署计量用的那一个。"""

    counter = tokenizer or FixtureWordCounter()
    native = NativePools(tenant_id="tenant-large-read", principal_id="large-read-user",
                         allowed_tools=DEFAULT_TOOLS, meter_factory=FixtureWordCounter().meter_factory)
    return RuntimeProfile("default", provider, MODEL, context_policy=policy, tokenizer=counter,
                          native_plane=native.assembly("default", tokens=262_144, counter=counter))


def _bind(assembled, profile, *, content=TEXT):
    workspace = assembled.workspaces.create("read-attempt", seed={})
    path = workspace.resolve(PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    binding = WorkspaceBinding(
        "read-attempt", "work", False, CRITIC_TOOLS,
        untrusted_sources=("sources/",),
        context_policy=profile.context_policy, tokenizer=profile.tokenizer,
    )
    return binding


def _tool_results(request):
    return [json.loads(m.content) for m in request.messages if "tool" in str(m.role).lower()]


def test_large_source_reaches_the_actual_worker_in_two_or_three_reads(tmp_path):
    seen: list[list[dict]] = []

    def worker(request):
        pages = [r["value"] for r in _tool_results(request)
                 if isinstance(r.get("value"), dict) and "sha256" in r["value"]]
        if not pages:
            return "workspace_read_file", {"path": PATH}
        if pages[-1]["next_offset"] is not None:
            return "workspace_read_file", {
                "path": PATH, "offset": pages[-1]["next_offset"], "expected_sha256": pages[-1]["sha256"]}
        seen.append(pages)
        # 读完以后照常写交付文件、交结果（读页结果不算写出的文件）
        kept = tuple(m for m in request.messages
                     if not ("tool" in str(m.role).lower() and '"sha256"' in str(m.content)))
        return worker_reply(replace(request, messages=kept))

    async def case():
        provider = LayeredScriptedProvider(worker=worker)
        async with product_world(tmp_path / "root", provider) as world:
            body = {"mission": {"goal": "完整读完来源 sources/条件.md，写 NOTES.md 摘要", "success_criteria": ["file:NOTES.md"],
                                "idempotency_key": "large-read", "budget": {"max_tokens": 8_000_000, "max_attempts": 12}},
                    "sources": [{"path": PATH, "content": TEXT, "kind": "markdown"}]}
            mission_id = world.deployment.create_mission_with_sources(world.loop, world.control, body)["mission_id"]
            mission = await world.run_until_settled(mission_id)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            return world.loop.assembled.pools

    pools = asyncio.run(case())
    assert seen, "执行者没有读完来源"
    pages = seen[0]
    assert 2 <= len(pages) <= 3
    offset = 0
    for page in pages:
        assert page["trust"] == "untrusted_external" and not page.get("truncated")
        assert page["offset"] == offset and page["sha256"] == sha256(TEXT.encode()).hexdigest()
        offset += len(page["content"])
    assert "".join(p["content"] for p in pages).encode() == TEXT.encode()
    # 用的就是部署的原生执行池（带上下文策略与部署计数器）
    assert pools and all(pool.profile.context_policy is not None for pool in pools.values())


class ByteTokenizer:
    fingerprint = "test:utf8-byte-counter:v1"
    count_mode = "EXACT"

    def count_text(self, text):
        return len(text.encode("utf-8"))


# 原生执行池只接受经认证的部署计量器（带 count_mode）；原来的 UpperBound 一档没有产品同形的对应，
# 留下按字节计数、比整串字节上限更紧的这一档。
@pytest.mark.parametrize("tokenizer", [ByteTokenizer()])
def test_full_wire_and_actual_tokenizer_both_bound_pages(tmp_path, tokenizer):
    async def run():
        profile = _profile(RoleScriptedProvider({}), tokenizer=tokenizer,
                           policy=replace(POLICY, max_tool_result_tokens=1100))
        assembled = assemble_orchestrator_runtime(
            OrchestratorConfig(evidence_root=tmp_path), profiles={"default": profile},
        )
        assembled.gateway.bind("agent", _bind(assembled, profile, content='"\\😀\r\n' * 1500))
        page = await _read(assembled.gateway, max_chars=8192)
        assert page.outcome.value == "succeeded"
        assert page.value["next_offset"] > 0
        assert len(_wire(page).encode()) <= 32768
        assert count_message(tokenizer, _message(page)) <= 1100
    asyncio.run(run())


def test_large_mode_small_shape_and_changed_file_still_refuse(tmp_path):
    async def run():
        profile = _profile(RoleScriptedProvider({}))
        assembled = assemble_orchestrator_runtime(
            OrchestratorConfig(evidence_root=tmp_path), profiles={"default": profile},
        )
        assembled.gateway.bind("agent", _bind(assembled, profile, content="短句\r\n"))
        small = await _read(assembled.gateway)
        assert set(small.value) == {"path", "content", "trust", "notice"}
        first = await _read(assembled.gateway, max_chars=1)
        assembled.workspaces.get("read-attempt").resolve(PATH).write_bytes(b"changed")
        changed = await _read(assembled.gateway, offset=first.value["next_offset"],
                              expected_sha256=first.value["sha256"])
        assert changed.outcome.value != "succeeded" and changed.error_code == "workspace_error"
    asyncio.run(run())


async def _open_and_close(assembled):
    async with assembled:
        pass


@pytest.mark.parametrize("change", ["policy", "tokenizer"])
def test_existing_pool_cannot_silently_change_frozen_context_identity(tmp_path, change):
    provider = RoleScriptedProvider({})
    config = OrchestratorConfig(evidence_root=tmp_path)
    profile = _profile(provider)
    # A real runtime owns the pool; its immutable identity must also survive an
    # interruption before the first Provider request or context selection exists.
    asyncio.run(_open_and_close(assemble_orchestrator_runtime(config, profiles={"default": profile})))
    same = assemble_orchestrator_runtime(config, profiles={"default": _profile(provider)})
    assert same.pools["default"].profile.context_snapshot() == profile.context_snapshot()
    asyncio.run(_open_and_close(same))
    if change == "policy":
        other = _profile(provider, policy=replace(POLICY, max_input_tokens=30000))
    else:
        other = _profile(provider, tokenizer=ByteTokenizer())
    with pytest.raises(ValueError, match="context.*identity"):
        assemble_orchestrator_runtime(config, profiles={"default": other})


def test_profile_snapshot_is_detached_and_default_tokenizer_is_honest():
    profile = RuntimeProfile("default", RoleScriptedProvider({}), MODEL, context_policy=POLICY)
    first = profile.context_snapshot()
    assert first["tokenizer_fingerprint"] == UpperBoundTokenizer.fingerprint
    assert first["policy"]["max_input_tokens"] == 32768
    first["policy"]["max_input_tokens"] = 1
    assert profile.context_snapshot()["policy"]["max_input_tokens"] == 32768


def test_tokenizer_without_explicit_policy_is_rejected():
    with pytest.raises(ValueError, match="context_policy"):
        RuntimeProfile("default", RoleScriptedProvider({}), MODEL, tokenizer=ByteTokenizer())


def test_host_readonly_resolver_preserves_old_pool_and_enables_fresh_pool(tmp_path):
    config = OrchestratorConfig(evidence_root=tmp_path / "new")
    assert resolve_profile_context_policy(config) == POLICY
    assert not config.evidence_root.exists()  # resolving is read-only, including fresh roots
    config.evidence_root.mkdir()
    config.execution_db.touch()
    assert resolve_profile_context_policy(config) is None


def test_host_resolver_restores_exact_stored_policy_and_rejects_bad_identity(tmp_path):
    config = OrchestratorConfig(evidence_root=tmp_path)
    policy = replace(POLICY, max_input_tokens=31000)
    counter = FixtureWordCounter()  # 部署的计量器：产品的 pool_options 也把它交给解析器
    asyncio.run(_open_and_close(assemble_orchestrator_runtime(
        config, profiles={"default": _profile(RoleScriptedProvider({}), policy=policy)})))
    assert resolve_profile_context_policy(config, tokenizer=counter) == policy
    marker = config.execution_db.with_name(config.execution_db.name + ".context.json")
    value = json.loads(marker.read_text())
    value["policy"]["max_tool_result_tokens"] = 20000
    marker.write_text(json.dumps(value))  # real durable mutation, not a mutable-copy attack
    assert json.loads(marker.read_text())["policy"]["max_tool_result_tokens"] == 20000
    with pytest.raises(ValueError, match="context identity"):
        resolve_profile_context_policy(config, tokenizer=counter)


def test_host_explicit_tokenizer_must_match_persisted_identity(tmp_path):
    config = OrchestratorConfig(evidence_root=tmp_path)
    counter = ByteTokenizer()
    assert resolve_profile_context_policy(config, tokenizer=counter) == POLICY
    asyncio.run(_open_and_close(assemble_orchestrator_runtime(
        config, profiles={"default": _profile(RoleScriptedProvider({}), tokenizer=counter)})))
    assert resolve_profile_context_policy(config, tokenizer=ByteTokenizer()) == POLICY
    with pytest.raises(ValueError, match="context identity"):
        resolve_profile_context_policy(config)
    with pytest.raises(ValueError, match="context identity"):
        resolve_profile_context_policy(config, tokenizer=FixtureWordCounter())


def test_actual_orchestrator_freezes_context_before_dispatch_and_refuses_substitution(tmp_path):
    # 删旧平面模式第三刀：规划请求改在真实主循环的分层世界里建（平面规划请求已删）。
    # HTN 补齐阶段 A′：任务建在产品同形部署上（h1i_seed），执行池是产品的原生执行池。
    import sys
    from pathlib import Path

    full_target = Path(__file__).resolve().parents[1] / "full_target"
    if str(full_target) not in sys.path:
        sys.path.insert(0, str(full_target))
    from h1i_seed import CONFIG, seeded

    from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
    from agent_orchestrator.contracts import ContractError
    from agent_orchestrator.testing.product_world import product_world

    async def run():
        async with seeded(tmp_path, key="context-freeze") as (orch, mission, _, _, _, product):
            provider = product.provider
            intent = await orch._create_planner_intent(mission.id, ordinal=1)
            PlanningAuthorizationApi(orch.store, tenant_id=mission.tenant_id,
                                     principal=product.deployment.principal).issue(
                mission.id, command_id="grant-" + intent.intent_id, request_id=intent.intent_id)
            frozen = orch.store.get_intent(intent.intent_id)
            profile = orch._profiles[frozen.config["runtime_profile_id"]]
            assert frozen.config.get("runtime_context") == profile.context_snapshot()
            original_hash = frozen.input_hash
            wrong = dict(frozen.config)
            wrong["runtime_context"] = {**(profile.context_snapshot() or {}), "fingerprint": "0" * 64}
            with pytest.raises(ContractError, match="context identity"):
                await orch._dispatch(replace(frozen, config=wrong))
            assert provider.calls == 0
            assert orch.store.get_intent(intent.intent_id).input_hash == original_hash
        # Real closed-library recovery of the pending intent, with no rewrite.
        async with product_world(tmp_path / "root", RoleScriptedProvider({}), auto=False, **CONFIG) as reopened:
            restored = reopened.store.get_intent(intent.intent_id)
            assert restored.config == frozen.config and restored.input_hash == original_hash
            assert reopened.provider.calls == 0
    asyncio.run(run())
