from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import aiosqlite
import pytest

from config import WorkflowsConfig, resolve_deep_research_workflow_version
from deskpet.retrieval.contracts import (
    EvidenceDocument,
    FetchRequest,
    RetrievalCandidate,
    SearchRequest,
    SearchResponse,
)
from deskpet.workflows.adapters.deep_research_v6_bootstrap import (
    DeepResearchV6ProductionRetrievalPort,
    build_deep_research_v6_context,
)
from deskpet.workflows.adapters.deep_research_v6_semantic_runtime import (
    DeepResearchV6SemanticRuntime,
)
from deskpet.workflows.adapters.research_runtime import (
    BoundResearchEffectContext,
    DurableV6ResearchLLMStagePort,
    V6_RESEARCH_RESPONSE_FORMATS,
    WorkflowControlSignalHub,
    build_v6_research_llm_profiles,
    research_response_format_hash,
)
from deskpet.workflows.definitions.v6.deep_research import initial_state
from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchLLMResult
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec
from deskpet.workflows.definitions.deep_research_v6_contracts import ResearchSpecV1
from deskpet.workflows.definitions import deep_research_v6_production_nodes
from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
from deskpet.workflows.launcher import WorkflowLauncher
from deskpet.workflows.delivery import (
    DeliveryDisposition,
    broadcast_websocket_best_effort,
)
from deskpet.workflows.store import NativeCheckpointStore, RegisteredBlobStore, RunFence


@pytest.mark.parametrize(
    "environment,expected,reason",
    [
        ({}, "v5", "configured"),
        ({"DESKPET_DEV_DEEPRESEARCH_VERSION": "v6"}, "v5", "dev_mode_required"),
        (
            {"DESKPET_DEV_DEEPRESEARCH_VERSION": "v6", "DESKPET_DEV_MODE": "1"},
            "v5",
            "isolated_user_data_required",
        ),
        (
            {
                "DESKPET_DEV_DEEPRESEARCH_VERSION": "v7",
                "DESKPET_DEV_MODE": "1",
                "DESKPET_USER_DATA_DIR": "F:/isolated",
            },
            "v5",
            "dev_override_unsupported",
        ),
    ],
)
def test_v6_dev_ingress_fails_closed(environment, expected, reason) -> None:
    assert resolve_deep_research_workflow_version(
        "v5",
        environment=environment,
        platform_default_user_data_dir="C:/platform-default",
    ) == (expected, reason)


def test_v6_dev_ingress_requires_resolved_non_default_user_data(tmp_path: Path) -> None:
    platform_default = tmp_path / "platform-default"
    valid_env = {
        "DESKPET_DEV_DEEPRESEARCH_VERSION": "v6",
        "DESKPET_DEV_MODE": "1",
        "DESKPET_USER_DATA_DIR": str(tmp_path / "isolated-v6"),
    }
    assert resolve_deep_research_workflow_version(
        "v5", environment=valid_env, platform_default_user_data_dir=platform_default
    ) == ("v6", "dev_isolated_override")

    default_env = {**valid_env, "DESKPET_USER_DATA_DIR": str(platform_default / ".")}
    assert resolve_deep_research_workflow_version(
        "v5", environment=default_env, platform_default_user_data_dir=platform_default
    ) == ("v5", "isolated_user_data_required")


def test_v6_release_config_is_a_normal_new_root_ingress() -> None:
    assert WorkflowsConfig().deep_research_version == "v6"
    assert WorkflowsConfig().deep_research_default_revision == 6
    assert resolve_deep_research_workflow_version("v6", environment={}) == (
        "v6",
        "configured",
    )


@pytest.mark.parametrize("configured", ["", "v7", "garbage", "v4"])
def test_invalid_or_recovery_only_config_falls_back_to_factory_v6(configured: str) -> None:
    assert resolve_deep_research_workflow_version(configured, environment={}) == (
        "v6",
        "configured_invalid_factory_default",
    )


def test_explicit_v5_pin_remains_supported_for_new_roots() -> None:
    assert resolve_deep_research_workflow_version("v5", environment={}) == (
        "v5",
        "configured",
    )


def test_main_enables_released_v6_roots_with_durable_budgets() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    registration = source.index('"deep_research",\n            "v6",')
    registration_end = source.index("from agent.tool_use_shim", registration)
    assert "new_runs_enabled=True" in source[registration:registration_end]

    ingress = source.index("async def _start_deepresearch_graph")
    ingress_end = source.index("set_deepresearch_workflow_starter", ingress)
    section = source[ingress:ingress_end]
    assert 'deep_research_version or "v6"' in section
    assert 'workflow_version in {"v5", "v6"}' in section
    assert '"research_llm_budget"' in section
    assert '"research_io_budget"' in section


def test_main_websocket_delivery_returns_typed_best_effort_outcome() -> None:
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    start = source.index("async def _workflow_websocket_delivery")
    end = source.index("async def _workflow_artifact_publisher", start)
    handler = source[start:end]
    assert "broadcast_websocket_best_effort" in handler


