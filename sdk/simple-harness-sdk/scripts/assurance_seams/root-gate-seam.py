"""保证通道根闸门接缝（产品同形世界）。2026-10-03 起没有离线备份与受管恢复（HTN 补齐 A″，决定⑤）。

1. 产品部署上建两个任务；主循环跑之前根状态文件没了（磁盘上的文件丢失，外界会发生的事）：主循环
   按名拒绝（ROOT_QUARANTINED），一次模型调用都没有、什么都没派发；文件回来以后两个任务照常完成。
2. 根状态文件再次丢失后冷启动（不带根安装回调）：只开管理面，诊断为 QUARANTINED，任务列表 / 快照
   被拒，产物读取按名拒绝 CURRENT_READ_AUTHORITY_UNAVAILABLE；从没起执行池。
3. 闸门层契约（原生根）：当前读权限由宿主那一侧作为参数交进来；本租户读得到，别的租户、权限过期
   各自按名拒绝。
"""
from _product_seam import EVIDENCE, SDK, count, quick, source_sha256, write_report  # noqa: F401

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.assurance.codec import AssuranceError, fingerprint
from agent_orchestrator.assurance.evidence import ReadItem
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.assurance.root_gate import STATE_FILE, CurrentReadPermission
from agent_orchestrator.deployment.native_pools import NativePools, pool_options
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from agent_orchestrator.testing.word_counter import FixtureWordCounter

USER = Principal("product-world-user")


def refused(call: Any, code: str | None = None) -> str:
    try:
        call()
    except (AssuranceError, FacadeError) as error:
        if code is not None:
            assert error.code == code, (error.code, code)
        return str(error.code)
    raise AssertionError("expected refusal")


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
        return {"config": world.loop._config, "profiles": world.loop._profiles, "mission_id": first["mission_id"],
                "artifact": artifact, "root_id": world.loop._assurance_root_gate.require_execution().root_incarnation_id}


def management_orchestrator(root: Path) -> Any:
    """宿主不带根安装回调地起服务（产品那份池子配置照旧）；执行池一起就算失败。"""
    cfg = OrchestratorConfig(evidence_root=root, model="agent-model", price_table=None)
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
        saved = state.read_bytes()
        state.unlink()
        orchestrator, provider = management_orchestrator(root)
        async with orchestrator as orch:
            assert orch._assurance_management_only
            api = MissionControlV1(orch, tenant_id=TENANT, principal=USER)
            assert api.assurance_root_diagnostic()["state"] == "QUARANTINED"
            refused(api.missions, "ROOT_QUARANTINED")
            refused(lambda: api.snapshot(mission_id), "ROOT_QUARANTINED")
            refused(lambda: api.artifact_read(artifact.id), "CURRENT_READ_AUTHORITY_UNAVAILABLE")
            results["quarantine_without_current_authority_refuses_every_read"] = True

            # 3. 闸门层契约（原生根）：外部的当前 ACL / 策略服务作为参数交进来。
            state.write_bytes(saved)
            state.chmod(0o600)
            gate = orch._assurance_root_gate
            ref = AssuranceRef("artifact", Pin(artifact.id, artifact.version, artifact.content_hash))
            horizon = int(orch.store.now * 1000) + 30_000
            current = CurrentReadPermission(ReadItem("ACCESS", "host-current-acl", fingerprint("acl-1")),
                                            ReadItem("POLICY", "host-current-policy", fingerprint("policy-1")),
                                            horizon)

            def read(tenant: str = TENANT, now_ms: int | None = None) -> Any:
                now = int(orch.store.now * 1000) if now_ms is None else now_ms
                return gate.require_read(principal=USER, tenant_id=tenant, mission_id=mission_id, ref=ref,
                                         purpose="DISCLOSE", current=current, now_ms=now)

            assert read().channel == "ACCESS"
            refused(lambda: read(tenant="other-tenant"), "ROOT_READ_NOT_AUTHORIZED")
            refused(lambda: read(now_ms=horizon), "READ_AUTHORITY_EXPIRED")
            results["native_root_read_checks_tenant_and_expiry"] = True
            assert provider.asked == []
    write_report("root-gate-seam", {
        "status": "PASS",
        "scope": "product deployment for the native round; management-only startup without a bound "
                 "current authority; gate-level contract with the Host's current authority passed as an "
                 "argument. No model, Host or UI.",
        "results": results, "provider_calls": 0,
        "source_sha256": source_sha256(["assurance/root_gate.py", "orchestrator/assurance_root_commits.py",
                                        "orchestrator/event_handler.py", "api/facade.py",
                                        "deployment/assembly.py"])})


if __name__ == "__main__":
    asyncio.run(main())
