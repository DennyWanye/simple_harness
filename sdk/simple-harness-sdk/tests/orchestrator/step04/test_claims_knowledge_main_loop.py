# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""声明与知识在产品主循环里的样子（HTN 补齐阶段 A′：step04 与 gap_phase1 知识族合并重写）。

一次产品同形的任务（建任务时绑定执行图、保证通道、原生执行池；只有模型回复是脚本）：两步链
write → continue。用户的第一条要求是 ``pytest:tests/probe/test_impl_a.py``，write 写出这个测试文件，
验收时核验器真的跑了它；continue 读上游交付、写 NOTES.md。执行者结果信封里的 claims、
used_knowledge、contradicts、supersedes 都是"模型写的"，是唯一的替身。另有一处外界事件：write
的结果入库后、派发结清前进程崩溃（存储层的崩溃点），同一回合的结果被再收一次。

产品实际行为（与旧夹具的差别，见各段注释）：
* 执行者自己的声明永远到不了 VERIFIED：引用本次跑过并通过的检查最多是 SUPPORTED；进知识库的只有
  系统写的"测试观测"（``test_observation``，范围钉在本次结果、尝试、产物哈希和执行回执上）。
* 产品没有任何一条路把知识标成"已被取代"：模型的 ``supersedes`` 一律被拒（只有 VERIFIED 的声明才能
  取代，而执行者声明到不了 VERIFIED），知识照旧是现行的。

迁移来源（旧用例都建在已退役的 ``knowledge_helpers.two_leaf_service`` 上）：
``gap_phase1/test_code_knowledge.py`` 的 k01/k02/k03/冻结档案/已验收不可改/工具引用；
``gap_phase1/test_context_consumption.py`` 的 k04/k06；``gap_phase1/test_result_output_contract.py``；
``step04/test_artifact_versions.py``、``test_blackboard.py``、``test_disputes_on_leaf_steps.py``；
``step04/test_retrieval_context.py`` 的摘要条；``step04/test_step04_review_round1.py`` 的取代条。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

_FULL_TARGET = Path(__file__).resolve().parents[1] / "full_target"
for _extra in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import chain_planner, enabled_world  # noqa: E402

from agent_orchestrator.context.knowledge_tools import read_knowledge_tool  # noqa: E402
from agent_orchestrator.orchestrator.assurance_point_use import knowledge_handover  # noqa: E402
from agent_orchestrator.contracts import ClaimStatus, ResultEnvelope  # noqa: E402
from agent_orchestrator.memory.code_observations import scoped_test_observations  # noqa: E402
from agent_orchestrator.observability.lineage import lineage  # noqa: E402
from agent_orchestrator.storage.store import InjectedCrash  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.verification.conflicts import mission_disputes  # noqa: E402

PROBE = "tests/probe/test_impl_a.py"
NOTES = "NOTES.md"
KEY = "impl_a.empty_input"
GOAL = "核对 impl_a 对空输入的行为：pytest target tests/probe/test_impl_a.py 要通过，结论写进 NOTES.md"
CRITERIA = ("pytest:" + PROBE, "file:" + NOTES)
TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}

#: write 这一步的声明，按信封里的顺序（claim-1 …）。
WRITE_CLAIMS = (
    {"content": "impl_a 对空输入抛 ValueError", "key": KEY, "stance": "refutes", "evidence": ["pytest:" + PROBE]},
    {"content": "那次读文件证明了实现", "evidence": ["tool-run:missing"]},
    {"content": "沿用一条不存在的知识", "evidence": ["knowledge:missing"]},
    {"content": "另一个测试也过了", "evidence": ["pytest:tests/other_test.py"]},
    {"content": "所有备份在任何硬件故障后都能恢复", "evidence": ["pytest:" + PROBE]},
)


def _tool_results(request: Any, name: str) -> list[Any]:
    return [m for m in request.messages if "tool" in str(m.role).lower() and m.name == name]