class _RecordingWebSocket:
    def __init__(self, *, fail: bool = False, block: bool = False) -> None:
        self.fail = fail
        self.block = block
        self.envelopes: list[dict[str, object]] = []

    async def send_json(self, envelope) -> None:
        if self.fail:
            raise RuntimeError("send failed")
        if self.block:
            await asyncio.Event().wait()
        self.envelopes.append(dict(envelope))


@pytest.mark.asyncio
async def test_production_websocket_broadcast_is_typed_and_deduplicated() -> None:
    empty = await broadcast_websocket_best_effort({"type": "workflow_event"}, [])
    assert empty.disposition is DeliveryDisposition.DELIVERED
    assert empty.reason_code == "websocket_best_effort"

    ws = _RecordingWebSocket()
    result = await broadcast_websocket_best_effort(
        {"type": "workflow_final"}, [ws, ws]
    )
    assert result.disposition is DeliveryDisposition.DELIVERED
    assert ws.envelopes == [{"type": "workflow_final"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ws", "reason"),
    [
        (_RecordingWebSocket(fail=True), "websocket_send_failed"),
        (_RecordingWebSocket(block=True), "websocket_send_timeout"),
    ],
)
async def test_production_websocket_broadcast_returns_retryable_failure(
    ws: _RecordingWebSocket, reason: str
) -> None:
    result = await broadcast_websocket_best_effort(
        {"type": "workflow_event"}, [ws], timeout_s=0.01
    )
    assert result.disposition is DeliveryDisposition.RETRYABLE_FAILURE
    assert result.reason_code == reason


class _OfficialFetch:
    def __init__(self) -> None:
        self.archive = "https://www.stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/"
        self.target = "https://www.stats.gov.cn/sj/zxfb/v6-bootstrap.html"
        self.requests: list[FetchRequest] = []

    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        self.requests.append(request)
        if request.url == self.archive:
            body = "National Bureau of Statistics annual bulletin index"
            html = '<a href="/sj/zxfb/v6-bootstrap.html">2024年</a>'
            final_url = "https://stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/"
        elif request.url == self.target:
            body = "2024年末全国人口140828万人。2024年全年出生人口954万人。"
            html = None
            final_url = request.url
        else:
            raise AssertionError(f"unexpected production fetch URL: {request.url}")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return EvidenceDocument(
            stable_id="doc-" + digest[:12], url=request.url,
            canonical_url=final_url, title="国家统计局公报", text=body,
            published_at=None, provider="fake", providers=("fake",),
            provider_rank=0, score=1.0,
            searched_at="2026-07-18T00:00:00+00:00", source_kind="web",
            content_hash=digest, fetcher="fake", extractor="fake",
            fetched_at="2026-07-18T00:00:01+00:00", html=html,
        )


class _OfficialGateway:
    def __init__(self) -> None:
        self.fetch_service = _OfficialFetch()
        self.search_calls = 0

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.search_calls += 1
        return SearchResponse(
            query=request.query, results=(), request_id=request.request_id,
            run_id=request.run_id,
        )


class _BlockingOfficialFetch(_OfficialFetch):
    def __init__(self, *, block_stage: str = "archive") -> None:
        super().__init__()
        if block_stage not in {"archive", "target"}:
            raise ValueError("block_stage must be archive or target")
        self.block_stage = block_stage
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        if request.url == getattr(self, self.block_stage):
            self.started.set()
            await self.release.wait()
        return await super().fetch(request)


class _BlockingOfficialGateway(_OfficialGateway):
    def __init__(self, *, block_stage: str = "archive") -> None:
        super().__init__()
        self.fetch_service = _BlockingOfficialFetch(block_stage=block_stage)


class _FailingTargetOfficialFetch(_OfficialFetch):
    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        if request.url == self.target:
            self.requests.append(request)
            raise RuntimeError("injected_target_fetch_failure")
        return await super().fetch(request)


class _FailingTargetOfficialGateway(_OfficialGateway):
    def __init__(self) -> None:
        super().__init__()
        self.fetch_service = _FailingTargetOfficialFetch()


class _BlockingGenericFetch:
    def __init__(self) -> None:
        self.target = "https://example.test/deep-research-control-frontier"
        self.requests: list[FetchRequest] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        self.requests.append(request)
        if request.url != self.target:
            raise AssertionError(f"unexpected generic fetch URL: {request.url}")
        self.started.set()
        await self.release.wait()
        body = (
            "The source documents a current conclusion, limitation, "
            "counterevidence, and uncertainty."
        )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return EvidenceDocument(
            stable_id="generic-document-" + digest[:12],
            url=request.url,
            canonical_url=request.url,
            title="Generic research source",
            text=body,
            published_at=None,
            provider="fake",
            providers=("fake",),
            provider_rank=0,
            score=1.0,
            searched_at="2026-07-18T00:00:00+00:00",
            source_kind="web",
            content_hash=digest,
            fetcher="fake",
            extractor="fake",
            fetched_at="2026-07-18T00:00:01+00:00",
            html=None,
        )


class _BlockingGenericGateway:
    def __init__(self) -> None:
        self.fetch_service = _BlockingGenericFetch()
        self.search_calls = 0

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.search_calls += 1
        target = self.fetch_service.target
        candidate = RetrievalCandidate(
            stable_id="generic-control-candidate",
            url=target,
            canonical_url=target,
            title="Generic research source",
            provider="fake",
            providers=("fake",),
            provider_rank=0,
            searched_at="2026-07-18T00:00:00+00:00",
        )
        return SearchResponse(
            query=request.query,
            results=(candidate,),
            request_id=request.request_id,
            run_id=request.run_id,
        )


class _TwoPageGenericFetch:
    def __init__(self) -> None:
        self.targets = (
            "https://one.example.test/deep-research-semantic-fence",
            "https://two.example.test/deep-research-semantic-fence",
        )
        self.requests: list[FetchRequest] = []

    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        self.requests.append(request)
        if request.url not in self.targets:
            raise AssertionError(f"unexpected generic fetch URL: {request.url}")
        body = f"Source {request.url} documents a conclusion and limitation."
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return EvidenceDocument(
            stable_id="generic-document-" + digest[:12],
            url=request.url,
            canonical_url=request.url,
            title="Generic research source",
            text=body,
            published_at=None,
            provider="fake",
            providers=("fake",),
            provider_rank=0,
            score=1.0,
            searched_at="2026-07-18T00:00:00+00:00",
            source_kind="web",
            content_hash=digest,
            fetcher="fake",
            extractor="fake",
            fetched_at="2026-07-18T00:00:01+00:00",
            html=None,
        )


class _TwoPageGenericGateway:
    def __init__(self) -> None:
        self.fetch_service = _TwoPageGenericFetch()
        self.search_calls = 0

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.search_calls += 1
        candidates = tuple(
            RetrievalCandidate(
                stable_id=f"generic-semantic-fence-{ordinal}",
                url=target,
                canonical_url=target,
                title=f"Generic research source {ordinal}",
                provider="fake",
                providers=("fake",),
                provider_rank=ordinal,
                searched_at="2026-07-18T00:00:00+00:00",
            )
            for ordinal, target in enumerate(self.fetch_service.targets)
        )
        return SearchResponse(
            query=request.query,
            results=candidates,
            request_id=request.request_id,
            run_id=request.run_id,
        )


async def _forbidden_llm(*_args, **_kwargs):
    raise AssertionError("official exact bootstrap path must not dispatch an LLM")


async def _controlled_v6_runtime(
    tmp_path: Path,
    gateway,
    *,
    existing_run_id: str | None = None,
    topic: str = "What were China's year-end total population and annual births in 2024?",
    llm_call=_forbidden_llm,
):
    from deskpet.workflows.bootstrap import build_workflow_service

    service = await build_workflow_service(tmp_path, activate=False)
    capability = {
        "research_io_budget": {
            "max_input_tokens": 100, "max_output_tokens": 0,
            "max_cost_micros": 0,
        },
        "research_llm_budget": {
            "max_input_tokens": 100_000, "max_output_tokens": 100_000,
            "max_cost_micros": 10_000_000,
        },
    }
    if existing_run_id is None:
        run_id = await service.runner.start(
            session_id="controlled-v6-session", request_id="controlled-v6-request",
            turn_id="controlled-v6-turn", workflow_name="deep_research",
            workflow_version="v6", capability_snapshot=capability,
        )
        await service.run_store.bind_session_refs(
            run_id, (("delivery", "controlled-v6-session", 0),)
        )
    else:
        run_id = existing_run_id
    database = service.run_store.path
    await service.research_repository.ensure_snapshot_lineage(
        run_id=run_id,
        operation_id=f"research:{run_id}",
        budget_lease_id=hashlib.sha256(
            f"controlled-v6-budget|{run_id}".encode("utf-8")
        ).hexdigest(),
    )
    # Use the production blob root so a fresh service instance can recover the
    # content-addressed terminal closure after process restart.
    blobs = RegisteredBlobStore(tmp_path / "workflows" / "blobs", database)
    journal = EffectJournal(database)

    async def resolve(identity):
        row = await service.run_store.get_run(identity.run_id)
        assert row is not None and row["status"] == "running"
        return BoundResearchEffectContext(
            identity,
            EffectExecutionContext(
                journal=journal,
                fence=RunFence(
                    identity.run_id, str(row["lease_owner"]),
                    int(row["lease_epoch"]), int(row["run_version"]),
                ),
                node_execution_id=f"controlled-{identity.checkpoint_id}-{identity.task_id}",
                workflow_name="deep_research", workflow_version="v6",
                node_id=identity.node_id,
            ),
        )

    signals = WorkflowControlSignalHub(
        service.research_repository, poll_interval=0.005
    )
    context = build_deep_research_v6_context(
        blobs=blobs, journal=journal, resolve_effect_context=resolve,
        search_gateway=gateway, llm_call=llm_call,
        control_signals=signals, request_id="controlled-v6-request",
        turn_id="controlled-v6-turn", max_parallel_tasks=3,
    )
    state = initial_state(
        topic=topic,
        run_id=run_id, session_id="controlled-v6-session",
    )
    return service, run_id, state, context


@pytest.mark.asyncio
async def test_production_v6_composition_executes_bootstrap_registry_runner(tmp_path: Path) -> None:
    from deskpet.workflows.bootstrap import build_workflow_service

    service = await build_workflow_service(tmp_path, activate=False)
    assert ("deep_research", "v6") in service.runner.registry.versions()
    capability = {
        "research_io_budget": {
            "max_input_tokens": 100, "max_output_tokens": 0,
            "max_cost_micros": 0,
        },
        "research_llm_budget": {
            "max_input_tokens": 100_000, "max_output_tokens": 100_000,
            "max_cost_micros": 10_000_000,
        },
    }
    run_id = await service.runner.start(
        session_id="bootstrap-v6-session", request_id="bootstrap-v6-request",
        turn_id="bootstrap-v6-turn", workflow_name="deep_research",
        workflow_version="v6", capability_snapshot=capability,
    )
    await service.run_store.bind_session_refs(
        run_id, (("delivery", "bootstrap-v6-session", 0),)
    )
    database = service.run_store.path
    blobs = RegisteredBlobStore(tmp_path / "production-v6-blobs", database)
    journal = EffectJournal(database)

    async def resolve(identity):
        row = await service.run_store.get_run(identity.run_id)
        assert row is not None and row["status"] == "running"
        context = EffectExecutionContext(
            journal=journal,
            fence=RunFence(
                identity.run_id, str(row["lease_owner"]), int(row["lease_epoch"]),
                int(row["run_version"]),
            ),
            node_execution_id=f"bootstrap-{identity.checkpoint_id}-{identity.task_id}",
            workflow_name="deep_research", workflow_version="v6",
            node_id=identity.node_id,
        )
        return BoundResearchEffectContext(identity, context)

    gateway = _OfficialGateway()
    context = build_deep_research_v6_context(
        blobs=blobs, journal=journal, resolve_effect_context=resolve,
        search_gateway=gateway, llm_call=_forbidden_llm,
        request_id="bootstrap-v6-request", turn_id="bootstrap-v6-turn",
        max_parallel_tasks=3,
    )
    assert isinstance(context.ports["retrieval"], DeepResearchV6ProductionRetrievalPort)
    assert isinstance(context.ports["semantic"], DeepResearchV6SemanticRuntime)
    assert all(
        isinstance(context.ports[name], DurableV6ResearchLLMStagePort)
        for name in ("llm_extract", "llm_inference", "llm_repair")
    )
    expected_profiles = build_v6_research_llm_profiles({
        role: research_response_format_hash(value)
        for role, value in V6_RESEARCH_RESPONSE_FORMATS.items()
    })
    assert {
        context.ports[name].profile_ref
        for name in ("llm_extract", "llm_inference", "llm_repair")
    } == {profile.profile_ref for profile in expected_profiles.values()}

    state = initial_state(
        topic="2024年中国总人口和出生人口分别是多少？优先国家统计局。",
        run_id=run_id, session_id="bootstrap-v6-session",
    )
    result = await service.runner.run(run_id, state, context)
    assert result.error is None, await service.runner.history(run_id)
    assert result.output["values"]["terminal_manifest_ref"].startswith("sha256:")
    assert gateway.search_calls == 0
    assert [request.url for request in gateway.fetch_service.requests] == [
        gateway.fetch_service.archive, gateway.fetch_service.target,
    ]
    route = json.loads((await blobs.get(
        result.output["values"]["route_decision_ref"].removeprefix("sha256:")
    )).decode("utf-8"))
    assert route["budget"]["llm"] == 0
    for profile in expected_profiles.values():
        assert json.loads((await blobs.get(profile.profile_hash)).decode("utf-8")) == {
            key: value for key, value in profile.to_json().items()
            if key not in {"profile_id", "profile_hash"}
        }
    async with aiosqlite.connect(database) as db:
        effect_types = [row[0] for row in await (await db.execute(
            "SELECT effect_type FROM workflow_effects WHERE run_id=?", (run_id,)
        )).fetchall()]
    assert "deep_research_v6_page_fetch" in effect_types
    assert "research_llm" not in effect_types


@pytest.mark.asyncio
async def test_real_launcher_persists_v6_automatic_deadline_before_actionable_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deskpet.workflows.bootstrap import build_workflow_service

    service = await build_workflow_service(tmp_path, activate=False)
    launcher = WorkflowLauncher(service)
    service.launcher = launcher
    gateway = _OfficialGateway()
    route_started = asyncio.Event()
    route_release = asyncio.Event()

    async def context_factory(row, start_payload):
        del start_payload
        database = service.run_store.path
        run_id = str(row["run_id"])
        blobs = RegisteredBlobStore(tmp_path / "launcher-v6-blobs", database)
        journal = EffectJournal(database)
        await service.research_repository.ensure_snapshot_lineage(
            run_id=run_id,
            operation_id=f"research:{run_id}",
            budget_lease_id=hashlib.sha256(
                f"launcher-v6-budget|{run_id}".encode("utf-8")
            ).hexdigest(),
        )

        async def resolve(identity):
            current = await service.run_store.get_run(identity.run_id)
            assert current is not None and current["status"] == "running"
            return BoundResearchEffectContext(
                identity,
                EffectExecutionContext(
                    journal=journal,
                    fence=RunFence(
                        identity.run_id,
                        str(current["lease_owner"]),
                        int(current["lease_epoch"]),
                        int(current["run_version"]),
                    ),
                    node_execution_id=(
                        f"launcher-{identity.checkpoint_id}-{identity.task_id}"
                    ),
                    workflow_name="deep_research",
                    workflow_version="v6",
                    node_id=identity.node_id,
                ),
            )

        signals = WorkflowControlSignalHub(
            service.research_repository, poll_interval=0.005
        )
        context = build_deep_research_v6_context(
            blobs=blobs,
            journal=journal,
            resolve_effect_context=resolve,
            search_gateway=gateway,
            llm_call=_forbidden_llm,
            control_signals=signals,
            request_id="launcher-v6-request",
            turn_id="launcher-v6-turn",
        )
        inner = context.ports["retrieval"]

        class BlockingPlanRoute:
            async def plan_route(self, **kwargs):
                route_started.set()
                await route_release.wait()
                return await inner.plan_route(**kwargs)

            async def load_pages(self, **kwargs):
                return await inner.load_pages(**kwargs)

        blocked = BlockingPlanRoute()
        return replace(
            context,
            ports={**context.ports, "retrieval": blocked},
        )

    launcher.register_adapter(
        "deep_research",
        "v6",
        state_factory=initial_state,
        context_factory=context_factory,
    )
    await service.activate_runtime()

    async def ignore_delivery(_event_id):
        return {"ignored": True}

    monkeypatch.setattr(service, "deliver_event_once", ignore_delivery)
    accepted = await launcher.launch(
        workflow_name="deep_research",
        workflow_version="v6",
        session_id="launcher-v6-session",
        request_id="launcher-v6-request",
        turn_id="launcher-v6-turn",
        start_payload={
            "topic": "What were China's year-end total population in 2024?"
        },
        capability_snapshot={
            "research_io_budget": {
                "max_input_tokens": 100,
                "max_output_tokens": 0,
                "max_cost_micros": 0,
            },
            "research_llm_budget": {
                "max_input_tokens": 100_000,
                "max_output_tokens": 100_000,
                "max_cost_micros": 10_000_000,
            },
        },
        state_factory=initial_state,
        context_factory=context_factory,
        authorize_disabled_deep_research_v6_new_root=True,
    )
    run_id = str(accepted["run_id"])
    route_wait = asyncio.create_task(route_started.wait())
    run_task = launcher._run_tasks[run_id]
    done, _ = await asyncio.wait(
        {route_wait, run_task}, timeout=10, return_when=asyncio.FIRST_COMPLETED
    )
    if run_task in done:
        failed = await service.run_store.get_run(run_id)
        raise AssertionError(f"launcher run ended before plan_route: {failed}")
    assert route_wait in done and route_started.is_set()
    running = await service.run_store.get_run(run_id)
    assert running is not None and running["status"] == "running"
    assert running["head_checkpoint_id"] is not None
    async with aiosqlite.connect(service.run_store.path) as db:
        deadline = await (
            await db.execute(
                """SELECT status,logical_scope FROM workflow_research_deadlines
                WHERE run_id=? AND logical_scope='run:automatic'""",
                (run_id,),
            )
        ).fetchone()
    assert deadline == ("open", "run:automatic")

    action = await service.execute_run_action(
        run_id,
        action_id="generate_now",
        idempotency_key="launcher-before-route-generate-now",
        expected_version=int(running["run_version"]),
        payload={},
    )
    assert action["accepted"] is True
    command_id = str(action["command"]["command_id"])
    command = await service.research_repository.get_control(command_id)
    assert command is not None and command["status"] == "accepted"
    assert command["accepted_at"] is not None
    await launcher.shutdown()


@pytest.mark.asyncio
async def test_restart_recovers_persisted_route_policy_v1_without_schema_version(
    tmp_path: Path,
) -> None:
    from deskpet.workflows.bootstrap import build_workflow_service

    gateway = _FailingTargetOfficialGateway()
    service, run_id, state, context = await _controlled_v6_runtime(tmp_path, gateway)
    result = await service.runner.run(run_id, state, context)
    assert result.error is None
    assert result.output["values"]["answer_status"] == "insufficient_evidence"

    route_ref = str(result.output["values"]["route_policy_ref"])
    route_policy = json.loads(
        (await context.ports["blob"].get(route_ref.removeprefix("sha256:"))).decode(
            "utf-8"
        )
    )
    assert route_policy["policy_id"] == "deep-research-v6-official-retrieval-v1"
    assert "schema_version" not in route_policy

    # A new process activates against the same real workflow.db and registered
    # blob tree.  Startup recovery must persist the continuation snapshot/pin
    # instead of aborting the entire workflow service.
    restarted = await build_workflow_service(tmp_path, activate=False)
    await restarted.activate_runtime()
    assert restarted.runtime_adapters.active is True
    async with aiosqlite.connect(restarted.run_store.path) as db:
        snapshot_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM workflow_research_snapshots WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
        )[0]
    assert snapshot_count == 1


