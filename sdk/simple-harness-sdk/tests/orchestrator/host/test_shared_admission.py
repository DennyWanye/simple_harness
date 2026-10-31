# SPDX-License-Identifier: Apache-2.0
"""Host admission covers independent chat runtimes and mission pools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os

import pytest
from test_chat import _chat_script

from agent_orchestrator.deployment.assembly import DEFAULT_PROJECTION_BUDGET
from agent_orchestrator.host import HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import FixtureWordCounter, ScriptedProvider
from agent_orchestrator.orchestrator.taskgraph_assembly import TaskGraphDeploymentPorts
from agent_orchestrator.orchestrator.taskgraph_deployment import InstalledHtnWiringAcceptance
from simple_harness.contracts import canonical_json
from simple_harness.execution.provider_admission import LocalProviderAdmission


@pytest.fixture(autouse=True)
def local_process_identity(monkeypatch):
    # 同现有 Session 测试：本机恢复驱动不依赖运行器能否查询 ps。
    monkeypatch.setattr(
        "agent_orchestrator.orchestrator.recovery_coordinator.process_identity",
        lambda pid: {"process_started_at": "shared-admission-test"} if pid == os.getpid() else None,
    )


class CountingProvider(ScriptedProvider):
    def __init__(self):
        super().__init__(chat=_chat_script(["Answer"]))
        self.active = 0
        self.peak = 0
        self.call_count = 0
        self.changed = asyncio.Event()
        self.held.add("chat")

    async def invoke(self, request, *, cancel):
        self.active += 1
        self.call_count += 1
        self.peak = max(self.peak, self.active)
        self.changed.set()
        try:
            return await super().invoke(request, cancel=cancel)
        finally:
            self.active -= 1
            self.changed.set()

    async def wait_for_calls(self, count):
        async def wait():
            while self.call_count < count:
                self.changed.clear()
                await self.changed.wait()

        await asyncio.wait_for(wait(), 10)


def current_source_ports(tmp_path, monkeypatch):
    """Exercise the real reader on current bytes, without editing the release manifest."""
    reader = InstalledHtnWiringAcceptance()
    value = json.loads(reader.manifest.read_text())
    value["source_files"] = {
        name: hashlib.sha256((reader.root / name).read_bytes()).hexdigest()
        for name in value["source_files"]
    }
    value["taskgraph_acceptance"] = "NOT_RUN"
    value["acceptance_evidence"] = None
    identity = {key: item for key, item in value.items() if key != "deployment_id"}
    value["deployment_id"] = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    reader.manifest = tmp_path / "test-source-manifest.json"
    reader.manifest.write_text(json.dumps(value))
    reader._read()
    original_init = InstalledHtnWiringAcceptance.__init__

    def use_test_manifest(self):
        original_init(self)
        self.manifest = reader.manifest

    # 恢复流程也自行构建读取器，必须核验同一份当前源码清单。
    monkeypatch.setattr(InstalledHtnWiringAcceptance, "__init__", use_test_manifest)
    return TaskGraphDeploymentPorts(
        tenant_id="local-host",
        principal=Principal("u", "User"),
        deployment_acceptance=reader,
        graph_budget=DEFAULT_PROJECTION_BUDGET,
    )


async def open_host(root, provider, slots=1, **options):
    return await OrchestrationHost.open(
        root,
        principal=Principal("u", "User"),
        provider=provider,
        counter=FixtureWordCounter(),
        settings=HostSettings(max_concurrent_model_calls=slots),
        require_acceptance=False,
        **options,
    )


def chat(host, session_id="host-chat", key="one"):
    return host.chat({"session_id": session_id, "text": "Hello", "idempotency_key": key})


@pytest.mark.parametrize("slots", [1, 2])
def test_two_sessions_and_host_chat_share_host_limit(tmp_path, monkeypatch, slots):
    async def run():
        attempted = set()
        all_attempted = asyncio.Event()
        acquire = LocalProviderAdmission.acquire

        async def counted(self, **kwargs):
            attempted.add(kwargs["record"].invocation_id)
            if len(attempted) == 3:
                all_attempted.set()
            return await acquire(self, **kwargs)

        monkeypatch.setattr(LocalProviderAdmission, "acquire", counted)
        provider = CountingProvider()
        host = await open_host(tmp_path, provider, slots)
        jobs = []
        try:
            alpha = await host.open_session("alpha")
            beta = await host.open_session("beta")
            jobs = [
                asyncio.create_task(alpha.send("Hello", key="one")),
                asyncio.create_task(beta.send("Hello", key="one")),
                asyncio.create_task(chat(host)),
            ]
            await asyncio.wait_for(all_attempted.wait(), 10)
            await provider.wait_for_calls(slots)
            assert provider.active == provider.peak == slots
            provider.release.set()
            replies = await asyncio.wait_for(asyncio.gather(*jobs), 10)
            assert all(reply["state"] == "committed" for reply in replies)
            assert provider.call_count == 3 and provider.active == 0 and provider.peak == slots
        finally:
            provider.release.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_cancel_inflight_releases_slot_for_another_runtime(tmp_path):
    async def run():
        provider = CountingProvider()
        host = await open_host(tmp_path, provider)
        jobs = []
        try:
            session = await host.open_session("next")
            jobs.append(asyncio.create_task(chat(host)))
            await provider.wait_for_calls(1)
            jobs.append(asyncio.create_task(session.send("Hello", key="one")))
            cancelled = await host.chat_cancel({"session_id": "host-chat"})
            assert cancelled["cancelled"]
            provider.release.set()
            first, second = await asyncio.wait_for(asyncio.gather(*jobs), 10)
            assert first["state"] == "failed" and second["state"] == "committed"
            assert provider.call_count == 2 and provider.peak == 1 and provider.active == 0
        finally:
            provider.release.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_host_close_cancels_held_call_and_reopen_has_fresh_slots(tmp_path):
    async def run():
        provider = CountingProvider()
        host = await open_host(tmp_path, provider)
        session = await host.open_session("held")
        job = asyncio.create_task(session.send("Hello", key="one"))
        try:
            await provider.wait_for_calls(1)
            await asyncio.wait_for(host.close(), 10)
            assert not provider.release.is_set() and provider.active == 0
            provider.held.clear()
            reopened = await open_host(tmp_path, provider)
            try:
                assert (await asyncio.wait_for(chat(reopened, key="two"), 10))["state"] == (
                    "committed"
                )
                assert provider.call_count == 2 and provider.peak == 1
            finally:
                await reopened.close()
        finally:
            provider.release.set()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_closing_session_keeps_host_gate_and_other_pools_usable(tmp_path):
    async def run():
        provider = CountingProvider()
        provider.held.clear()
        host = await open_host(tmp_path, provider)
        try:
            alpha = await host.open_session("alpha")
            beta = await host.open_session("beta")
            admission = host.chat_service._pool().runtime.effective_provider_admission
            assert alpha._chat._pool().runtime.effective_provider_admission is admission
            assert beta._chat._pool().runtime.effective_provider_admission is admission
            await alpha.close()
            assert (await beta.send("Hello", key="one"))["state"] == "committed"
            assert (await chat(host))["state"] == "committed"
            assert host.chat_service._pool().runtime.effective_provider_admission is admission
        finally:
            await host.close()

    asyncio.run(run())


def test_chat_profile_ids_match_main_before_injection(tmp_path):
    # Extracted from 169363812: test_session.py and SessionChatService._profile_id.
    async def run():
        host = await open_host(tmp_path, CountingProvider())
        try:
            session = await host.open_session("alpha")
            assert host.chat_service.profile_id == "deepseek-native-600k-chat-v1"
            assert session._chat.profile_id == "deepseek-native-600k-chat-v3-c2-9f49f4fc1de3"
        finally:
            await host.close()

    asyncio.run(run())


def test_mission_and_chat_share_capacity_and_keep_budget_identity(tmp_path, monkeypatch):
    async def run():
        attempted = set()
        both_attempted = asyncio.Event()
        acquire = LocalProviderAdmission.acquire

        async def counted(self, **kwargs):
            attempted.add(kwargs["record"].invocation_id)
            if len(attempted) >= 2:
                both_attempted.set()
            return await acquire(self, **kwargs)

        monkeypatch.setattr(LocalProviderAdmission, "acquire", counted)
        provider = CountingProvider()
        provider.held.update(provider._answer)
        host = await open_host(
            tmp_path, provider, taskgraph_ports=current_source_ports(tmp_path, monkeypatch)
        )
        jobs = []
        try:
            shared = host.chat_service._pool().runtime.effective_provider_admission
            for profile_id, pool in host.orchestrator.assembled.pools.items():
                admission = pool.runtime.effective_provider_admission
                assert admission.shared is shared
                assert admission.admission is host.orchestrator._admission_for(profile_id)
                assert admission.fingerprint == admission.admission.fingerprint
            created = host.create_mission(
                {
                    "goal": "Write NOTES.md",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "mission",
                }
            )
            jobs.append(
                asyncio.create_task(host.run_until_settled(created["mission_id"], timeout=30))
            )
            await provider.wait_for_calls(1)
            jobs.append(asyncio.create_task(chat(host)))
            await asyncio.wait_for(both_attempted.wait(), 10)
            assert provider.active == provider.peak == provider.call_count == 1
            provider.release.set()
            mission, reply = await asyncio.wait_for(asyncio.gather(*jobs), 40)
            assert mission["snapshot"]["mission"]["status"] == "COMPLETED"
            assert reply["state"] == "committed" and provider.peak == 1
        finally:
            provider.release.set()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_cancel_waiting_turn_does_not_call_provider_or_leak_slot(tmp_path, monkeypatch):
    async def run():
        queued = asyncio.Event()
        acquire = LocalProviderAdmission.acquire
        attempted = 0

        async def counted(self, **kwargs):
            nonlocal attempted
            attempted += 1
            if attempted == 2:
                queued.set()
            return await acquire(self, **kwargs)

        monkeypatch.setattr(LocalProviderAdmission, "acquire", counted)
        provider = CountingProvider()
        host = await open_host(tmp_path, provider)
        jobs = []
        try:
            session = await host.open_session("held")
            jobs.append(asyncio.create_task(session.send("Hello", key="one")))
            await provider.wait_for_calls(1)
            jobs.append(asyncio.create_task(chat(host)))
            await asyncio.wait_for(queued.wait(), 10)
            assert provider.call_count == 1
            assert (await host.chat_cancel({"session_id": "host-chat"}))["cancelled"]
            provider.release.set()
            held, cancelled = await asyncio.wait_for(asyncio.gather(*jobs), 10)
            assert held["state"] == "committed" and cancelled["state"] == "failed"
            assert provider.call_count == 1
            assert (await asyncio.wait_for(chat(host, key="two"), 10))["state"] == "committed"
            assert provider.call_count == 2 and provider.peak == 1
        finally:
            provider.release.set()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_provider_failure_releases_slot_for_next_runtime(tmp_path):
    from simple_harness.providers.errors import ProviderRequestRejectedError

    def reject(request):
        raise ProviderRequestRejectedError(status_code=403)

    async def run():
        provider = CountingProvider()
        provider.held.clear()
        provider._answer["chat"] = reject
        host = await open_host(tmp_path, provider)
        try:
            assert (await chat(host))["state"] == "failed"
            provider._answer["chat"] = _chat_script(["Answer"])
            session = await host.open_session("next")
            assert (await asyncio.wait_for(session.send("Hello", key="one"), 10))["state"] == (
                "committed"
            )
            assert provider.call_count == 2 and provider.peak == 1
        finally:
            await host.close()

    asyncio.run(run())


def test_same_invocation_ids_in_distinct_databases_release_both_slots(tmp_path):
    async def run():
        provider = CountingProvider()
        host = await open_host(tmp_path, provider, 2)
        jobs = []
        try:
            session = await host.open_session("same")
            jobs = [
                asyncio.create_task(session.send("Hello", key="one")),
                asyncio.create_task(chat(host, session_id="same")),
            ]
            await provider.wait_for_calls(2)
            session_pool = session._chat._pool()
            chat_pool = host.chat_service._pool()
            session_run = next(iter(session._chat._runs))
            chat_run = next(iter(host.chat_service._runs))
            assert session_run == chat_run
            assert {
                row[1] for row in session._chat._invocation_rows(session_pool, session_run)
            } == {row[1] for row in host.chat_service._invocation_rows(chat_pool, chat_run)}
            provider.release.set()
            replies = await asyncio.wait_for(asyncio.gather(*jobs), 10)
            assert all(reply["state"] == "committed" for reply in replies)
            provider.release.clear()
            jobs = [
                asyncio.create_task(session.send("Again", key="two")),
                asyncio.create_task(chat(host, session_id="same", key="two")),
            ]
            await provider.wait_for_calls(4)
            assert provider.active == provider.peak == 2
            provider.release.set()
            replies = await asyncio.wait_for(asyncio.gather(*jobs), 10)
            assert all(reply["state"] == "committed" for reply in replies)
        finally:
            provider.release.set()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_rebuild_keeps_shared_gate_while_session_holds_capacity(tmp_path):
    async def run():
        provider = CountingProvider()
        host = await open_host(tmp_path, provider)
        job = None
        try:
            session = await host.open_session("held")
            shared = session._chat._pool().runtime.effective_provider_admission
            job = asyncio.create_task(session.send("Hello", key="one"))
            await provider.wait_for_calls(1)
            await host._rebuild()
            assert host.chat_service._pool().runtime.effective_provider_admission is shared
            for pool in host.orchestrator.assembled.pools.values():
                assert pool.runtime.effective_provider_admission.shared is shared
            provider.release.set()
            assert (await asyncio.wait_for(job, 10))["state"] == "committed"
            assert (await asyncio.wait_for(chat(host), 10))["state"] == "committed"
            assert provider.peak == 1
        finally:
            provider.release.set()
            if job is not None:
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_budget_admission_denial_releases_shared_slot_before_handoff(tmp_path, monkeypatch):
    from simple_harness.execution.provider_admission import ProviderAdmissionDenied

    async def run():
        provider = CountingProvider()
        provider.held.clear()
        host = await open_host(
            tmp_path, provider, taskgraph_ports=current_source_ports(tmp_path, monkeypatch)
        )
        denied = asyncio.Event()
        job = None

        async def reject(**kwargs):
            denied.set()
            raise ProviderAdmissionDenied()

        try:
            for pool in host.orchestrator.assembled.pools.values():
                admission = pool.runtime.effective_provider_admission
                monkeypatch.setattr(admission.admission, "acquire", reject)
            created = host.create_mission(
                {
                    "goal": "Write NOTES.md",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "denied-mission",
                }
            )
            job = asyncio.create_task(host.run_until_settled(created["mission_id"], timeout=20))
            await asyncio.wait_for(denied.wait(), 10)
            host.cancel(created["mission_id"])
            assert provider.call_count == 0
            assert (await asyncio.wait_for(chat(host), 10))["state"] == "committed"
            assert provider.call_count == provider.peak == 1
        finally:
            if job is not None:
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_persisted_empty_admission_fingerprint_is_refused(tmp_path):
    import sqlite3

    from agent_orchestrator.runtime.assembly import assemble_orchestrator_runtime

    async def run():
        host = await open_host(tmp_path, CountingProvider())
        try:
            chat_service = host.chat_service
            config = chat_service._assembled.config
            profile = chat_service._pool().profile
            with sqlite3.connect(config.orchestrator_db) as connection:
                connection.execute("CREATE TABLE dispatch_intents(config_json TEXT)")
                connection.execute(
                    "INSERT INTO dispatch_intents VALUES(?)",
                    (
                        json.dumps(
                            {
                                "runtime_profile_id": profile.profile_id,
                                "runtime_context": profile.context_snapshot(),
                                "provider_admission_fingerprint": None,
                            }
                        ),
                    ),
                )
            with pytest.raises(ValueError, match="provider admission identity differs"):
                assemble_orchestrator_runtime(
                    config,
                    profiles={profile.profile_id: profile},
                    default_profile=profile.profile_id,
                    provider_admission=host._provider_admission,
                )
        finally:
            await host.close()

    asyncio.run(run())