class _Worker:
    """执行者模型的脚本回复：先逐个写文件，再交结果信封；记下每一步收到的任务包。"""

    def __init__(self) -> None:
        self.packages: dict[str, list[dict[str, Any]]] = {"write": [], "continue": []}

    def __call__(self, request: Any) -> Any:
        package = package_of(request)
        contract = package.get("task_contract", {})
        outputs = list(contract.get("outputs") or [])
        step = "continue" if NOTES in outputs else "write"
        self.packages[step].append(package)
        declared = package.get("declared_output_ports") or {}
        ports = [item["port"] for item in declared.get("ports", ()) if item.get("required", True)]
        files = [PROBE, *[o for o in outputs if o != PROBE]] if step == "write" else [NOTES]
        written = len(_tool_results(request, "workspace_write_file"))
        if written < len(files):
            body = "def test_empty_input():\n    assert True\n" if files[written] == PROBE else "# 结论\n"
            return ("workspace_write_file", {"path": files[written], "content": body})
        if step == "write":
            claims = [{"confidence": 0.8, **item} for item in WRITE_CLAIMS]
            used: list[str] = []
        else:
            # 下游执行者看到的已核实知识：它引用、反驳、并请求取代这一条。
            known = (package.get("verified_knowledge") or [{}])[0].get("id", "unknown")
            claims = [
                {"content": "那条测试观测不成立", "confidence": 0.8, "contradicts": [known], "evidence": [NOTES]},
                {"content": "impl_a 对空输入返回 {}", "confidence": 0.8, "key": KEY, "stance": "affirms",
                 "evidence": [NOTES]},
                {"content": "impl_a 对空输入不抛错", "confidence": 0.8, "key": KEY, "stance": "affirms"},
                {"content": "沿用上游的测试观测", "confidence": 0.8, "evidence": ["knowledge:" + known]},
                {"content": "新的观测取代旧的", "confidence": 0.8, "key": "probe.result", "supersedes": known,
                 "evidence": [NOTES]},
            ]
            # 引用写成"编号@版本"（上下文里每条已核实知识自带 ref）
            used = [(package.get("verified_knowledge") or [{}])[0].get("ref", "unknown")]
        envelope = {
            "task_id": contract.get("task_id", ""), "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
            "outcome": "candidate", "summary": f"{step} 完成", "claims": claims, "evidence": files,
            "artifacts": files, "outputs": {port: files[0] for port in ports[:1]},
            "proposed_tasks": [], "used_knowledge": used, "risks": [], "cost": {},
        }
        return "<result_envelope>" + json.dumps(envelope, ensure_ascii=False) + "</result_envelope>"


def _refusal(call: Any) -> str:
    try:
        call()
    except Exception as error:  # noqa: BLE001 - the refusal text is the observation
        return f"{type(error).__name__}: {error}"
    return "ACCEPTED"