@pytest.mark.asyncio
async def test_real_runner_preserves_failed_page_result_as_honest_insufficient(
    tmp_path: Path,
) -> None:
    gateway = _FailingTargetOfficialGateway()
    service, run_id, state, context = await _controlled_v6_runtime(tmp_path, gateway)

    result = await asyncio.wait_for(
        service.runner.run(run_id, state, context), timeout=30
    )

    assert result.error is None, await service.runner.history(run_id)
    assert result.output["values"]["answer_status"] == "insufficient_evidence"
    refs = result.output["values"]["page_result_refs"]
    assert len(refs) == 1
    raw = await context.ports["blob"].get(str(refs[0]).removeprefix("sha256:"))
    page_result = json.loads(raw.decode("utf-8"))
    assert page_result["outcome"] == "failed"
    assert page_result["page_record"] is None
    assert gateway.search_calls > 0
    assert [request.url for request in gateway.fetch_service.requests] == [
        gateway.fetch_service.archive,
        gateway.fetch_service.target,
    ]


@pytest.mark.asyncio
async def test_real_runner_generate_now_uses_durable_control_frontiers_and_honest_insufficient(
    tmp_path: Path,
) -> None:
    gateway = _BlockingOfficialGateway()
    service, run_id, state, context = await _controlled_v6_runtime(tmp_path, gateway)
    run_task = asyncio.create_task(service.runner.run(run_id, state, context))
    await asyncio.wait_for(gateway.fetch_service.started.wait(), timeout=10)

    running = await service.run_store.get_run(run_id)
    assert running is not None and running["status"] == "running"
    accepted = await service.execute_run_action(
        run_id,
        action_id="generate_now",
        idempotency_key="controlled-generate-now",
        expected_version=int(running["run_version"]),
        payload={},
    )
    assert accepted["accepted"] is True
    command_id = str(accepted["command"]["command_id"])
    gateway.fetch_service.release.set()

    result = await asyncio.wait_for(run_task, timeout=30)
    assert result.error is None
    assert result.output["values"]["answer_status"] == "insufficient_evidence"
    assert result.output["values"]["page_result_refs"] == []
    assert [request.url for request in gateway.fetch_service.requests] == [
        gateway.fetch_service.archive
    ]

    command = await service.research_repository.get_control(command_id)
    assert command is not None and command["status"] == "consumed"
    assert all(
        command[name] is not None
        for name in ("accepted_at", "observed_at", "settled_at", "consumed_at")
    )
    checkpoint = await NativeCheckpointStore(service.run_store.path).get_checkpoint(
        run_id,
        str(command["head_checkpoint_id"]),
        checkpoint_ns=str(command["head_checkpoint_ns"]),
    )
    # Consumption is attached to persist_manifest's committed input frontier;
    # the manifest output is committed immediately afterwards by the runner.
    assert checkpoint["state"]["values"]["stage"] == "integrity_passed"
    assert result.output["values"]["terminal_manifest_ref"].startswith("sha256:")


