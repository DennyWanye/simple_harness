"""One isolated, in-process main-factory scoring session; never opens oracle files.

No application server or Tauri is started. C07 authored-recent setup alone owns
its bounded loopback fixture Provider; scoring uses a separate real binding.
Run only as a child of corpus_scoring under the shared resource owner.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from deskpet.quality.corpus_trace import wire, digest


def write_result(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(wire(value), handle, ensure_ascii=False, sort_keys=True, indent=2,
                  allow_nan=False)
        handle.write("\n")


class RedactedStream:
    """Redact before log bytes leave this process, including import-time logs."""
    def __init__(self, stream, key):
        self.stream, self.key, self.pending = stream, key, ""
    def write(self, value):
        self.pending += str(value)
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self._write_line(line + "\n")
        return len(value)
    def _write_line(self, value):
        safe = value.replace(self.key, "[REDACTED]")
        safe = re.sub(r"(?i)(SHARED_SECRET[=: ]+)[^\s]+", r"\1[REDACTED]", safe)
        safe = re.sub(r"(?i)(Bearer\s+)[^\s\"\\]+", r"\1[REDACTED]", safe)
        return self.stream.write(safe)
    def flush(self):
        self.stream.flush()


def configure_process(directory, host_root, *, initialize_only=False):
    # No generic .env loading; only the two authorized fields are consumed.
    # The key is neither copied into config/userdata nor written to keychain.
    from dotenv import dotenv_values
    import tomlkit
    values = {"APIKEY": "corpus-offline-not-a-key", "BASEURL": "http://127.0.0.1:9/v1"} \
        if initialize_only else dotenv_values(Path("/Users/denny/projects/simple_harness/.env"), interpolate=False)
    key, base_url = values.get("APIKEY"), values.get("BASEURL")
    if not key or not base_url:
        raise ValueError("corpus_process_credentials_missing")
    from urllib.parse import urlsplit
    endpoint = urlsplit(base_url)
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("corpus_endpoint_must_not_contain_credentials_or_query")
    sys.stdout = RedactedStream(sys.stdout, key)
    sys.stderr = RedactedStream(sys.stderr, key)
    runtime_root = directory / "runtime"
    runtime_root.mkdir(exist_ok=False)
    config = tomlkit.parse((host_root / "config.toml").read_text())
    def remove_secrets(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if re.fullmatch(r"(?i)(api_?key|access_token|refresh_token|password|secret|token)", str(name)):
                    value[name] = ""
                else:
                    remove_secrets(item)
        elif isinstance(value, list):
            for item in value:
                remove_secrets(item)
    remove_secrets(config)
    # Do not inherit an unrelated configured Provider chain from the main config.
    # The registry below admits exactly one process-only real Provider.
    config["llm"].pop("endpoints", None)
    # Model/window/output parameters remain the actual source configuration.
    # Transport endpoint only is replaced by the authorized process endpoint.
    config["llm"]["base_url"] = base_url
    config_path = runtime_root / "config.toml"
    config_path.write_text(tomlkit.dumps(config))
    for name in tuple(os.environ):
        if name.startswith("DESKPET_"):
            del os.environ[name]
    os.environ.update(DESKPET_CONFIG=str(config_path),
        DESKPET_BACKEND_DIR=str(host_root / "backend"),
        DESKPET_USER_DATA_DIR=str(runtime_root / "userdata"),
        DESKPET_USER_LOG_DIR=str(runtime_root / "logs"),
        DESKPET_USER_CACHE_DIR=str(runtime_root / "cache"),
        DESKPET_CLOUD_API_KEY=key, DESKPET_DEV_MODE="0",
        DESKPET_DEEPRESEARCH_DIR=str(runtime_root / "deepresearch"))
    return key, base_url


async def collect_turn(main, service, subject, queued, text, *, directory):
    """Host DB provides identity only; terminal/proposals come from public SDK."""
    with sqlite3.connect(f"file:{main._state_db_path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT r.host_run_id,b.sdk_run_id FROM foreground_runs r "
            "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
            "WHERE r.turn_id=? AND r.subject=?", (queued["turn_ref"], subject)).fetchall()
    if len(rows) != 1:
        raise ValueError("corpus_exact_run_binding_missing_or_ambiguous")
    row = dict(rows[0])
    write_result(directory / "observation-run-identity.json", row)

    def route_reader():
        from deskpet.sdk_adapters.context_authority import canonical_sha256
        with sqlite3.connect(f"file:{main._state_db_path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            route_rows = db.execute("SELECT * FROM context_route_tool_invocations "
                "WHERE sdk_run_id=? ORDER BY recorded_at,effect_id LIMIT 257",
                (row["sdk_run_id"],)).fetchall()
        if len(route_rows) > 256:
            raise ValueError("corpus_route_trace_limit")
        routes = []
        for route_row in route_rows:
            route = dict(route_row)
            detail = json.loads(route["detail_json"])
            content = dict(decision_id=route["decision_id"], detail=detail,
                effect_id=route["effect_id"], proposal_hash=route["proposal_hash"],
                raw_call_id=route["raw_call_id"], sdk_run_id=row["sdk_run_id"], verdict=route["verdict"])
            if canonical_sha256(content) != route["invocation_hash"]:
                raise ValueError("corpus_route_trace_hash")
            routes.append({**content, "invocation_hash": route["invocation_hash"]})
        return routes

    from deskpet.quality.corpus_trace import collect_bound_observations
    observations = await collect_bound_observations(stack=main._sdk_runtime_stack,
        run_id=row["sdk_run_id"], text=text, route_reader=route_reader,
        queue_reader=service.queue_snapshot,
        persist=lambda name, value: write_result(directory / ("observation-" + name + ".json"), value))
    return {**row, **observations}


class _InitializationComplete(Exception):
    """Leave the preparation block through the same resource cleanup path."""


async def run(directory, host_root, key, base_url, *, initialize_only=False):
    # Explicit input files only. Parent's original oracle/case archive never
    # becomes a constructor argument, system instruction, tool schema or seed.
    authored = json.loads((directory / "input.json").read_text())
    setup = json.loads((directory / "setup.json").read_text())
    case_id = setup["case_id"]
    c07 = c05 = c08_retained = None
    c05_schedule = None
    c05_input = None
    if setup["scenario_clock"] != authored["scenario_clock"]:
        raise ValueError("corpus_input_setup_clock_mismatch")
    if type(case_id) is str and case_id.startswith("C07-"):
        from deskpet.quality.corpus_c07 import compile_c07_setup
        from deskpet.quality.corpus_c07_prepare import validate_c07_recent_input
        c07 = compile_c07_setup(case_id, setup["setup_source_text"],
            scenario_clock=setup["scenario_clock"]["instant"])
        validate_c07_recent_input(c07, authored["recent_messages"])
    if type(case_id) is str and case_id.startswith("C05-"):
        from deskpet.quality.corpus_c05 import compile_c05_setup
        from deskpet.quality.corpus_c05_session import validate_schedule
        c05 = compile_c05_setup(case_id, setup["setup_source_text"])
        c05_schedule = validate_schedule(case_id, json.loads((directory / "scheduler.json").read_text()))
        from deskpet.quality.corpus_c05_state_phase import compile_c05_initial_input
        c05_input = compile_c05_initial_input(c05, authored)
    if type(case_id) is str and case_id.startswith("C08-"):
        # Only source-complete retained carriers enter this phase. Other C08
        # scalar/partial carriers are not made ready by an empty placeholder.
        from deskpet.quality.corpus_c08_retained import compile_c08_retained_setup
        c08_retained = compile_c08_retained_setup(case_id, setup["setup_source_text"],
            scenario_clock=setup["scenario_clock"]["instant"])
    if c05_input is None and ((authored["recent_messages"] and c07 is None) or authored["unresolved_source_text"] is not None):
        raise ValueError("corpus_first_batch_scalar_input_required")
    text = authored["current_user_message"] if c05_input is None else c05_input['current_user_message']
    if type(text) is not str or not text:
        raise ValueError("corpus_original_user_message_required")
    scenario_time = datetime.fromisoformat(authored["scenario_clock"]["instant"]).timestamp()
    clock = lambda: scenario_time

    from deskpet.memory.schema import dispatch_startup_epoch
    # Fresh epoch must be established before main's module-level legacy openers.
    state_path = directory / "runtime" / "userdata" / "data" / "state.db"
    epoch = await dispatch_startup_epoch(state_path, approved_fresh_lane=True)
    import main
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
    from deskpet.sdk_adapters.context_route import local_owner_auth
    from deskpet.quality.corpus_c01 import compile_setup
    from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority, prepare_runtime_seed
    if case_id.startswith("C07-"):
        from deskpet.quality.corpus_c07 import compile_c07_setup as compile_setup
        from deskpet.quality.corpus_c07_prepare import prepare_c07_seed as prepare_runtime_seed
    elif case_id == "C01-06":
        from deskpet.quality.corpus_c01_revision_prepare import compile_c01_revision_setup as compile_setup
    from deskpet.memory.human_memory_v7 import local_memory_principal, host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES
    from deskpet.memory.evidence_authority import HostEvidenceAuthority
    from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
    from deskpet.quality.corpus_runtime import execute_scoring_turn
    from deskpet.workflows.bootstrap import build_workflow_service
    from llm.provider_registry import LLMProviderRegistry
    import simple_harness_memory as memory

    outcome = dict(execution_status="SETUP_NOT_READY", trace=None,
        scenario_clock=authored["scenario_clock"],
        provider_clock_projection="CONFIGURED_MAIN_PRIMARY_SEMANTIC_CLOCK_NOT_OBSERVED",
        embedding="NO_LOCAL_MODEL_LOADED", initialization="main_product_factory",
        cleanup_errors=[])
    if c05 is not None or c08_retained is not None:
        # The fixture owns LLM responses, not the main embedder's lifecycle.
        # Model-load absence is established by the separate controlled carrier.
        outcome["embedding"] = "MAIN_EMBEDDER_LOAD_NOT_OBSERVED"
    service = None
    recent_provider = task_provider = retained_provider = None
    task_history = None
    task_workspace = directory / "runtime" / "task-workspace" if c05 is not None else None
    try:
        outcome["stage"] = "host_schema_session"
        if main._state_db_path.resolve() != state_path.resolve():
            raise ValueError("corpus_main_state_path_differs")
        from deskpet.memory.schema import StartupCompositionMode
        if epoch.composition_mode is not StartupCompositionMode.HUMAN:
            raise ValueError("corpus_requires_actual_human_epoch")
        # Same owning initializer used by actual main lifespan, no fake manager.
        await main._initialize_product_memory()
        embedder = main.service_context.get("embedder")
        await main._session_db.initialize()
        outcome["stage"] = "process_provider_registry"
        from llm.resolution import ProviderRoutingReadiness
        readiness = ProviderRoutingReadiness()
        main.service_context.register("provider_routing_readiness", readiness)
        main._provider_registry = LLMProviderRegistry(main._CONFIG_PATH)
        if main._provider_registry.list_providers():
            raise ValueError("corpus_requires_empty_isolated_provider_registry")
        if c05 is not None and not initialize_only:
            from deskpet.quality.corpus_c05_transport import TaskSetupHttpProvider
            task_provider = await TaskSetupHttpProvider(model=main.config.llm.local.model).start()
            await main._provider_registry.add_ephemeral_provider(task_provider.registration())
        elif c08_retained is not None and not initialize_only:
            from deskpet.quality.corpus_c08_retained import RetainedSummaryProvider
            retained_provider = await RetainedSummaryProvider(c08_retained,
                model=main.config.llm.local.model).start()
            await main._provider_registry.add_ephemeral_provider(retained_provider.registration())
        elif c07 is not None and c07.recent_messages and not initialize_only:
            from deskpet.quality.corpus_c07_phase import RecentMessagesProvider
            recent_provider = await RecentMessagesProvider(c07.recent_messages,
                model=main.config.llm.local.model).start()
            await main._provider_registry.add_ephemeral_provider(recent_provider.registration())
        else:
            await main._provider_registry.add_ephemeral_provider(dict(id="corpus-real-provider",
                base_url=base_url, api_key=key, models=[main.config.llm.local.model]))
        main.service_context.register("provider_registry", main._provider_registry)
        await main._session_db.reconcile_provider_bindings({entry["id"]:
            (str(entry["incarnation_id"]), int(entry["config_revision"]))
            for entry in main._provider_registry.list_providers()},
            registry_digest=main._provider_registry.snapshot_digest())
        readiness.mark_ready()
        outcome["stage"] = "workflow_capability_growth"
        workflow = await build_workflow_service(main._paths.user_data_dir(), activate=False,
            session_delivery_state_reader=main._session_db.get_session_delivery_state)
        main.service_context.register("workflow_service", workflow)
        main._workflow_service = workflow
        from deskpet.retrieval.runtime import build_search_gateway, set_default_gateway
        gateway = build_search_gateway(main.config)
        set_default_gateway(gateway)
        main.service_context.register("search_gateway", gateway)
        await main._initialize_capability_runtime()
        await main._initialize_growth_authority()

        outcome["stage"] = "public_setup"
        auth = local_owner_auth()
        # Same complete Host factory as main lifespan; context_route resolves
        # this registry entry later, rather than the local facade variable.
        async def suppression(candidate, purpose):
            actual = main.service_context.get("human_memory_v7_runtime")
            manager = await actual.manager()
            return await manager.backend.resolve_suppression(candidate, purpose,
                principal=actual.principal())
        from deskpet.memory.display_invalidation import MemoryDisplayInvalidation
        factory = HumanMemoryHostServiceFactory(main._state_db_path, epoch,
            settled_run_reader=lambda run_id, **kwargs: main._sdk_runtime_stack.read_settled_primary_run(run_id, **kwargs),
            suppression_resolver=suppression,
            history_visibility_checker=main._primary_history_visibility_checker,
            run_binding_reader=lambda run_id: main._sdk_runtime_stack.read_closure_run_facts(run_id).binding_record,
            decision_ingress_getter=lambda: main._sdk_ingress,
            cognitive_runtime_getter=lambda: main.service_context.get("human_memory_v7_runtime"),
            display_invalidation=MemoryDisplayInvalidation(main._broadcast_control))
        main.service_context.register("human_memory_host_service_factory", factory)
        if c05 is not None or c08_retained is not None or case_id.startswith("C07-") or case_id == "C01-06":
            await factory.bind(auth).open_primary()
        if c05 is not None:
            # Task archives require the real main runtime. No empty scalar seed
            # is substituted for those source/closure obligations.
            outcome["task_phase_status"] = "NOT_RUN"
        elif c08_retained is not None:
            # C08-01 also skips its old scalar seed: A and the retained summary
            # must derive from this actual old USER in the same store.
            outcome["retained_phase_status"] = "NOT_RUN"
        else:
            batch = compile_setup(case_id, setup["setup_source_text"],
                scenario_clock=authored["scenario_clock"]["instant"])
            # Fixture authorities are owned only by this setup scope. Close them
            # before main reopens the same database with production authorities.
            async with AsyncExitStack() as fixture_owners:
                memory_path = main._paths.user_data_dir() / "data" / "human_memory_v7.db"
                if case_id == "C01-06":
                    from deskpet.quality.corpus_c01_revision_prepare import open_c01_revision_fixture
                    _, seed = await fixture_owners.enter_async_context(open_c01_revision_fixture(
                        path=main._state_db_path, memory_path=memory_path,
                        principal=local_memory_principal(), authority_ref=auth.authority_ref,
                        batch=batch, classification_policy=host_classification_policy(),
                        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES))
                else:
                    delivery = SetupFixtureDeliveryAuthority()
                    fixture_manager = await memory.build_human_memory_v7(
                        memory_path, classification_policy=host_classification_policy(),
                        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES,
                        evidence_authority=HostEvidenceAuthority(main._state_db_path),
                        analysis_delivery_authority=delivery, clock=clock)
                    fixture_owners.push_async_callback(fixture_manager.close)
                    seed = await prepare_runtime_seed(path=main._state_db_path, manager=fixture_manager,
                        principal=local_memory_principal(), authority_ref=auth.authority_ref,
                        batch=batch, delivery_authority=delivery)
                outcome["setup_receipt"] = wire({name: seed[name] for name in
                    ("source_pair", "labels", "setup_hash", "outcome", "fixture_executions")})
                if case_id.startswith("C07-"):
                    outcome["setup_receipt"].update(wire({name: seed[name] for name in
                        ("manifest_hash", "fixture_defaults", "application", "request")}))
                elif case_id == "C01-06":
                    outcome["setup_receipt"].update(wire({name: seed[name] for name in
                        ("ingestion_receipt", "application", "request", "initial_plan", "plan",
                         "old_receipt", "new_receipt", "old_receipt_ref", "new_receipt_ref")}))
        # Reopen with actual production authorities. Never replace the production
        # analysis authority with the local setup executor during scoring.
        outcome["stage"] = "main_product_runtime_factory"
        if task_workspace is not None:
            task_workspace.mkdir(exist_ok=False)
        await main._activate_product_sdk_runtime(clock=clock,
            **({"configured_workspace_root": task_workspace} if task_workspace is not None else {}))
        # Isolate foreground scoring from unrelated background model analysis.
        await main._memory_analysis_lane.close()
        outcome["stage"] = "main_foreground_factory"
        if c05 is not None:
            from deskpet.execution.primary_history import PrimaryHistoryStore
            from deskpet.quality.corpus_c05_history import C05PhaseHistoryReader
            task_history = C05PhaseHistoryReader(path=main._state_db_path, subject=auth.subject,
                delegate=PrimaryHistoryStore(main._state_db_path,
                    settled_run_reader=main._sdk_runtime_stack.read_settled_primary_run,
                    policy=main._primary_history_policy(auth.subject)))
        await main._activate_human_memory_host_ports(epoch,
            **({"history_reader": task_history, "configured_workspace_root": task_workspace}
               if c05 is not None else {}))
        # Original main startup barrier: bind the real companion adapter before
        # accepting any foreground Run. This does not start background workers.
        await main._activate_companion_runtime_adapter_and_open_ingress()
        runtime = main.service_context.get("human_memory_foreground_runtime_execution_authority")
        cognitive = main.service_context.get("human_memory_v7_runtime")
        if runtime is None or cognitive is None:
            raise ValueError("corpus_actual_foreground_authority_missing")
        service = factory.bind(auth)
        if (await service.queue_snapshot())["turns"]:
            raise ValueError("corpus_scoring_history_not_empty")
        if initialize_only:
            if c07 is not None and c07.recent_messages:
                outcome["recent_phase_status"] = "NOT_RUN_INITIALIZE_ONLY"
            outcome["embedder_status"] = embedder.status_snapshot()
            outcome["execution_status"] = "INITIALIZED_NOT_EXECUTED"
            raise _InitializationComplete()
        worker = MemoryIngestionOutboxWorker(main._state_db_path, cognitive.manager,
            owner_id="corpus-scoring-user-ingestion")
        if task_provider is not None:
            from deskpet.quality.corpus_c05_session import prepare_setup_phase
            from deskpet.quality.corpus_c07_phase import admit_scoring_provider
            outcome["stage"] = "authored_task_setup_phase"
            outcome["setup_phase"] = await prepare_setup_phase(main=main, batch=c05,
                service=service, runtime=runtime, transport=task_provider, history_reader=task_history,
                workspace_root=task_workspace, directory=directory, collect_turn=collect_turn,
                record=write_result, ingestion_worker=worker)
            from deskpet.quality.corpus_c05_state_phase import bind_c05_initial_input
            input_binding = bind_c05_initial_input(c05_input, outcome['setup_phase'])
            if input_binding is not None:
                outcome['setup_phase']['authored_input_binding'] = input_binding
                write_result(directory / 'authored-input-binding.json', input_binding)
            outcome["task_phase_status"] = "CONFIRMED"
            await task_provider.close()
            await admit_scoring_provider(registry=main._provider_registry,
                resolver=main._sdk_provider_binding_resolver, session_db=main._session_db,
                base_url=base_url, key=key, model=main.config.llm.local.model)
        if retained_provider is not None:
            from deskpet.quality.corpus_c08_retained_phase import execute_retained_phase
            from deskpet.quality.corpus_c07_phase import admit_scoring_provider
            outcome["stage"] = "authored_retained_source_phase"
            phase, seed = await execute_retained_phase(main=main, service=service, runtime=runtime,
                batch=c08_retained, worker=worker, directory=directory, provider=retained_provider,
                collect_turn=collect_turn, record=write_result)
            if phase["status"] != "CONFIRMED" or not seed["setup_complete"]:
                raise ValueError("c08_retained_phase_not_confirmed")
            outcome["setup_phase"] = phase
            outcome["setup_receipt"] = wire({name: seed[name] for name in
                ("setup_complete", "manifest_hash", "setup_hash", "labels", "source_pair", "outcome",
                 "fixture_executions", "mutation_receipt_ref", "suppression_decision", "fixture_defaults")})
            outcome["retained_phase_status"] = "CONFIRMED"
            # Helper has joined the fixture HTTP owner, closed its temporary
            # manager and reopened main's original production authority.
            await admit_scoring_provider(registry=main._provider_registry,
                resolver=main._sdk_provider_binding_resolver, session_db=main._session_db,
                base_url=base_url, key=key, model=main.config.llm.local.model)
        if recent_provider is not None:
            from deskpet.quality.corpus_c07_phase import execute_recent_phase, admit_scoring_provider
            outcome["stage"] = "authored_recent_phase"
            outcome["setup_phase"] = await execute_recent_phase(main=main, service=service,
                runtime=runtime, batch=c07, recent_messages=authored["recent_messages"], worker=worker,
                directory=directory, provider=recent_provider, collect_turn=collect_turn, record=write_result)
            # Stop the local response authority before admitting the actual
            # scoring Provider. Frozen old binding/terminal/audit remain intact.
            await recent_provider.close()
            await admit_scoring_provider(registry=main._provider_registry,
                resolver=main._sdk_provider_binding_resolver, session_db=main._session_db,
                base_url=base_url, key=key, model=main.config.llm.local.model)
        outcome["execution_status"] = "DISPATCH_STARTED"
        outcome["stage"] = "original_scoring_turn"
        if c05 is not None:
            from deskpet.quality.corpus_c05_session import execute_task_scoring
            outcome.update(await execute_task_scoring(main=main, service=service, runtime=runtime,
                subject=auth.subject, text=text, schedule=c05_schedule, directory=directory,
                setup_phase=outcome["setup_phase"], worker=worker, collect_turn=collect_turn, record=write_result))
        else:
            from deskpet.quality.corpus_approval import ReadOnlyMemoryApproval
            approval = ReadOnlyMemoryApproval(ingress=main._sdk_ingress,
                persist=lambda name, value: write_result(directory / (name + ".json"), value))
            try:
                executed = await execute_scoring_turn(service=service, runtime=runtime,
                    scoring_path=main._state_db_path, subject=auth.subject, text=text,
                    delivery_key="scoring-turn-1", ingestion_worker=worker,
                    approval_driver=approval)
                outcome["queue_receipt"] = wire(executed.queue_receipt)
                outcome["completed_group"] = wire(executed.completed_group)
                outcome["stage"] = "post_terminal_public_trace"
                outcome["actual_completed_group_available"] = True
                outcome.update(await collect_turn(main, service, auth.subject, executed.queue_receipt,
                    text, directory=directory))
                if recent_provider is not None or retained_provider is not None:
                    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
                    binding = SdkRunBindingV1.from_record(main._sdk_runtime_stack.read_closure_run_facts(
                        outcome["sdk_run_id"]).binding_record)
                    if (binding.provider_id != "corpus-real-provider"
                            or outcome["sdk_run_id"] == outcome["setup_phase"]["sdk_run_id"]
                            or (recent_provider if recent_provider is not None else retained_provider).attempts != 1):
                        raise ValueError("c07_scoring_phase_identity_differs" if recent_provider is not None
                            else "c08_scoring_phase_identity_differs")
                    outcome["provider_statistics_scope"] = "scoring_run_only_setup_excluded"
                outcome["execution_status"] = "OBSERVATION_FAILED" if outcome["observation_errors"] \
                    or (outcome.get("trace") or {}).get("terminal_status") != "TERMINAL" else "COMPLETED"
            except Exception as exc:
                from deskpet.quality.corpus_approval import CorpusApprovalBlocked
                if isinstance(exc, CorpusApprovalBlocked):
                    outcome["approval_status"] = "BLOCKED"
                outcome["execution_status"] = "OBSERVATION_FAILED" if outcome.get(
                    "actual_completed_group_available") else "EXECUTION_FAILED"
                outcome["error_type"] = type(exc).__name__
                # Enqueue was durable even if runtime failed before returning the
                # complete group. Re-read its exact single-case transport receipt.
                turns = [turn for turn in (await service.queue_snapshot())["turns"]
                    if turn["delivery_key"] == "scoring-turn-1"]
                if len(turns) == 1 and turns[0]["delivery_key"] == "scoring-turn-1":
                    outcome["queue_snapshot"] = turns
                    try:
                        # Do not repeat collection after a successful group: every
                        # observation has its own durable file and failure state.
                        if not outcome.get("actual_completed_group_available"):
                            outcome.update(await collect_turn(main, service, auth.subject, turns[0],
                                text, directory=directory))
                    except Exception as trace_error:
                        outcome["trace_error_type"] = type(trace_error).__name__
    except _InitializationComplete:
        pass
    except Exception as exc:
        outcome["error_type"] = type(exc).__name__
    finally:
        # Every constructed owner is closed even when preparation or activation
        # fails; outer resource carrier is the final process-group cleanup owner.
        runtime = main.service_context.get("human_memory_foreground_runtime_execution_authority")
        owners = (
            (runtime, "close", {}),
            (main.service_context.get("terminal_operation_audit"), "close", {}),
            (main._memory_analysis_lane, "close", {}),
            (main.service_context.get("companion_runtime"), "close", {"timeout": 5.0}),
            (main._sdk_runtime_stack, "close", {}),
            (main.service_context.get("human_memory_v7_runtime"), "close", {}),
            (main._session_db, "close", {}),
            # SessionDB is the sole owning closer of the product Memory manager.
            (main.service_context.get("capability_center"), "shutdown", {}),
            (main.service_context.get("capability_platform"), "shutdown", {}),
        )
        if main._sdk_ingress is not None:
            main._sdk_ingress.close()
        if recent_provider is not None:
            try:
                await recent_provider.close()
            except Exception as exc:
                outcome["cleanup_errors"].append("recent_provider:" + type(exc).__name__)
        if task_provider is not None:
            try:
                await task_provider.close()
            except Exception as exc:
                outcome["cleanup_errors"].append("task_provider:" + type(exc).__name__)
        if retained_provider is not None:
            try:
                await retained_provider.close()
            except Exception as exc:
                outcome["cleanup_errors"].append("retained_provider:" + type(exc).__name__)
        for owner, method, kwargs in owners:
            if owner is None:
                continue
            try:
                await getattr(owner, method)(**kwargs)
            except Exception as exc:
                outcome["cleanup_errors"].append(type(owner).__name__ + ":" + type(exc).__name__)
        workflow = main.service_context.get("workflow_service")
        if workflow is not None:
            try:
                await workflow.close()
            except Exception as exc:
                outcome["cleanup_errors"].append("workflow:" + type(exc).__name__)
        from deskpet.retrieval.runtime import shutdown_default_gateway
        try:
            await shutdown_default_gateway()
        except Exception as exc:
            outcome["cleanup_errors"].append("search_gateway:" + type(exc).__name__)
        outcome["result_hash"] = digest(outcome)
        write_result(directory / "execution.json", outcome)
    return 0 if outcome["execution_status"] in {"COMPLETED", "INITIALIZED_NOT_EXECUTED"} \
        and not outcome["cleanup_errors"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host-root", type=Path, required=True)
    parser.add_argument("--initialize-only", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    if ".local-test-evidence" not in directory.parts:
        raise ValueError("corpus_evidence_directory_required")
    try:
        key, base_url = configure_process(directory, args.host_root.resolve(strict=True),
            initialize_only=args.initialize_only)
        return asyncio.run(run(directory, args.host_root, key, base_url,
            initialize_only=args.initialize_only))
    except Exception as exc:
        if not (directory / "execution.json").exists():
            write_result(directory / "execution.json", dict(execution_status="SETUP_NOT_READY",
                trace=None, error_type=type(exc).__name__))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