async def _run(tmp_path: Path) -> dict[str, Any]:
    worker = _Worker()
    async with enabled_world(tmp_path, key="knowledge-main-loop", planner=chain_planner, worker=worker,
                             goal=GOAL, criteria=CRITERIA) as world:
        store, mission_id, commit = world.store, world.mission.id, world.loop.commit
        # 外界事件：第一份结果入库后、派发结清前进程崩溃；同一回合的结果随后被再收一次。
        store.arm("after_result_submitted:attempt")
        crashes = 0
        async with asyncio.timeout(60):
            while str(store.get_mission(mission_id).status.value) not in TERMINAL:
                try:
                    await world.step()
                except InjectedCrash:
                    crashes += 1
        mission = store.get_mission(mission_id)
        tasks = {t.id: t for t in store.list_tasks(mission_id)}
        claims = store.list_mission_claims(mission_id)
        knowledge = store.list_knowledge(mission_id)
        results = {row[0]: row[1] for row in store.connection.execute(
            "SELECT result_id, task_id FROM results WHERE mission_id=?", (mission_id,))}
        write_task = next(c.source_task for c in claims if c.key == KEY and c.stance == "refutes")
        write_result = next(r for r, t in results.items() if t == write_task)
        stored_write = store.get_result(write_result)
        write_artifacts = [store.get_artifact(a) for a in stored_write.artifacts]
        layers = [dict(item) for item in store.list_verifications(write_result, requirements_revision=1)]
        artifacts = [(a.path, a.version, a.task_id) for a in store.list_mission_artifacts(mission_id)
                     if not a.path.startswith(".")]

        # 产物谱系：同路径同版本的第二行被表结构拒（零写入）。
        probe_row = next(a for a in store.list_mission_artifacts(mission_id) if a.path == PROBE)
        duplicate_row = _refusal(lambda: store.connection.execute(
            "INSERT INTO artifacts(artifact_id,mission_id,task_id,attempt_id,path,content_hash,version,json,created_at)"
            " VALUES ('dup', ?, ?, ?, ?, ?, 1, '{}', 1.0)",
            (mission_id, probe_row.task_id, probe_row.attempt_id, PROBE, "b" * 64)))

        # 已验收结果的核验记录不可再写。
        immutable = _refusal(lambda: commit.record_verification_layer(
            write_result, layer="code_test", status="PASS", detail={}))

        # 系统观测只认本次的执行回执：把真实核验层的范围改一处，观测就不成立。
        def observed(mutate: str | None) -> int:
            layer = json.loads(json.dumps(next(item for item in layers if item["layer"] == "code_test")))
            detail = layer["detail"]
            scope = detail["observation_scope"]
            if mutate == "hash":
                scope["artifact_hashes"] = {PROBE: "0" * 64}
            elif mutate in {"result", "attempt"}:
                scope[mutate + "_id"] = "old-version"
            elif mutate == "receipt":
                detail["runs"] = [{k: v for k, v in run.items() if k != "receipt"} for run in detail["runs"]]
            elif mutate == "unrecorded":
                layer = {**layer, "layer": "critic_review"}
            return len(scoped_test_observations(stored_write.envelope, write_artifacts, [layer], store.now,
                                                tasks[write_task]))

        observations = {m: observed(m) for m in (None, "hash", "result", "attempt", "receipt", "unrecorded")}

        statuses_before = {c.id: c.status for c in claims}
        listed_once = read_knowledge_tool(store, mission_id, "knowledge_list", {})
        listed_twice = read_knowledge_tool(store, mission_id, "knowledge_list", {})
        statuses_after = {c.id: c.status for c in store.list_mission_claims(mission_id)}
        knowledge_id = knowledge[0].id if knowledge else ""
        facts = {
            "mission": mission, "crashes": crashes, "fired": list(store.fired), "tasks": tasks,
            "claims": {c.id: c for c in claims}, "knowledge": knowledge, "results": results,
            "write_task": write_task, "write_result": write_result, "write_artifacts": write_artifacts,
            "artifacts": artifacts, "duplicate_row": duplicate_row,
            "artifact_rows": store.connection.execute(
                "SELECT COUNT(*) FROM artifacts WHERE mission_id=? AND path=?", (mission_id, PROBE)).fetchone()[0],
            "immutable": immutable, "observations": observations,
            "domain": commit.domain_for(mission_id),
            "events": [e.type for e in store.list_events(mission_id)],
            "disputes": mission_disputes(store, mission_id),
            "listed_once": listed_once, "listed_twice": listed_twice,
            "statuses_before": statuses_before, "statuses_after": statuses_after,
            "listing": read_knowledge_tool(store, mission_id, "knowledge_list", {}),
            # 交出正文前过使用证书签发方那道门（推后第 1 批 A26，与执行者运行中读同一道门）
            "reading": read_knowledge_tool(store, mission_id, "knowledge_read", {"id": knowledge_id},
                                           handover=knowledge_handover(commit, mission_id=mission_id,
                                                                       consumer_id="step04-read",
                                                                       task_id=knowledge[0].source_task)),
            "foreign_read": _refusal(lambda: read_knowledge_tool(
                store, "mission-foreign", "knowledge_read", {"id": knowledge_id})),
            "unknown_read": _refusal(lambda: read_knowledge_tool(
                store, mission_id, "knowledge_read", {"id": "observation:unknown"})),
            "packages": worker.packages,
            "lineage": lineage(store, mission_id),
            **_stage_c_facts(store, mission_id, knowledge, results),
        }
    return facts


