"""保证通道根闸门接缝（产品同形世界，2026-10-03 迁离手插任务 / 尝试 / 产物与改内部属性的旧写法）。

1. 产品部署上建两个任务；主循环跑之前根状态文件没了（磁盘上的文件丢失，外界会发生的事）：主循环
   按名拒绝（ROOT_QUARANTINED），一次模型调用都没有、什么都没派发；文件回来以后两个任务照常完成。
2. 真的离线备份 / 恢复：备份不带根状态文件，恢复出一个新的根身份，恢复目录里没有活的授权。
3. 产品部署在恢复出来的目录上起不来：部署的根安装回调按名拒绝 RESTORED_ROOT_REQUIRES_REAUTHORIZATION
   （现状，见报告"疑似缺陷"：恢复后的隔离管理面在产品部署里够不到）。
4. 不带根安装回调起服务（宿主没绑当前读权限）：只开管理面，诊断为 QUARANTINED，任务列表 / 快照
   被拒，产物读取与恢复后重新授权都按名拒绝 CURRENT_READ_AUTHORITY_UNAVAILABLE；从没起执行池。
5. 闸门层契约：当前读权限由宿主那一侧作为参数交进来（外部的 ACL / 策略服务），走真的重新授权写入
   函数与闸门读检查——同一命令重放得到同一份授权；别的用户 / 别的租户、策略变了、权限过期、时钟
   回拨、授权到期、恢复库缺文件，各自按名拒绝；授权以后仍不许执行。
"""
from _product_seam import EVIDENCE, SDK, count, quick, source_sha256, write_report  # noqa: F401

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.assurance.codec import AssuranceError, fingerprint
from agent_orchestrator.assurance.evidence import ReadItem
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.assurance.root_gate import STATE_FILE, CurrentReadPermission
from agent_orchestrator.deployment.native_pools import NativePools, pool_options
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.assurance_root_commits import reauthorize_restored_read
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.storage.offline_backup import BackupSourceIdentity, backup_offline, restore_offline
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

        backup = backup_offline(cfg, source["profiles"], WorkspaceManager(cfg.workspaces_root),
                                instance_lock_path=cfg.evidence_root / ".instance.lock", destination=base / "bundle",
                                source_identity=BackupSourceIdentity("a" * 40, "b" * 64))
        assert not (base / "bundle" / STATE_FILE).exists()
        restored = restore_offline(base / "bundle", destination=base / "restored",
                                   expected_manifest_sha256=backup["manifest_sha256"])
        assert restored["root_incarnation_id"] != source["root_id"]
        assert not (base / "restored" / STATE_FILE).exists()
        results["managed_backup_restore_new_identity_no_live_grant"] = True

        provider = LayeredScriptedProvider()
        try:
            async with product_world(base / "restored", provider):
                raise AssertionError("product deployment started on a restored root")
        except AssuranceError as error:
            assert error.code == "RESTORED_ROOT_REQUIRES_REAUTHORIZATION", error.code
        assert provider.asked == []
        results["product_deployment_refuses_to_start_on_a_restored_root"] = True

        orchestrator, provider = management_orchestrator(base / "restored")
        async with orchestrator as orch:
            assert orch._assurance_management_only
            api = MissionControlV1(orch, tenant_id=TENANT, principal=USER)
            assert api.assurance_root_diagnostic()["state"] == "QUARANTINED"
            refused(api.missions, "ROOT_QUARANTINED")
            refused(lambda: api.snapshot(mission_id), "ROOT_QUARANTINED")
            refused(lambda: api.artifact_read(artifact.id), "CURRENT_READ_AUTHORITY_UNAVAILABLE")
            gate = orch._assurance_root_gate
            identity = gate.restored_identity()
            ref = AssuranceRef("artifact", Pin(artifact.id, artifact.version, artifact.content_hash))
            command = {"command_id": "grant-1", "root_incarnation_id": identity.root_incarnation_id,
                       "restore_manifest_hash": identity.restore_manifest_hash,
                       "targets": [{"mission_id": mission_id, "ref": ref.to_json(), "purpose": "DISCLOSE"}],
                       "ttl_ms": 20_000}
            refused(lambda: api.reauthorize_restored_read(command), "CURRENT_READ_AUTHORITY_UNAVAILABLE")
            results["quarantine_without_current_authority_refuses_every_read"] = True

            # 5. 闸门层契约：外部的当前 ACL / 策略服务作为参数交进来。
            policy = ["policy-1"]
            allowed = [True]
            horizon = [int(orch.store.now * 1000) + 30_000]

            def authority(principal: Any, tenant: str, mid: str, target: Any, purpose: str) -> CurrentReadPermission:
                if not allowed[0]:
                    raise AssuranceError("HOST_CURRENT_AUTHORITY_REFUSED")
                return CurrentReadPermission(ReadItem("ACCESS", "host-current-acl", fingerprint("acl-1")),
                                             ReadItem("POLICY", "host-current-policy", fingerprint(policy[0])),
                                             horizon[0])

            def grant() -> Any:
                return reauthorize_restored_read(
                    orch.commit, gate, principal=USER, tenant_id=TENANT, command_id="grant-1",
                    root_incarnation_id=identity.root_incarnation_id,
                    restore_manifest_hash=identity.restore_manifest_hash,
                    targets=((mission_id, ref, "DISCLOSE"),), authority=authority, ttl_ms=20_000)

            allowed[0] = False
            refused(grant, "HOST_CURRENT_AUTHORITY_REFUSED")
            allowed[0] = True
            first = grant()
            assert grant() == first
            assert api.assurance_root_diagnostic()["state"] == "READ_ONLY_REAUTHORIZED"
            refused(gate.require_execution)
            refused(api.missions)

            def read(principal: Any = USER, tenant: str = TENANT, now_ms: int | None = None) -> Any:
                now = int(orch.store.now * 1000) if now_ms is None else now_ms
                return gate.require_read(principal=principal, tenant_id=tenant, mission_id=mission_id, ref=ref,
                                         purpose="DISCLOSE", current=authority(principal, tenant, mission_id, ref,
                                                                               "DISCLOSE"), now_ms=now)

            assert read().channel == "ACCESS"
            results["exact_grant_replay_and_read_without_execution_resume"] = True
            refused(lambda: read(principal=Principal("other-user")), "ROOT_READ_NOT_AUTHORIZED")
            refused(lambda: read(tenant="other-tenant"), "ROOT_READ_NOT_AUTHORIZED")
            policy[0] = "changed-policy"
            refused(read, "ROOT_READ_NOT_AUTHORIZED")
            policy[0] = "policy-1"
            wall_high = orch.store.connection.execute(
                "SELECT wall_high_ms FROM assurance_environment_state WHERE singleton=1").fetchone()[0]
            refused(lambda: read(now_ms=wall_high - 1), "TIME_DISCONTINUITY")
            refused(lambda: read(now_ms=horizon[0]), "READ_AUTHORITY_EXPIRED")
            horizon[0] += 3_600_000
            refused(lambda: read(now_ms=wall_high + 25_000), "ROOT_READ_NOT_AUTHORIZED")  # 授权 20 秒到期
            results["other_caller_policy_expiry_and_clock_rollback_refuse"] = True
            execution_db = next(p for p in (base / "restored").glob("*.db") if p.name != "orchestrator.db")
            moved = execution_db.with_suffix(".missing")
            execution_db.rename(moved)
            refused(gate.check_restored_integrity, "RESTORE_DATABASE_INCOMPLETE")
            moved.rename(execution_db)
            results["partial_restored_database_refuses"] = True
            assert provider.asked == []
    write_report("root-gate-seam", {
        "status": "PASS",
        "scope": "product deployment for the native round; real offline backup/restore; management-only "
                 "startup without a bound current authority; gate-level contract with the Host's current "
                 "authority passed as an argument. No model, Host or UI.",
        "results": results, "provider_calls": 0,
        "source_sha256": source_sha256(["assurance/root_gate.py", "orchestrator/assurance_root_commits.py",
                                        "orchestrator/event_handler.py", "api/facade.py",
                                        "storage/offline_backup.py", "deployment/assembly.py"])})


if __name__ == "__main__":
    asyncio.run(main())