@pytest.mark.asyncio
async def test_real_runner_generate_now_preserves_inflight_canonical_page_outcome(
    tmp_path: Path,
) -> None:
    gateway = _BlockingOfficialGateway(block_stage="target")
    service, run_id, state, context = await _controlled_v6_runtime(tmp_path, gateway)
    run_task = asyncio.create_task(service.runner.run(run_id, state, context))
    await asyncio.wait_for(gateway.fetch_service.started.wait(), timeout=10)
    running = await service.run_store.get_run(run_id)
    assert running is not None
    accepted = await service.execute_run_action(
        run_id,
        action_id="generate_now",
        idempotency_key="preserve-inflight-generate-now",
        expected_version=int(running["run_version"]),
        payload={},
    )
    gateway.fetch_service.release.set()
    result = await asyncio.wait_for(run_task, timeout=30)
    assert result.error is None
    refs = result.output["values"]["page_result_refs"]
    assert len(refs) == 1
    raw = await context.ports["blob"].get(str(refs[0]).removeprefix("sha256:"))
    page = json.loads(raw.decode("utf-8"))
    assert page["outcome"] == "succeeded"
    command = await service.research_repository.get_control(
        str(accepted["command"]["command_id"])
    )
    assert command is not None and command["status"] == "consumed"