def _stage_c_facts(store: Any, mission_id: str, knowledge: list[Any], results: dict[str, str]) -> dict[str, Any]:
    """阶段 C：黑板三层目录、引用带版本、是否当前读时判定、步骤审查包的两节。"""
    from dataclasses import replace

    from agent_orchestrator.contracts.resolution import ReviewPackage
    from agent_orchestrator.memory.knowledge_standing import knowledge_standing
    from agent_orchestrator.memory.verified_knowledge import KnowledgeIndex

    catalogue: list[dict[str, Any]] = []
    offset: int | None = 0
    digest = None
    while offset is not None:
        page = read_knowledge_tool(store, mission_id, "knowledge_list", {
            "offset": offset, "limit": 5, **({"expected_sha256": digest} if offset else {})})
        catalogue += page["items"]
        offset, digest = page["next_offset"], page["sha256"]
    record = knowledge[0]
    index = KnowledgeIndex.load(store, mission_id)
    packages = [ReviewPackage.from_json(json.loads(row[0])) for row in store.connection.execute(
        "SELECT package_json FROM review_packages WHERE mission_id=?", (mission_id,))]
    return {
        "catalogue": catalogue,
        "raw_read": read_knowledge_tool(store, mission_id, "knowledge_read",
                                        {"id": next(i["id"] for i in catalogue if i["layer"] == "raw_ref")}),
        "check_ok": index.check([f"{record.id}@{record.version}"]),
        "check_wrong_version": index.check([f"{record.id}@{record.version + 1}"]),
        "check_no_version": index.check([record.id]),
        "standing": knowledge_standing(store, record),
        "standing_no_support": knowledge_standing(store, replace(record, support={})),
        "standing_lost_acceptance": knowledge_standing(
            store, replace(record, support={**dict(record.support), "acceptance_id": "acc-gone"})),
        "content_packages": [pkg for pkg in packages if str(pkg.purpose) == "TASK_CONTENT"],
        "result_tasks": results,
    }


@pytest.fixture(scope="module")
def world(tmp_path_factory):  # type: ignore[no-untyped-def]
    import agent_orchestrator.orchestrator.event_handler as event_handler

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)
        facts = asyncio.run(_run(tmp_path_factory.mktemp("knowledge")))
    assert str(facts["mission"].status.value) == "COMPLETED", facts["mission"].final_report
    return facts


def _write_claims(world: dict[str, Any]) -> list[Any]:
    return sorted((c for c in world["claims"].values() if c.result_id == world["write_result"]
                   and not c.proposed_by.startswith("system:")), key=lambda c: c.id)


def _continue_claims(world: dict[str, Any]) -> list[Any]:
    return sorted((c for c in world["claims"].values() if c.source_task != world["write_task"]),
                  key=lambda c: c.id)


def test_a_passing_check_supports_but_never_verifies_a_workers_claim(world):
    """k01/k02/k03：执行者的声明只按引用判"有无依据"；进知识库的只有系统的测试观测。"""

    claims = _write_claims(world)
    grades = [(c.status, dict(c.confidence_metadata)["grade"]) for c in claims]
    assert grades == [
        (ClaimStatus.DISPUTED, "supported"),        # 引用跑过并通过的检查 → 有依据；后来被下游反驳
        (ClaimStatus.UNDER_REVIEW, "unsupported"),  # 悬空的工具引用不支撑
        (ClaimStatus.UNDER_REVIEW, "unsupported"),  # 悬空的知识引用不支撑
        (ClaimStatus.UNDER_REVIEW, "unsupported"),  # 没跑过的检查不支撑
        (ClaimStatus.SUPPORTED, "supported"),       # 无关的语义断言：有出处，但不因此成立
    ]
    assert not any(c.status is ClaimStatus.VERIFIED for c in world["claims"].values()
                   if not c.proposed_by.startswith("system:"))
    (record,) = world["knowledge"]
    assert record.type == "test_observation" and record.status == "VERIFIED"
    assert record.proposed_by == "system:pytest-observation-v2" and record.source_task == world["write_task"]
    assert record.verifier["scope"]["artifact_hashes"] == {
        a.path: a.content_hash for a in world["write_artifacts"]}
    assert record.verifier["scope"]["result_id"] == world["write_result"]
    assert "only that test outcome" in record.content
    assert world["events"].count("KnowledgeCommitted") == 1
    # 观测只认本次真实记录的执行：改范围/回执、或不是核验器记录的检查层，都不出观测
    assert world["observations"] == {None: 1, "hash": 0, "result": 0, "attempt": 0, "receipt": 0,
                                     "unrecorded": 0}
    assert world["immutable"].startswith("CommitRejected") and "immutable" in world["immutable"]
    domain = world["domain"]
    assert domain.version == "6" and "claim_grading" not in domain.completion_rules
    assert domain.completion_rules["result_envelope_contract"] == "candidate-json-v1"


