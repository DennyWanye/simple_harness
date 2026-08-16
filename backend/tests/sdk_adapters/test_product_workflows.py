# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import importlib
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from simple_harness.workflow import WorkflowContext

from deskpet.sdk_adapters.conformance import _WorkflowSeam, _research_context

from deskpet.sdk_adapters.workflows import (
    ACTIVE_PRODUCT_WORKFLOWS,
    build_product_workflow_registrations,
)


FORBIDDEN = (
    "deskpet.workflows.contracts",
    "deskpet.workflows.control",
    "deskpet.workflows.definition",
    "deskpet.workflows.runner",
    "deskpet.workflows.definitions.v1",
    "deskpet.workflows.definitions.v7",
    "deskpet.workflows.definitions.research_core",
    "deskpet.workflows.definitions.deep_research_v5_contracts",
)


def test_active_product_workflow_identities_are_versioned_and_detached():
    assert ACTIVE_PRODUCT_WORKFLOWS == {
        "workflow.deep_research": ("deep_research", "v7-sdk1"),
        "workflow.presentation": ("ppt_pro", "v2"),
    }
    before = set(sys.modules)
    importlib.import_module("deskpet.workflows.definitions.sdk_v7.deep_research")
    importlib.import_module("deskpet.workflows.definitions.sdk_v2.ppt_pro")
    loaded = set(sys.modules) - before
    assert not any(name == prefix or name.startswith(prefix + ".") for name in loaded for prefix in FORBIDDEN)