@pytest.mark.asyncio
async def test_real_runner_generate_now_fences_semantic_llm_after_inflight_generic_fetch(
    tmp_path: Path,
) -> None:
    gateway = _BlockingGenericGateway()
    llm_calls = 0

    async def counting_llm(*_args, **_kwargs):
        nonlocal llm_calls
        llm_calls += 1
        raise AssertionError("generate-now must fence new semantic LLM work")

    service, run_id, state, context = await _controlled_v6_runtime(
        tmp_path,
        gateway,
        topic=(
            "Research the current state, conclusions, limitations, and "
            "uncertainty of example technology"
        ),
        llm_call=counting_llm,
    )
    run_task = asyncio.create_task(service.runner.run(run_id, state, context))
    await asyncio.wait_for(gateway.fetch_service.started.wait(), timeout=10)
    running = await service.run_store.get_run(run_id)
    assert running is not None and running["status"] == "running"
    accepted = await service.execute_run_action(
        run_id,
        action_id="generate_now",
        idempotency_key="generic-mid-fetch-generate-now",
        expected_version=int(running["run_version"]),
        payload={},
    )
    assert accepted["accepted"] is True
    command_id = str(accepted["command"]["command_id"])
    gateway.fetch_service.release.set()

    result = await asyncio.wait_for(run_task, timeout=30)
    assert result.error is None, await service.runner.history(run_id)
    assert result.output["values"]["answer_status"] == "insufficient_evidence"
    assert llm_calls == 0
    assert gateway.search_calls > 0
    refs = result.output["values"]["page_result_refs"]
    assert len(refs) == 1
    raw = await context.ports["blob"].get(str(refs[0]).removeprefix("sha256:"))
    assert json.loads(raw.decode("utf-8"))["outcome"] == "succeeded"

    command = await service.research_repository.get_control(command_id)
    assert command is not None and command["status"] == "consumed"
    assert all(
        command[name] is not None
        for name in ("accepted_at", "observed_at", "settled_at", "consumed_at")
    )


