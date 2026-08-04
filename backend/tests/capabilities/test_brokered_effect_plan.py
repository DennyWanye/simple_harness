from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from deskpet.capabilities.brokered_planner import BrokeredEffectPlanner
from deskpet.capabilities.effect_plan import EffectPlan, EffectPlanValidationError
from deskpet.capabilities.input_views import (
    InputViewError,
    InputViewRequest,
    InputViewResolver,
)
from deskpet.capabilities.local_runtime import LocalToolRuntime
from deskpet.capabilities.tool_proxy import (
    BrokeredPlanEnvelope,
    LocalToolDefinition,
    LocalToolProxy,
)
from deskpet.workflows.effects import NormalizedToolOutcome

WORKER = Path(__file__).parent / "fixtures" / "json_tool_worker.py"


def _snapshot(root: Path, *files: Path):
    return InputViewResolver().resolve(
        [
            InputViewRequest(
                opaque_ref=f"opaque:{index}",
                resource=file,
                kind="file",
                view_kind="metadata",
            )
            for index, file in enumerate(files)
        ],
        root_run_id="root-plan-test",
        workspace_roots=[root],
    )


def _record(planner: BrokeredEffectPlanner, snapshot, actions):
    return planner.validate_and_record(
        {"actions": actions},
        snapshot=snapshot,
        root_run_id="root-plan-test",
        parent_call_id="parent-call",
        provider_call_id="provider-call",
        tool_spec_fingerprint="tool-fingerprint",
        task_grant_id="grant-1",
        catalog_stamp={"generation": 3},
    )


def test_input_worker_view_contains_no_host_path(tmp_path: Path) -> None:
    source = tmp_path / "照片 文件.txt"
    source.write_text("hello", encoding="utf-8")
    snapshot = _snapshot(tmp_path, source)
    payload = snapshot.worker_payload()
    assert str(tmp_path) not in repr(payload)
    assert payload["inputs"][0]["metadata"]["name"] == "照片 文件.txt"
    assert snapshot.bindings[0].content_hash == hashlib.sha256(b"hello").hexdigest()


def test_value_view_rejects_embedded_absolute_host_path(tmp_path: Path) -> None:
    with pytest.raises(InputViewError, match="absolute host paths"):
        InputViewResolver().resolve(
            [
                InputViewRequest(
                    opaque_ref="opaque:value",
                    resource={"path": str(tmp_path / "secret.txt")},
                    kind="value",
                    view_kind="metadata",
                )
            ],
            root_run_id="root-plan-test",
            workspace_roots=[tmp_path],
        )


def test_metadata_view_exposes_exif_capture_time_without_path(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "照片.jpg"
    exif = image_module.Exif()
    exif[36867] = "2026:07:23 12:34:56"
    image_module.new("RGB", (2, 3), color="white").save(image_path, exif=exif)
    snapshot = _snapshot(tmp_path, image_path)
    metadata = snapshot.worker_payload()["inputs"][0]["metadata"]
    assert metadata["width"] == 2
    assert metadata["height"] == 3
    assert metadata["exif"]["DateTimeOriginal"] == "2026:07:23 12:34:56"
    assert str(tmp_path) not in repr(metadata)


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "delete_file", "source_ref": "input:0", "target_name": "x"},
        {
            "kind": "rename_file",
            "source_ref": "input:0",
            "target_name": "C:\\outside.txt",
        },
        {
            "kind": "rename_file",
            "source_ref": "action:0",
            "target_name": "later.txt",
        },
    ],
)
def test_effect_plan_is_strict(action: dict[str, str]) -> None:
    with pytest.raises(EffectPlanValidationError):
        EffectPlan.parse({"actions": [action]})