def test_fresh_process_standard_import_does_not_load_legacy_workflow_authority():
    script = textwrap.dedent(
        f"""
        import importlib
        import json
        import sys

        forbidden = {FORBIDDEN!r}
        importlib.import_module('deskpet.workflows.definitions.sdk_v7.deep_research')
        importlib.import_module('deskpet.workflows.definitions.sdk_v2.ppt_pro')
        blocked = sorted(
            name for name in sys.modules
            if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)
        )
        print(json.dumps(blocked))
        raise SystemExit(1 if blocked else 0)
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[2])
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[3],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == []


def test_lazy_legacy_exports_preserve_first_access_identity():
    import deskpet.workflows as workflows
    import deskpet.workflows.definitions as definitions

    contracts = importlib.import_module("deskpet.workflows.contracts")
    legacy_definition = importlib.import_module("deskpet.workflows.definition")
    research_core = importlib.import_module("deskpet.workflows.definitions.research_core")
    v5_contracts = importlib.import_module(
        "deskpet.workflows.definitions.deep_research_v5_contracts"
    )
    assert workflows.EffectKind is contracts.EffectKind
    assert workflows.WorkflowDefinition is legacy_definition.WorkflowDefinition
    assert definitions.ResearchCoreConfig is research_core.ResearchCoreConfig
    assert definitions.ResearchBrief is v5_contracts.ResearchBrief


def test_product_registrations_use_exact_active_identities():
    owner = object()
    registrations = build_product_workflow_registrations(
        generation=1, transaction_owner=owner
    )
    assert {
        item.profile.descriptor.key: (
            item.definition.name,
            item.definition.version,
            item.transaction_owner,
        )
        for item in registrations
    } == {
        "workflow.deep_research": ("deep_research", "v7-sdk1", owner),
        "workflow.presentation": ("ppt_pro", "v2", owner),
    }


def test_ppt_v2_has_pure_barrier_and_separate_effect_node():
    module = importlib.import_module("deskpet.workflows.definitions.sdk_v2.ppt_pro")
    definition = module.PPT_PRO_V2_DEFINITION
    nodes = {node.node_id: node for node in definition.nodes}
    assert nodes["wait_outline_decision"].interrupt_capable is True
    assert nodes["wait_outline_decision"].pre_interrupt_effect_policy == "pure"
    assert nodes["apply_outline_decision"].interrupt_capable is False
    assert len(definition.conditional_edges) == 7
    assert {
        edge.selector_effect_policy for edge in definition.conditional_edges
    } == {"pure"}


@pytest.mark.asyncio
async def test_deep_research_runs_real_ports_delivers_openable_artifact_and_reopens(
    tmp_path: Path,
):
    database_path = tmp_path / "deep-research.sqlite"
    seam = _WorkflowSeam(database_path)
    registration = next(
        item
        for item in seam.product
        if item.profile.descriptor.key == "workflow.deep_research"
    )
    context, ports = _research_context(tmp_path / "research-physical")
    run_id, result, run = await seam.execute(
        registration,
        {
            "run_id": "run-deep-research",
            "values": {"topic": "SDK migration evidence", "mode": "deep"},
        },
        context,
    )
    assert run.state.value == "completed"
    values = result.output["values"]
    artifact = Path(values["report_artifact"]["path"])
    assert artifact.is_file()
    report_bytes = artifact.read_bytes()
    assert values["report_sha256"] == hashlib.sha256(report_bytes).hexdigest()
    assert ports["blob"].get is not None
    blob_bytes = await ports["blob"].get(values["report_blob"])
    assert blob_bytes == report_bytes
    report = report_bytes.decode("utf-8")
    assert "[^1]" in report and "## 引用" in report
    assert values["report_payload"]["citations"]
    assert all(item["status"] == "valid" for item in values["child_records"])
    assert {item["kind"] for item in values["delivery_intents"]} == {
        "artifact_card",
        "final_assistant",
    }
    physical_before = sum(
        len(port.calls) for port in ports.values() if hasattr(port, "calls")
    )
    seam.close()

    reopened = _WorkflowSeam(database_path)
    recovered = reopened.uow.read_run(run_id)
    assert recovered is not None
    assert recovered.state.value == "completed"
    assert sum(
        len(port.calls) for port in ports.values() if hasattr(port, "calls")
    ) == physical_before
    reopened.close()


class _ContinuationLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, prompt, *, operation_key):
        self.calls.append((prompt, operation_key))
        if "planning a deep research" in prompt:
            return '["What is verified?", "What are the risks?"]'
        if "returned insufficient evidence" in prompt:
            return "official primary evidence for the same question"
        return "# Continued report\n\n## TL;DR\n\nVerified with evidence.[^1]"


class _ContinuationSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def search(self, query, *, operation_key):
        self.calls.append((query, operation_key))
        if len(self.calls) == 1:
            return []
        return [{"url": f"https://example.invalid/source-{len(self.calls)}", "title": query}]


@pytest.mark.asyncio
async def test_deep_research_continues_same_child_and_preserves_citation_authority(
    tmp_path: Path,
):
    seam = _WorkflowSeam(tmp_path / "deep-research-continuation.sqlite")
    registration = next(
        item
        for item in seam.product
        if item.profile.descriptor.key == "workflow.deep_research"
    )
    base_context, ports = _research_context(tmp_path / "continued-physical")
    del base_context
    llm, search = _ContinuationLLM(), _ContinuationSearch()
    ports["llm"] = llm
    ports["search"] = search
    context = WorkflowContext(ports=ports)
    _, result, run = await seam.execute(
        registration,
        {
            "run_id": "run-deep-research-continuation",
            "values": {"topic": "Continuation evidence", "mode": "deep"},
        },
        context,
    )
    assert run.state.value == "completed"
    values = result.output["values"]
    first = values["child_records"][0]
    assert first["attempt"] == 2
    assert first["status"] == "valid"
    assert "same question" in first["continuation"]
    assert first["attempts"][0]["reason_code"] == "no_citations"
    assert first["attempts"][1]["reason_code"] == "ok"
    assert len({key for _, key in search.calls}) == len(search.calls)
    assert values["report_payload"]["coverage"]["n_sources"] >= 2
    assert Path(values["report_artifact"]["path"]).is_file()
    seam.close()


class _PptLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def plan_research(self, *, topic, operation_key):
        self.calls.append(operation_key)
        return [f"Evidence for {topic}"]

    async def synthesize_research(self, *, topic, sources, operation_key):
        self.calls.append(operation_key)
        return {"summary": f"Verified {topic}", "source_count": len(sources)}

    async def draft_outline(self, *, topic, pages, research, operation_key):
        self.calls.append(operation_key)
        assert research
        return [
            {
                "title": f"{topic} {index}",
                "bullets": ["Evidence"],
                "layout": "title_body",
            }
            for index in range(1, pages + 1)
        ]


class _PptResearch:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(self, *, query, operation_key):
        self.calls.append(operation_key)
        return [{"url": "https://example.invalid/evidence", "title": query}]

    async def fetch(self, *, url, operation_key):
        self.calls.append(operation_key)
        return {"url": url, "title": "Evidence", "content": "Verified evidence."}


class _PptWorkspace:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def apply_outline_decision(self, *, outline_id, decision, operation_key):
        self.calls.append(operation_key)
        return {"outline_id": outline_id, "action": decision["action"]}


class _PptArtifact:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []
        self.image_calls: list[str] = []
        self.last_pages = 0

    async def probe_images(self, *, operation_key):
        self.calls.append(operation_key)
        return True

    async def generate_slide_image(self, *, slide_id, slide, operation_key):
        self.calls.append(operation_key)
        self.image_calls.append(slide_id)
        return {"image_ref": f"image:{slide_id}", "sha256": "a" * 64}

    async def render_presentation(self, **values):
        self.calls.append(values["operation_key"])
        self.last_pages = len(values["slides"])
        target = Path(values["output_path"]) if values.get("output_path") else self.root / "deck.pptx"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(b"PK\x03\x04deskpet-ppt-v2")
        return {
            "artifact_ref": "artifact:ppt-v2",
            "path": str(target),
            "editable": bool(values["editable_required"]),
            "full_page_images": bool(values["full_page_images"]),
        }

    async def render_preview(self, *, artifact, operation_key):
        self.calls.append(operation_key)
        return [
            {"page_number": index, "artifact_ref": artifact["artifact_ref"]}
            for index in range(1, self.last_pages + 1)
        ]


class _PptEvaluator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate_visuals(self, *, previews, operation_key):
        self.calls.append(operation_key)
        return {"approved": bool(previews), "issues": []}

    async def revise_visuals(self, *, slides, review, operation_key):
        self.calls.append(operation_key)
        return slides


class _PptNotifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def outline_ready(self, *, outline, operation_key):
        self.calls.append(operation_key)
        return {"notified": True, "outline_id": outline["outline_id"]}

    async def publish(self, *, artifact, operation_key):
        self.calls.append(operation_key)
        return {"published": True, "artifact_ref": artifact["artifact_ref"]}


@pytest.mark.asyncio
async def test_ppt_public_resolve_and_resume_is_durable_and_effects_are_exact_once(
    tmp_path: Path,
):
    database_path = tmp_path / "ppt.sqlite"
    llm, research, workspace = _PptLLM(), _PptResearch(), _PptWorkspace()
    artifact, notifier = _PptArtifact(tmp_path / "ppt-artifacts"), _PptNotifier()
    evaluator = _PptEvaluator()
    context = WorkflowContext(
        ports={
            "llm": llm,
                "search": research,
                "fetch": research,
            "workspace": workspace,
            "artifact": artifact,
            "evaluator": evaluator,
            "notifier": notifier,
        }
    )
    seam = _WorkflowSeam(database_path)
    registration = next(
        item
        for item in seam.product
        if item.profile.descriptor.key == "workflow.presentation"
    )
    run_id, waiting, run = await seam.execute(
        registration,
        {
            "run_id": "run-ppt-v2",
            "values": {
                "topic": "SDK migration deck",
                "pages": 3,
                "editable_required": True,
                "full_page_images": False,
            },
        },
        context,
    )
    assert run.state.value == "waiting"
    assert waiting.output["interrupt"]["payload"]["kind"] == "ppt_outline"
    assert workspace.calls == artifact.calls == evaluator.calls == []
    assert len(notifier.calls) == 1
    decision_id = seam.database.connection.execute(
        "SELECT decision_id FROM decisions WHERE run_id=? AND state='open'", (run_id,)
    ).fetchone()[0]
    durable = seam.uow.read_decision(str(decision_id))
    seam.close()

    reopened = _WorkflowSeam(database_path)
    completed = await reopened.runner.resolve_and_resume(
        run_id,
        decision_id=str(decision_id),
        nonce=str(decision_id),
        expected_version=durable.version,
        response={"action": "approve"},
        context=context,
    )
    assert completed.status.value == "completed"
    values = completed.output["values"]
    assert values["business_status"] == "completed"
    assert Path(values["presentation_artifact"]["path"]).is_file()
    assert len(workspace.calls) == 1
    assert len(artifact.calls) == 3
    assert len(evaluator.calls) == 1
    assert len(notifier.calls) == 2
    physical_before = (
        len(llm.calls), len(research.calls), len(workspace.calls),
        len(artifact.calls), len(evaluator.calls), len(notifier.calls),
    )
    reopened.close()

    recovered = _WorkflowSeam(database_path)
    durable_run = recovered.uow.read_run(run_id)
    assert durable_run is not None
    assert durable_run.state.value == "completed"
    assert (
        len(llm.calls), len(research.calls), len(workspace.calls),
        len(artifact.calls), len(evaluator.calls), len(notifier.calls),
    ) == physical_before
    recovered.close()


@pytest.mark.asyncio
async def test_ppt_full_page_output_path_and_generic_approval_survive_reopen(
    tmp_path: Path,
):
    database_path = tmp_path / "ppt-full-page.sqlite"
    llm, research, workspace = _PptLLM(), _PptResearch(), _PptWorkspace()
    artifact = _PptArtifact(tmp_path / "ppt-full-page-artifacts")
    evaluator, notifier = _PptEvaluator(), _PptNotifier()
    context = WorkflowContext(
        ports={
            "llm": llm,
            "search": research,
            "fetch": research,
            "workspace": workspace,
            "artifact": artifact,
            "evaluator": evaluator,
            "notifier": notifier,
        }
    )
    output_path = tmp_path / "requested" / "full-page.pptx"
    seam = _WorkflowSeam(database_path)
    registration = next(
        item
        for item in seam.product
        if item.profile.descriptor.key == "workflow.presentation"
    )
    run_id, waiting, run = await seam.execute(
        registration,
        {
            "run_id": "run-ppt-v2-full-page",
            "values": {
                "topic": "Full-page migration deck",
                "pages": 2,
                "editable_required": False,
                "full_page_images": True,
                "output_path": str(output_path),
            },
        },
        context,
    )
    assert run.state.value == "waiting"
    decision_id = seam.database.connection.execute(
        "SELECT decision_id FROM decisions WHERE run_id=? AND state='open'", (run_id,)
    ).fetchone()[0]
    durable = seam.uow.read_decision(str(decision_id))
    seam.close()

    reopened = _WorkflowSeam(database_path)
    completed = await reopened.runner.resolve_and_resume(
        run_id,
        decision_id=str(decision_id),
        nonce=str(decision_id),
        expected_version=durable.version,
        response={"approved": True},
        context=context,
    )
    values = completed.output["values"]
    assert completed.status.value == "completed"
    assert values["outline_action"] == "accept"
    assert values["presentation_artifact"]["path"] == str(output_path)
    assert values["presentation_artifact"]["full_page_images"] is True
    assert values["presentation_artifact"]["editable"] is False
    assert output_path.is_file()
    assert all(item["image_status"] == "complete" for item in values["slide_records"])
    assert len(artifact.image_calls) == 2
    physical_before = tuple(artifact.calls)
    reopened.close()

    recovered = _WorkflowSeam(database_path)
    assert recovered.uow.read_run(run_id).state.value == "completed"
    assert tuple(artifact.calls) == physical_before
    recovered.close()