@pytest.mark.asyncio
async def test_real_runner_generate_now_fences_inflight_semantic_page_group_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def compile_two_group_spec(question: str, **kwargs) -> ResearchSpecV1:
        base = compile_research_spec(question, **kwargs)
        value = base.to_json()
        value["work_dimensions"] = value["work_dimensions"][:2]
        return ResearchSpecV1.create(**value)

    monkeypatch.setattr(
        deep_research_v6_production_nodes,
        "compile_research_spec",
        compile_two_group_spec,
    )
    gateway = _TwoPageGenericGateway()
    provider_started = asyncio.Event()
    provider_release = asyncio.Event()
    provider_calls: list[str] = []

    async def blocking_llm(
        _prompt,
        *,
        max_output_tokens,
        stable_call_id,
        response_format=None,
    ) -> ResearchLLMResult:
        del max_output_tokens, response_format
        provider_calls.append(stable_call_id)
        provider_started.set()
        await provider_release.wait()
        return ResearchLLMResult(
            content="{}",
            model="semantic-fence-test-model",
            input_tokens=1,
            output_tokens=1,
            cache_tokens=0,
            usage_source="provider",
            request_id="semantic-fence-provider-request",
        )

    service, run_id, state, context = await _controlled_v6_runtime(
        tmp_path,
        gateway,
        topic=(
            "Research the current state, conclusions, limitations, and "
            "uncertainty of example technology"
        ),
        llm_call=blocking_llm,
    )
    run_task = asyncio.create_task(service.runner.run(run_id, state, context))
    await asyncio.wait_for(provider_started.wait(), timeout=10)
    running = await service.run_store.get_run(run_id)
    assert running is not None and running["status"] == "running"
    accepted = await service.execute_run_action(
        run_id,
        action_id="generate_now",
        idempotency_key="inflight-semantic-page-group-generate-now",
        expected_version=int(running["run_version"]),
        payload={},
    )
    assert accepted["accepted"] is True
    command_id = str(accepted["command"]["command_id"])
    provider_release.set()

    result = await asyncio.wait_for(run_task, timeout=30)
    assert result.error is None, await service.runner.history(run_id)
    assert result.output["values"]["answer_status"] == "insufficient_evidence"
    assert len(provider_calls) == 1
    assert len(result.output["values"]["page_result_refs"]) == 2
    assert len(gateway.fetch_service.requests) == 2

    blobs = context.ports["blob"]
    route_ref = str(result.output["values"]["route_decision_ref"])
    route = json.loads(
        (await blobs.get(route_ref.removeprefix("sha256:"))).decode("utf-8")
    )
    assert len(route["work_groups"]) == 2
    fact_batch_ref = str(result.output["values"]["fact_batch_refs"][0])
    fact_batch = json.loads(
        (await blobs.get(fact_batch_ref.removeprefix("sha256:"))).decode("utf-8")
    )
    assert len(fact_batch["candidate_slot_results"]) == 1
    producer_ref = fact_batch["candidate_slot_results"][0]["producer_outcome_ref"]
    producer = json.loads(
        (await blobs.get(producer_ref.removeprefix("sha256:"))).decode("utf-8")
    )
    assert producer["status"] == "malformed"
    assert producer["bundle_ref"] is None
    outcome_ref = producer["llm_effect_outcome_ref"]
    outcome = json.loads(
        (await blobs.get(outcome_ref.removeprefix("sha256:"))).decode("utf-8")
    )
    assert outcome["status"] == "malformed"
    assert outcome["raw_result_ref"] is not None

    command = await service.research_repository.get_control(command_id)
    assert command is not None and command["status"] == "consumed"
    assert all(
        command[name] is not None
        for name in ("accepted_at", "observed_at", "settled_at", "consumed_at")
    )


