"""保证通道根闸门接缝（产品同形世界）。2026-10-03 起没有离线备份与受管恢复（HTN 补齐 A″，决定⑤）。

1. 产品部署上建两个任务；主循环跑之前根状态文件没了（磁盘上的文件丢失，外界会发生的事）：主循环
   按名拒绝（ROOT_QUARANTINED），一次模型调用都没有、什么都没派发；文件回来以后两个任务照常完成。
2. 根状态文件再次丢失后冷启动（不带根安装回调）：只开管理面，诊断为 QUARANTINED，任务列表 / 快照
   被拒，产物读取签不出披露证书（没有装保证通道的有效性组件）按名拒绝；从没起执行池。
3. 原生根上的产物读取（推后第 2 批 A03 起走使用证书签发方，原 ``root_gate.require_read`` 已删）：本人
   读得到并留一张 DISCLOSE 证书；别的租户看不到这件任务、别的主体、读权限过期各自按名拒绝，拒绝不留证书。
"""
from _product_seam import EVIDENCE, SDK, count, quick, source_sha256, write_report  # noqa: F401

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.assurance.root_gate import STATE_FILE
from agent_orchestrator.deployment.native_pools import NativePools, pool_options
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from agent_orchestrator.testing.word_counter import FixtureWordCounter

USER = Principal("product-world-user")


def refused(call: Any, code: str | None = None, reason: str | None = None) -> str:
    try:
        call()
    except (AssuranceError, FacadeError) as error:
        if code is not None:
            assert error.code == code, (error.code, code)
        if reason is not None:
            assert reason in str(error), (str(error), reason)
        return str(error.code)
    raise AssertionError("expected refusal")


def native_reads(world: Any, artifact: Any, results: dict[str, Any]) -> None:
    """原生根上的产物读取：同一个使用证书签发方（DISCLOSE），交出才留证书。"""
    store = world.store

    def certificates() -> int:
        return count(store, "SELECT count(*) FROM assurance_use_certificates WHERE purpose='DISCLOSE' "
                            "AND consumer_kind='NATIVE_READ'")

    read = world.control.artifact_read(artifact.id)
    assert read["content_hash"] == artifact.content_hash and read["content"], read
    assert certificates() == 1
    other_tenant = MissionControlV1(world.loop, tenant_id="other-tenant", principal=USER)
    refused(lambda: other_tenant.artifact_read(artifact.id), "not_found")
    stranger = MissionControlV1(world.loop, tenant_id=TENANT, principal=Principal("someone-else"))
    refused(lambda: stranger.artifact_read(artifact.id), "USE_EVIDENCE_NOT_CURRENT", "ROOT_READ_NOT_AUTHORIZED")
    authority = world.loop.commit._assurance_validity.authority
    ttl = authority.ttl_ms
    authority.ttl_ms = -1  # 当前读权限一发出就已过期
    try:
        refused(lambda: world.control.artifact_read(artifact.id), "USE_EVIDENCE_NOT_CURRENT", "CHECK_USE_EXPIRED")
    finally:
        authority.ttl_ms = ttl
    assert certificates() == 1
    results["native_root_read_checks_tenant_principal_and_expiry"] = True


async def native_round(base: Path, results: dict[str, Any]) -> dict[str, Any]:
    provider = LayeredScriptedProvider()
    async with product_world(base / "source", provider) as world:
        first = world.create({"goal": "写 NOTES.md", "success_criteria": ["file:NOTES.md"], "idempotency_key": "r1"})
        second = world.create({"goal": "写 B.md", "success_criteria": ["file:B.md"], "idempotency_key": "r2"})
        root = world.loop._config.evidence_root
        state = root / STATE_FILE
        saved = state.read_bytes()
        state.unlink()
        try:
            await asyncio.wait_for(world.loop.run(), timeout=10)
        except AssuranceError as error:
            assert error.code == "ROOT_QUARANTINED", error.code
        else:
            raise AssertionError("main loop ran without a root")
        assert provider.asked == [], provider.asked
        assert count(world.store, "SELECT count(*) FROM attempts") == 0
        state.write_bytes(saved)
        state.chmod(0o600)
        for created in (first, second):
            mission = await world.run_until_settled(created["mission_id"])
            assert str(mission.status.value) == "COMPLETED", mission.final_report
        [artifact] = [a for a in world.store.list_mission_artifacts(first["mission_id"]) if a.path == "NOTES.md"]
        results["missing_root_state_refuses_the_main_loop_before_any_model_call"] = True
        native_reads(world, artifact, results)
        return {"config": world.loop._config, "profiles": world.loop._profiles, "mission_id": first["mission_id"],
                "artifact": artifact, "root_id": world.loop._assurance_root_gate.require_execution().root_incarnation_id}


def management_orchestrator(root: Path) -> Any:
    """宿主不带根安装回调地起服务（产品那份池子配置照旧）；执行池一起就算失败。"""
    cfg = OrchestratorConfig(evidence_root=root, model="agent-model")
    counter = FixtureWordCounter()
    provider = LayeredScriptedProvider()
    native = NativePools(tenant_id=TENANT, principal_id=USER.principal_id, allowed_tools=DEFAULT_TOOLS,
                         meter_factory=counter.meter_factory)
    options = pool_options(cfg, native=native, provider=provider, counter=counter, provider_kind="fixtures")

    def never(_: Any) -> None:
        raise AssertionError("runtime started on a quarantined root")

    return Orchestrator(cfg, provider, startup_assembly=never, **options), provider


async def main() -> None:
    quick()
    results: dict[str, Any] = {}
    with TemporaryDirectory(prefix="assurance-root-seam-") as temp:
        base = Path(temp).resolve()
        source = await native_round(base, results)
        cfg, artifact, mission_id = source["config"], source["artifact"], source["mission_id"]

        root = cfg.evidence_root
        state = root / STATE_FILE
        state.unlink()
        orchestrator, provider = management_orchestrator(root)
        async with orchestrator as orch:
            assert orch._assurance_management_only
            api = MissionControlV1(orch, tenant_id=TENANT, principal=USER)
            assert api.assurance_root_diagnostic()["state"] == "QUARANTINED"
            refused(api.missions, "ROOT_QUARANTINED")
            refused(lambda: api.snapshot(mission_id), "ROOT_QUARANTINED")
            refused(lambda: api.artifact_read(artifact.id), "USE_EVIDENCE_NOT_CURRENT", "USE_CERTIFICATE_REQUIRED")
            results["quarantine_without_current_authority_refuses_every_read"] = True

            assert provider.asked == []
    write_report("root-gate-seam", {
        "status": "PASS",
        "scope": "product deployment for the native round and its native artifact reads through the use "
                 "certificate issuer; management-only startup without a bound current authority. No model, "
                 "Host or UI.",
        "results": results, "provider_calls": 0,
        "source_sha256": source_sha256(["assurance/root_gate.py", "orchestrator/assurance_root_commits.py",
                                        "orchestrator/event_handler.py", "api/facade.py",
                                        "orchestrator/assurance_point_use.py",
                                        "deployment/assembly.py"])})


if __name__ == "__main__":
    asyncio.run(main())