def test_the_downstream_worker_sees_upstream_verified_knowledge_with_its_source(world):
    """k04 与输出合同：下游执行者的真实派发里有上游已核实知识的全文和出处；结果示例带真实编号。"""

    (record,) = world["knowledge"]
    (package,) = world["packages"]["continue"][:1]
    (view,) = package["verified_knowledge"]
    assert view["id"] == record.id and view["content"] == record.content
    assert view["source_task"] == world["write_task"] and view["evidence"] == ["pytest:" + PROBE]
    assert view["status"] == "VERIFIED" and package["knowledge_retrieval"]["status"] == "ok"
    assert "candidate_claims" not in package and "rejected_claims" not in package
    # 摘要层只有审阅员核对过的各步摘要（阶段 C3）；这里的脚本审阅员不核对，所以没有
    assert package["step_summaries"] == []
    # 输出合同：版本化的示例里是本次派发的真实编号和要交的文件
    match = re.search(r"<result_envelope>(.*?)</result_envelope>", package["output_contract"], re.S)
    assert match is not None
    example = json.loads(match.group(1))
    ResultEnvelope.from_json({**example, "id": "result-provisional"})
    assert example["task_id"] == package["task_contract"]["task_id"]
    assert example["attempt_id"] == package["attempt"]["attempt_id"]
    assert example["artifacts"] == [NOTES] and example["outcome"] == "candidate"
    # 下游引用它：used_knowledge 记下复用链，引用 knowledge:<id> 的声明有依据
    cited = _continue_claims(world)[3]
    assert record.used_by == (cited.source_task,)
    assert world["events"].count("KnowledgeUsed") == 1
    assert cited.content == "沿用上游的测试观测" and cited.status is ClaimStatus.SUPPORTED
    # 原始知识读取（产品网关调的同一个函数）：预览不全、全文可读，别的任务读不到
    assert world["listing"]["items"][0]["preview"] != record.content
    assert world["reading"]["content"] == record.content
    assert world["reading"]["source_task"] == world["write_task"]
    assert "not available" in world["foreign_read"] and "not available" in world["unknown_read"]


def test_contradicting_claims_are_disputed_and_never_enter_knowledge(world):
    """两步声明互相矛盾都标争议并互指；同一结果两条反驳同一条都记上；与已核实知识矛盾的不进知识。"""

    first = _write_claims(world)[0]
    against_knowledge, affirms, affirms_again = _continue_claims(world)[:3]
    (record,) = world["knowledge"]
    observation = world["claims"][record.claim_id]
    assert affirms.status is ClaimStatus.DISPUTED and affirms_again.status is ClaimStatus.DISPUTED
    assert first.status is ClaimStatus.DISPUTED
    assert set(first.disputed_by) == {affirms.id, affirms_again.id}
    assert affirms.disputed_by == (first.id,) and affirms_again.disputed_by == (first.id,)
    # 争议先于判级：后来那条自己的依据（NOTES.md 是本次产物）判过"有依据"，仍封顶为争议
    assert dict(affirms.confidence_metadata)["grade_before_dispute"] == "supported"
    # 反驳已核实知识：这条是争议，知识照旧是 VERIFIED，只标上谁有异议
    assert against_knowledge.status is ClaimStatus.DISPUTED
    assert against_knowledge.disputed_by == (record.claim_id,)
    assert record.status == "VERIFIED" and record.disputed_by == (against_knowledge.id,)
    assert observation.status is ClaimStatus.VERIFIED and observation.disputed_by == (against_knowledge.id,)
    assert [k.id for k in world["knowledge"]] == [record.id]
    assert world["events"].count("ClaimDisputed") == 3
    disputes = {d["claim_id"]: d for d in world["disputes"]}
    assert set(disputes) == {first.id, affirms.id, affirms_again.id, against_knowledge.id, record.claim_id}
    assert disputes[record.claim_id]["in_knowledge"] is True
    assert not any(d["in_knowledge"] for key, d in disputes.items() if key != record.claim_id)


def test_the_knowledge_records_who_modified_it(world):
    """K07（推后第 3 批车道 R2，原计划 09-10 §11.2"由谁修改过"）：下游声明反驳已核实知识，知识上追加
    恰好一项修改记录，指向那条声明、它的任务与尝试、提出者与时刻；血缘视图里也带着。

    **改坏检验**：反驳写方不追加修改记录 → 本条失败。"""

    against_knowledge = _continue_claims(world)[0]
    (record,) = world["knowledge"]
    assert len(record.modified_by) == 1, record.modified_by
    (entry,) = record.modified_by
    assert {key: value for key, value in entry.items() if key != "at"} == {
        "change": "DISPUTED", "by": against_knowledge.id, "task_id": against_knowledge.source_task,
        "attempt_id": against_knowledge.source_attempt, "proposed_by": against_knowledge.proposed_by}
    assert entry["at"] >= record.created_at
    (view,) = [item for item in world["lineage"]["knowledge"] if item["id"] == record.id]
    assert view["modified_by"] == [dict(entry)]


