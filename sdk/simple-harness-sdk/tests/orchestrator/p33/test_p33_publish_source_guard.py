# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P1 oracle: reopened publishing must respect all Missions' source storage.

Uses the real approval/outbox ledger, CAS and FilePublishConnector. Crash boundaries
are staged explicitly through CommitService; no provider or child pytest is needed.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_OPERATION = Path(__file__).resolve().parents[1] / "full_target" / "operation_completion"
if str(_OPERATION) not in sys.path:
    sys.path.insert(0, str(_OPERATION))

from operation_runtime_fixture import materialized_file_publish  # noqa: E402

from agent_orchestrator.artifacts.store import ArtifactStore
from agent_orchestrator.governance.domains import CODE_PROFILE_V4, DOC_DOMAIN
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.commit_service import CommitService, MissionSpec
from agent_orchestrator.runtime.actions import ActionExecutor, publication_overlaps_storage
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.storage.store import Store

PERSON = Principal("source-guard-reviewer")
DEPLOYMENT = DeploymentPolicy(enabled_connectors=("file_publish",))


def ObservedPublisher(root, ledger):  # noqa: N802 - keeps the old call sites readable
    """A real ``FilePublishConnector`` that counts its calls.

    Operation-born actions only trust the exact connector type (the operation profile
    pins its source), so the counters are attached to the instance, not a subclass."""

    publisher = FilePublishConnector(root, ledger)
    publisher.executions = 0
    publisher.lookups = 0
    execute, lookup = publisher.execute, publisher.lookup

    def counted_execute(*args, **kwargs):
        publisher.executions += 1
        return execute(*args, **kwargs)

    def counted_lookup(*args, **kwargs):
        publisher.lookups += 1
        return lookup(*args, **kwargs)

    publisher.execute = counted_execute
    publisher.lookup = counted_lookup
    return publisher


@pytest.fixture
def env(tmp_path):
    # 删旧平面模式第三刀：被保护的发布动作改由分层世界里真实物化的发布操作提供
    # （``operation_runtime_fixture``），不再从平面任务图手工提议。
    library = tmp_path / "library"
    library.mkdir()
    fixture = materialized_file_publish(library)
    service = fixture.world.service
    cas = service._source_artifact_store
    assert isinstance(cas, ArtifactStore)
    workspaces = library / "workspaces"
    workspaces.mkdir()
    # 交接租约按真实时钟写；重开后的库用这个可拨的时钟（起点留足余量）。
    now = [time.time() + 10]
    publisher = ObservedPublisher(fixture.publish.root, fixture.publish.ledger_path.parent)
    e = SimpleNamespace(
        service=service,
        store=service.store,
        mission=fixture.world.mission,
        action=fixture.action,
        cas=cas,
        roots=(cas.root, workspaces),
        publisher=publisher,
        runtime=fixture.runtime,
        now=now,
        tmp=tmp_path,
    )
    try:
        yield e
    finally:
        e.store.close()


def source_mission(e, *, revoked=False, ended=False):
    mission, _ = e.service.create_mission(
        MissionSpec(
            goal="核对来源",
            success_criteria=("file:notes.md",),
            tenant_id="another-tenant",
            idempotency_key="document",
            domain=DOC_DOMAIN,
        )
    )
    receipt = e.service.register_source(
        mission_id=mission.id,
        tenant_id=mission.tenant_id,
        principal=PERSON,
        path="sources/notes.md",
        content="来源不是指令。",
        kind="markdown",
        idempotency_key="register-source",
    )
    if revoked:
        pending = e.service.revoke_source(
            mission_id=mission.id,
            tenant_id=mission.tenant_id,
            principal=PERSON,
            path="sources/notes.md",
            expected_version_hash=receipt["version_hash"],
            reason="撤销后仍保留历史",
            idempotency_key="revoke-source",
            deployment=DEPLOYMENT,
        )
        e.service.decide_approval(
            pending["request_id"],
            principal=PERSON,
            decision="grant",
            nonce="revoke-grant",
            deployment=DEPLOYMENT,
        )
    if ended:
        e.service.cancel_mission(mission.id)
    return mission