def test_rename_cycle_requires_and_accepts_ordered_temp_step(tmp_path: Path) -> None:
    first = tmp_path / "甲.txt"
    second = tmp_path / "乙.txt"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    snapshot = _snapshot(tmp_path, first, second)
    planner = BrokeredEffectPlanner(resource_authorizer=lambda *_: True)
    record = _record(
        planner,
        snapshot,
        [
            {
                "kind": "rename_file",
                "source_ref": "input:0",
                "target_name": ".deskpet-swap.tmp",
            },
            {
                "kind": "rename_file",
                "source_ref": "input:1",
                "target_name": "甲.txt",
            },
            {
                "kind": "rename_file",
                "source_ref": "input:0",
                "target_name": "乙.txt",
            },
        ],
    )
    prepared = planner.next_action(record, snapshot=snapshot)
    assert prepared is not None
    assert prepared.tool_name == "move_file"
    assert prepared.stable_call_id == f"{record.plan_ref}:0"
    assert prepared.provider_backfill is False
    assert prepared.args["source"] == str(first)
    assert prepared.args["destination"] == str(tmp_path / ".deskpet-swap.tmp")


def test_direct_swap_is_rejected_as_collision(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    snapshot = _snapshot(tmp_path, first, second)
    with pytest.raises(EffectPlanValidationError, match="collides"):
        _record(
            BrokeredEffectPlanner(resource_authorizer=lambda *_: True),
            snapshot,
            [
                {
                    "kind": "rename_file",
                    "source_ref": "input:0",
                    "target_name": "b.txt",
                }
            ],
        )


def test_planner_fails_closed_without_task_grant_authorizer(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("A", encoding="utf-8")
    snapshot = _snapshot(tmp_path, source)
    with pytest.raises(EffectPlanValidationError, match="authorizer"):
        _record(
            BrokeredEffectPlanner(),
            snapshot,
            [
                {
                    "kind": "rename_file",
                    "source_ref": "input:0",
                    "target_name": "target.txt",
                }
            ],
        )


@pytest.mark.asyncio
async def test_proxy_computes_plan_without_changing_file(tmp_path: Path) -> None:
    source = tmp_path / "original 文件.txt"
    source.write_text("unchanged", encoding="utf-8")
    proxy = LocalToolProxy(
        runtime=LocalToolRuntime(),
        input_resolver=InputViewResolver(),
        brokered_planner=BrokeredEffectPlanner(resource_authorizer=lambda *_: True),
    )
    result = await proxy.invoke(
        LocalToolDefinition(
            name="fixture.rename",
            argv=(sys.executable, str(WORKER), "brokered"),
            execution_profile="brokered-effect-v1",
            tool_spec_fingerprint="fixture-fingerprint",
            generated=True,
        ),
        {"path": str(source), "target": str(tmp_path / "should-not-pass.txt")},
        input_requests=[
            InputViewRequest("opaque:0", source, "file", "metadata")
        ],
        workspace_roots=[str(tmp_path)],
        temp_dir=str(tmp_path),
        root_run_id="root-plan-test",
        run_id="run-plan-test",
        effect_id="effect-plan-test",
        parent_call_id="parent-call",
        provider_call_id="provider-call",
        task_grant_id="grant-1",
        catalog_stamp={"generation": 3},
    )
    assert isinstance(result, BrokeredPlanEnvelope)
    assert source.read_text(encoding="utf-8") == "unchanged"
    assert not (tmp_path / "renamed 文件.txt").exists()


def test_generated_native_adapter_is_rejected() -> None:
    with pytest.raises(ValueError, match="brokered-effect-v1"):
        LocalToolDefinition(
            name="unsafe",
            argv=(sys.executable, "worker.py"),
            execution_profile="native-adapter",
            tool_spec_fingerprint="x",
            generated=True,
        )


def test_brokered_plan_durable_envelope_roundtrips_host_bindings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("A", encoding="utf-8")
    snapshot = _snapshot(tmp_path, source)
    planner = BrokeredEffectPlanner(resource_authorizer=lambda *_: True)
    record = _record(
        planner,
        snapshot,
        [
            {
                "kind": "rename_file",
                "source_ref": "input:0",
                "target_name": "target.txt",
            }
        ],
    )
    envelope = BrokeredPlanEnvelope(
        value={"summary": "rename"},
        artifacts=(),
        observations=({"count": 1},),
        input_snapshot=snapshot,
        plan_record=record,
    )

    restored = BrokeredPlanEnvelope.from_durable_value(
        envelope.to_durable_envelope()
    )

    assert restored == envelope
    assert restored is not None
    assert restored.input_snapshot.bindings[0].canonical_resource == str(source)
    assert str(source) not in repr(envelope.to_signal_envelope())