def test_a_model_cannot_supersede_verified_knowledge(world):
    """取代：产品里执行者声明到不了 VERIFIED，所以模型的 supersedes 一律被拒，知识照旧现行、版本不变。"""

    proposer = _continue_claims(world)[4]
    (record,) = world["knowledge"]
    assert proposer.status is ClaimStatus.SUPPORTED
    assert "only a VERIFIED claim may supersede" in dict(proposer.confidence_metadata)["supersedes_rejected"]
    assert record.status == "VERIFIED" and record.superseded_by is None and record.version == 1
    assert "KnowledgeSuperseded" not in world["events"]


def test_artifact_versions_survive_a_redelivered_turn(world):
    """同一回合的结果因崩溃被再收一次：结果只有一份，产物版本不变；同路径同版本的第二行被表结构拒。"""

    assert world["crashes"] == 1 and world["fired"] == ["after_result_submitted:attempt"]
    assert list(world["results"].values()).count(world["write_task"]) == 1
    assert sorted((path, version) for path, version, _ in world["artifacts"]) == [(NOTES, 1), (PROBE, 1)]
    assert world["artifact_rows"] == 1
    assert world["duplicate_row"].startswith("IntegrityError")


def test_blackboard_is_a_read_only_projection(world):
    """读黑板确定、不改声明状态。（四层读取见 product_world/test_blackboard_tools.py。）"""

    assert world["listed_once"] == world["listed_twice"]  # 确定
    assert world["statuses_after"] == world["statuses_before"]  # 不改声明状态


def test_blackboard_layers_versioned_citation_and_review_package_sections(world):
    """阶段 C：执行者读到的黑板分三层；引用知识要写"编号@版本"；知识是否当前读时判定；
    步骤审查包带"本步待确认结论"和"相关条目"，相关条目里没有被审步骤自己的东西。

    **改坏检验**：读工具不再过滤过时知识 / 核对不比版本 → 对应断言变红。"""

    (record,) = world["knowledge"]
    layers = {item["layer"] for item in world["catalogue"]}
    assert layers == {"verified", "candidate", "raw_ref"}
    [verified] = [item for item in world["catalogue"] if item["layer"] == "verified"]
    assert verified["ref"] == f"{record.id}@{record.version}" and verified["basis"] == "test_observation"
    candidates = [item for item in world["catalogue"] if item["layer"] == "candidate"]
    assert candidates and all(item["marker"] in {"未验证", "有争议，不是事实"} for item in candidates)
    assert any(item["marker"] == "有争议，不是事实" for item in candidates)
    raw = [item for item in world["catalogue"] if item["layer"] == "raw_ref"]
    assert raw and all(set(item) == {"layer", "id", "source_task", "artifacts"} for item in raw)
    assert "content" not in world["raw_read"]  # 原始记录引用层不带任何文件内容
    # 引用带版本：对的通过；版本不符、不写版本都报问题
    assert world["check_ok"] == []
    assert world["check_wrong_version"] and "not the current version" in world["check_wrong_version"][0]
    assert "names no version" in world["check_no_version"][0]
    # 是否当前读时判定：知识行没变，依据没了或来源验收不在了就是过时
    assert world["standing"] == "CURRENT" and record.support["acceptance_id"].startswith("acc-")
    assert world["standing_no_support"] == "STALE:no_support"
    assert world["standing_lost_acceptance"] == "STALE:acceptance_missing"
    # 步骤审查包两节
    by_task = {}
    for package in world["content_packages"]:
        by_task[str(package.binding.subject_ref.id)] = package
    downstream = next(pkg for task, pkg in by_task.items() if task != world["write_task"])
    own_task = str(downstream.binding.subject_ref.id)
    own_claims = {c.id for c in world["claims"].values() if c.source_task == own_task}
    assert {row["claim_id"] for row in downstream.claims_to_confirm} <= own_claims
    assert downstream.claims_to_confirm, "the reviewed result's claims are listed for confirmation"
    kinds = {row["kind"] for row in downstream.related_entries}
    assert kinds == {"dispute", "used_knowledge"}
    assert all(row["source_task"] != own_task for row in downstream.related_entries)
    used = next(row for row in downstream.related_entries if row["kind"] == "used_knowledge")
    assert (used["id"], used["version"]) == (record.id, record.version) and used["content"] == record.content