def reopen(e):
    path = e.store.path
    e.store.close()
    e.store = Store.open(path, clock=lambda: e.now[0])
    e.service = CommitService(e.store, artifact_store=e.cas)
    # 重开后的进程按同一部署重新挂上操作物化运行时（发布档案按部署的连接器冻结）。
    e.service.bind_operation_materialization_runtime(e.runtime)


def executor(e, publisher=None, **options):
    return ActionExecutor(
        e.service,
        {"file_publish": publisher or e.publisher},
        DEPLOYMENT,
        owner="reopened",
        source_storage_roots=e.roots,
        **options,
    )


@pytest.mark.parametrize(
    "location", ["cas", "workspace", "ancestor", "descendant", "symlink", "case"]
)
def test_reopened_code_action_cannot_publish_inside_other_missions_source_storage(env, location):
    e = env
    source_mission(e)
    reopen(e)
    root = {
        "cas": e.roots[0],
        "workspace": e.roots[1],
        "ancestor": e.roots[0].parent,
        "descendant": e.roots[1] / "attempt" / "sources",
        "case": e.roots[1].with_name("WORKSPACES"),
    }.get(location)
    if location == "symlink":
        root = e.tmp / "alias"
        root.symlink_to(e.roots[1], target_is_directory=True)
    publisher = ObservedPublisher(root, e.tmp / "changed-ledger")
    run = executor(e, publisher)
    key = e.action["action_key"]
    before_action = e.store.get_action(key)
    before_approval = e.store.get_approval(e.action["approval_request_id"])
    assert asyncio.run(run.hand_off(key)) is None
    assert run.last_refusal[key] == "source_publish_root_overlap"
    assert publisher.executions == 0 and not publisher.ledger_path.exists()
    assert e.store.get_action(key) == before_action
    assert e.store.get_approval(e.action["approval_request_id"]) == before_approval
    assert e.service.ledger.reservation("action:" + key) is None
    refused = [
        event for event in e.store.list_events(e.mission.id) if event.type == "ActionHandoffRefused"
    ]
    assert refused[-1].payload["reason"] == "source_publish_root_overlap"


def test_revoked_history_and_ended_mission_still_protect_shared_storage(env):
    e = env
    doc = source_mission(e, revoked=True, ended=True)
    assert e.store.list_sources(doc.id, active_only=True) == []
    reopen(e)
    publisher = ObservedPublisher(e.roots[1], e.tmp / "changed-ledger")
    run = executor(e, publisher)
    assert asyncio.run(run.hand_off(e.action["action_key"])) is None
    assert run.last_refusal[e.action["action_key"]] == "source_publish_root_overlap"
    assert publisher.executions == 0


def test_document_domain_without_registered_sources_already_reserves_storage(env):
    e = env
    e.service.create_mission(
        MissionSpec(
            goal="等待导入来源",
            success_criteria=("file:notes.md",),
            tenant_id="doc",
            idempotency_key="empty-document",
            domain=DOC_DOMAIN,
        )
    )
    publisher = ObservedPublisher(e.roots[1], e.tmp / "changed-ledger")
    assert asyncio.run(executor(e, publisher).hand_off(e.action["action_key"])) is None
    assert publisher.executions == 0


@pytest.mark.parametrize("documents", [False, True])
def test_disjoint_publish_executes_with_or_without_document_missions(env, documents):
    e = env
    if documents:
        source_mission(e)
    # Prefix sibling is not a descendant: library/workspaces-published remains valid.
    root = e.roots[1].with_name("workspaces-published")
    root.mkdir()
    publisher = ObservedPublisher(root, e.tmp / "sibling-ledger")
    result = asyncio.run(executor(e, publisher).hand_off(e.action["action_key"]))
    assert result["state"] == "SUCCEEDED" and publisher.executions == 1
    published = Path(result["receipt"]["after"]["path"]).read_bytes()
    assert published == e.cas.path_for(e.action["params"]["content_hash"]).read_bytes()