@pytest.mark.asyncio
async def test_real_runner_recovers_durable_accepted_generate_now_after_process_restart(
    tmp_path: Path,
) -> None:
    old_gateway = _BlockingOfficialGateway()
    old_service, run_id, old_state, old_context = await _controlled_v6_runtime(
        tmp_path, old_gateway
    )
    old_run_task = asyncio.create_task(
        old_service.runner.run(run_id, old_state, old_context)
    )
    await asyncio.wait_for(old_gateway.fetch_service.started.wait(), timeout=10)
    running = await old_service.run_store.get_run(run_id)
    assert running is not None
    accepted = await old_service.execute_run_action(
        run_id,
        action_id="generate_now",
        idempotency_key="restart-accepted-generate-now",
        expected_version=int(running["run_version"]),
        payload={},
    )
    command_id = str(accepted["command"]["command_id"])
    before_command = await old_service.research_repository.get_control(command_id)
    assert before_command is not None and before_command["status"] == "accepted"
    database = Path(old_service.run_store.path)
    async with aiosqlite.connect(database) as db:
        before_deadline = await (await db.execute(
            """SELECT deadline_id,run_id,logical_scope,policy_hash,budget_ms,
            created_at,wall_not_after,parent_deadline_id
            FROM workflow_research_deadlines
            WHERE run_id=? AND logical_scope='run:automatic'""",
            (run_id,),
        )).fetchone()
    assert before_deadline is not None

    # Simulate abrupt process loss: parent cancellation propagates, no old
    # control port settles the accepted command, and the lease remains owned by
    # the dead process until the durable expiry/takeover condition is observed.
    old_run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await old_run_task
    still_accepted = await old_service.research_repository.get_control(command_id)
    assert still_accepted is not None and still_accepted["status"] == "accepted"
    assert (
        still_accepted["accepted_at"], still_accepted["settle_deadline"]
    ) == (
        before_command["accepted_at"], before_command["settle_deadline"]
    )
    async with aiosqlite.connect(database) as db:
        await db.execute(
            "UPDATE workflow_runs SET lease_expires_at=0 WHERE run_id=?",
            (run_id,),
        )
        await db.commit()
    del old_run_task, old_context, old_service, old_gateway

    # A fresh composition creates a new run store, repository, runner, signal
    # hub and DurableV5ControlPort.  It receives no in-memory notification from
    # the old process and must recover solely from the accepted database row.
    new_gateway = _OfficialGateway()
    new_service, resumed_run_id, new_state, new_context = await _controlled_v6_runtime(
        tmp_path, new_gateway, existing_run_id=run_id
    )
    assert resumed_run_id == run_id
    result = await asyncio.wait_for(
        new_service.runner.run(run_id, new_state, new_context), timeout=30
    )
    assert result.error is None
    assert result.output["values"]["answer_status"] == "insufficient_evidence"
    assert result.output["values"]["page_result_refs"] == []
    assert new_gateway.fetch_service.requests == []

    after_command = await new_service.research_repository.get_control(command_id)
    assert after_command is not None and after_command["status"] == "consumed"
    assert (
        after_command["accepted_at"], after_command["settle_deadline"]
    ) == (
        before_command["accepted_at"], before_command["settle_deadline"]
    )
    assert all(
        after_command[name] is not None
        for name in ("observed_at", "settled_at", "consumed_at")
    )
    async with aiosqlite.connect(database) as db:
        after_deadline = await (await db.execute(
            """SELECT deadline_id,run_id,logical_scope,policy_hash,budget_ms,
            created_at,wall_not_after,parent_deadline_id
            FROM workflow_research_deadlines
            WHERE run_id=? AND logical_scope='run:automatic'""",
            (run_id,),
        )).fetchone()
    assert after_deadline == before_deadline


@pytest.mark.asyncio
async def test_real_runner_parent_cancelled_error_propagates_through_v6_load_pages(
    tmp_path: Path,
) -> None:
    gateway = _BlockingOfficialGateway()
    service, run_id, state, context = await _controlled_v6_runtime(tmp_path, gateway)
    run_task = asyncio.create_task(service.runner.run(run_id, state, context))
    await asyncio.wait_for(gateway.fetch_service.started.wait(), timeout=10)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert not gateway.fetch_service.release.is_set()


@pytest.mark.asyncio
async def test_bootstrap_registers_v6_definition_exactly_once(tmp_path: Path) -> None:
    from deskpet.workflows.bootstrap import build_workflow_service

    service = await build_workflow_service(tmp_path, activate=False)
    versions = service.runner.registry.versions()
    assert versions.count(("deep_research", "v6")) == 1