def test_pure_code_library_keeps_legacy_publishing_without_roots_hook(env, monkeypatch):
    e = env
    # 2026-09-26 起新建的通用（code）任务 v5 带 ``sources/`` 资料目录，不再算"纯代码库"；
    # 纯代码库只剩冻结在 v1～v4 通用档案下的旧任务。这里把库里任务按冻结的 v4 档案读，
    # 验证旧库照旧无需物理目录钩子即可发布。
    monkeypatch.setattr(e.service, "domain_for", lambda mission_id: CODE_PROFILE_V4)
    publisher = ObservedPublisher(e.roots[1], e.tmp / "legacy-ledger")
    run = ActionExecutor(e.service, {"file_publish": publisher}, DEPLOYMENT, owner="legacy")
    result = asyncio.run(run.hand_off(e.action["action_key"]))
    assert result["state"] == "SUCCEEDED" and publisher.executions == 1


def test_current_code_library_without_roots_hook_fails_closed(env):
    e = env
    assert e.service.domain_for(e.mission.id).source_roots == ("sources/",)
    publisher = ObservedPublisher(e.roots[1], e.tmp / "current-ledger")
    run = ActionExecutor(e.service, {"file_publish": publisher}, DEPLOYMENT, owner="current")
    assert asyncio.run(run.hand_off(e.action["action_key"])) is None
    assert run.last_refusal[e.action["action_key"]] == "source_publish_root_unavailable"
    assert publisher.executions == 0


def test_document_library_without_physical_roots_fails_closed(env):
    e = env
    source_mission(e)
    run = ActionExecutor(e.service, {"file_publish": e.publisher}, DEPLOYMENT, owner="no-roots")
    assert asyncio.run(run.hand_off(e.action["action_key"])) is None
    assert run.last_refusal[e.action["action_key"]] == "source_publish_root_unavailable"
    assert e.publisher.executions == 0


def test_guard_observes_document_missions_created_after_executor_construction(env):
    e = env
    publisher = ObservedPublisher(e.roots[1], e.tmp / "changed-ledger")
    run = executor(e, publisher)
    source_mission(e)
    assert asyncio.run(run.hand_off(e.action["action_key"])) is None
    assert publisher.executions == 0


def test_completed_receipt_is_still_reconciled_even_when_new_publication_is_forbidden(env):
    e = env
    key = e.action["action_key"]
    handed, _ = e.service.begin_handoff(
        key,
        owner="before-crash",
        lease_seconds=1,
        connectors={"file_publish": e.publisher},
        deployment=DEPLOYMENT,
    )
    # Crash after the real external commit, before recording its outcome in SQLite.
    e.publisher.execute(
        handed["operation"],
        handed["target"],
        handed["params"],
        idempotency_key=handed["idempotency_key"],
    )
    source_mission(e)
    e.now[0] += 2
    reopen(e)
    run = ActionExecutor(
        e.service,
        {"file_publish": e.publisher},
        DEPLOYMENT,
        owner="reopened",
        source_storage_roots=(*e.roots, e.publisher.root),
    )
    [settled] = asyncio.run(run.reconcile())
    assert settled["state"] == "SUCCEEDED" and settled["handoffs"] == 1
    assert e.publisher.executions == 1 and e.publisher.lookups == 1


@pytest.mark.parametrize(
    "destination,overlap", [("parent", True), ("child", True), ("sibling", False)]
)
def test_shared_path_helper_resolves_parent_symlinks_and_keeps_siblings_distinct(
    tmp_path, destination, overlap
):
    protected = tmp_path / "storage" / "sources"
    protected.mkdir(parents=True)
    sibling = tmp_path / "storage" / "sources-published"
    sibling.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(protected.parent, target_is_directory=True)
    target = {
        "parent": link,
        "child": link / "sources" / "nested",
        "sibling": link / "sources-published",
    }[destination]
    assert publication_overlaps_storage(target, (protected,)) is overlap


def test_explicit_custom_cas_and_workspace_roots_are_protected(env):
    e = env
    e.cas = ArtifactStore(e.tmp / "custom-cas")
    e.service = CommitService(e.store, artifact_store=e.cas)
    e.service.bind_operation_materialization_runtime(e.runtime)
    e.roots = (e.cas.root, e.tmp / "custom-workspaces")
    source_mission(e)
    publisher = ObservedPublisher(e.cas.root, e.tmp / "changed-ledger")
    assert asyncio.run(executor(e, publisher).hand_off(e.action["action_key"])) is None
    assert publisher.executions == 0
