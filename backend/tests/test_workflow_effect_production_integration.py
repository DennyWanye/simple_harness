from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from deskpet.workflows import EffectKind, EffectPolicy
from deskpet.workflows.adapters.code_runtime import ToolDispatchPort, derive_effect_id
from deskpet.workflows.adapters.ppt_runtime import PptRuntime
from deskpet.workflows.effects import (
    EffectExecutionContext,
    EffectJournal,
    EffectStateConflict,
    NormalizedToolOutcome,
    PreparedTarget,
    PreparedToolCall,
    TargetMode,
)
from deskpet.workflows.store import WorkflowRunStore


async def _effect_context(
    path: Path,
    *,
    workflow_name: str,
    node_id: str,
    key: str,
) -> tuple[EffectExecutionContext, str]:
    store = WorkflowRunStore(path)
    await store.initialize()
    run_id, _ = await store.create_run(
        request_key=key,
        session_id="session-1",
        request_id=f"request-{key}",
        turn_id=f"turn-{key}",
        workflow_name=workflow_name,
        workflow_version="v1",
        manifest_hash="manifest-v1",
        implementation_hash="implementation-v1",
        capability_hash=f"capability-{key}",
        capability_snapshot={},
        state_schema_version=1,
    )
    fence = await store.claim(run_id, f"owner-{key}")
    return (
        EffectExecutionContext(
            journal=EffectJournal(path),
            fence=fence,
            node_execution_id=f"node-execution-{key}",
            workflow_name=workflow_name,
            workflow_version="v1",
            node_id=node_id,
        ),
        run_id,
    )


class _JournaledRegistry:
    def __init__(
        self,
        policy: EffectPolicy,
        *,
        fail: bool = False,
        execution_metadata: dict[str, object] | None = None,
    ) -> None:
        self.spec = SimpleNamespace(effect_policy=policy)
        self.fail = fail
        self.calls: list[str] = []
        self.execution_metadata = dict(execution_metadata or {})
        self.acknowledged: list[str] = []

    def get(self, _name: str) -> object:
        return self.spec

    async def execute_prepared(
        self, prepared: PreparedToolCall, *, effect_id: str, authorization=None
    ) -> NormalizedToolOutcome:
        del authorization
        self.calls.append(effect_id)
        if self.fail:
            raise RuntimeError("injected handler crash")
        for target in prepared.prepared_targets:
            Path(target.final_path).write_text(
                str(prepared.final_params["content"]), encoding="utf-8"
            )
        return NormalizedToolOutcome.success(
            {"path": prepared.final_params.get("path"), "effect_id": effect_id}
        )

    def take_prepared_execution_metadata(self, _effect_id: str) -> dict[str, object]:
        return dict(self.execution_metadata)

    def acknowledge_prepared_effect(self, effect_id: str) -> None:
        self.acknowledged.append(effect_id)


@pytest.mark.asyncio
async def test_code_execute_prepared_reuses_journaled_committed_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    user_data_dir = tmp_path / "user-data"
    db_path = user_data_dir / "data" / "workflow.db"
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data_dir))
    context, run_id = await _effect_context(
        db_path,
        workflow_name="code_complex",
        node_id="tool_execution",
        key="code-commit",
    )
    target_path = tmp_path / "result.txt"
    target = PreparedTarget.prepare(
        target_path,
        run_id=run_id,
        stable_call_id="call-1",
        mode=TargetMode.CREATE,
    )
    prepared = PreparedToolCall.prepare(
        tool_name="write_file",
        stable_call_id="call-1",
        final_params={"path": str(target_path), "content": "done"},
        prepared_targets=(target,),
        tool_spec_version="v1",
        schema_hash="schema-write-v1",
        permission_policy_version="permission-v1",
        effect_type="staged_file",
    )
    registry = _JournaledRegistry(
        EffectPolicy("deskpet:write-file", "v1", EffectKind.STAGED_FILE)
    )
    port = ToolDispatchPort(
        registry,
        session_id="session-1",
    )

    first = await port.dispatch(
        [prepared],
        workflow_step_id="step-1",
        prior_results={},
        authorizations={},
    )
    recovered = await port.dispatch(
        [prepared],
        workflow_step_id="step-1",
        prior_results={},
        authorizations={},
    )

    effect_id = str(first["call-1"]["effect_id"])
    assert target_path.read_text(encoding="utf-8") == "done"
    assert recovered == first
    assert registry.calls == [effect_id]
    assert effect_id != derive_effect_id("step-1", prepared)
    record = await context.journal.get(effect_id)
    assert record is not None and record.status.value == "committed"
    json.dumps(first, allow_nan=False)


@pytest.mark.asyncio
async def test_code_effect_commits_registry_artifact_identity_atomically(
    tmp_path: Path,
) -> None:
    context, run_id = await _effect_context(
        tmp_path / "workflow.db",
        workflow_name="durable_task",
        node_id="tool_execution",
        key="artifact-identity",
    )
    digest = "a" * 64
    prepared = PreparedToolCall.prepare(
        tool_name="register_artifacts",
        stable_call_id="call-artifacts",
        final_params={"paths": ["REPORT.md"]},
        tool_spec_version="v1",
        schema_hash="schema-register-artifacts-v1",
        permission_policy_version="permission-v1",
        effect_type="idempotent_read",
    )
    registry = _JournaledRegistry(
        EffectPolicy(
            "deskpet:register-artifacts",
            "v1",
            EffectKind.IDEMPOTENT_READ,
        ),
        execution_metadata={
            "receipt_ref": "receipt-artifacts",
            "artifact_refs": [digest],
        },
    )
    registry.execute_prepared = lambda *args, **kwargs: _async_value(
        NormalizedToolOutcome.success(
            {
                "ok": True,
                "artifacts": [
                    {
                        "kind": "file",
                        "path": str(tmp_path / "REPORT.md"),
                        "title": "REPORT.md",
                        "sha256": digest,
                    }
                ],
            }
        )
    )
    port = ToolDispatchPort(
        registry,
        session_id="session-1",
        effect_context=context,
        workflow_name="durable_task",
    )

    result = await port.dispatch(
        [prepared],
        workflow_step_id="step-deliver",
        prior_results={},
        authorizations={},
    )

    effect_id = str(result["call-artifacts"]["effect_id"])
    record = await context.journal.get(effect_id)
    assert record is not None
    assert record.run_id == run_id
    assert record.receipt_ref == "receipt-artifacts"
    assert record.artifact_refs == (digest,)
    assert registry.acknowledged == [effect_id]


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_code_uncertain_effect_blocks_reexecution(tmp_path: Path) -> None:
    context, _ = await _effect_context(
        tmp_path / "workflow.db",
        workflow_name="code_complex",
        node_id="tool_execution",
        key="code-uncertain",
    )
    prepared = PreparedToolCall.prepare(
        tool_name="desktop_write",
        stable_call_id="call-dangerous",
        final_params={"action": "click", "x": 10, "y": 20},
        tool_spec_version="v1",
        schema_hash="schema-desktop-v1",
        permission_policy_version="permission-v1",
        effect_type="opaque_manual",
    )
    registry = _JournaledRegistry(
        EffectPolicy("deskpet:desktop-write", "v1", EffectKind.OPAQUE_MANUAL),
        fail=True,
    )
    port = ToolDispatchPort(
        registry,
        session_id="session-1",
        effect_context=context,
    )

    with pytest.raises(RuntimeError, match="injected handler crash"):
        await port.dispatch(
            [prepared],
            workflow_step_id="step-dangerous",
            prior_results={},
            authorizations={},
        )
    with pytest.raises(EffectStateConflict, match="execution is blocked"):
        await port.dispatch(
            [prepared],
            workflow_step_id="step-dangerous",
            prior_results={},
            authorizations={},
        )

    assert len(registry.calls) == 1
    record = await context.journal.get(registry.calls[0])
    assert record is not None and record.status.value == "uncertain"


@pytest.mark.asyncio
async def test_production_run_ambiguity_fails_closed_before_code_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    user_data_dir = tmp_path / "user-data"
    db_path = user_data_dir / "data" / "workflow.db"
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data_dir))
    await _effect_context(
        db_path,
        workflow_name="code_complex",
        node_id="tool_execution",
        key="ambiguous-a",
    )
    await _effect_context(
        db_path,
        workflow_name="code_complex",
        node_id="tool_execution",
        key="ambiguous-b",
    )
    prepared = PreparedToolCall.prepare(
        tool_name="desktop_write",
        stable_call_id="call-ambiguous",
        final_params={"action": "click", "x": 10, "y": 20},
        tool_spec_version="v1",
        schema_hash="schema-desktop-v1",
        permission_policy_version="permission-v1",
        effect_type="opaque_manual",
    )
    registry = _JournaledRegistry(
        EffectPolicy("deskpet:desktop-write", "v1", EffectKind.OPAQUE_MANUAL)
    )
    port = ToolDispatchPort(registry, session_id="session-1")

    with pytest.raises(EffectStateConflict, match="found 2"):
        await port.dispatch(
            [prepared],
            workflow_step_id="step-ambiguous",
            prior_results={},
            authorizations={},
        )

    assert registry.calls == []


@pytest.mark.asyncio
async def test_ppt_effects_reuse_committed_json_safe_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from deskpet.tools import ppt_tools

    user_data_dir = tmp_path / "user-data"
    db_path = user_data_dir / "data" / "workflow.db"
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data_dir))
    image_context, _ = await _effect_context(
        db_path,
        workflow_name="ppt_pro",
        node_id="image_map",
        key="ppt-effects",
    )
    image = tmp_path / "slide.png"
    deck = tmp_path / "deck.pptx"
    preview = tmp_path / "preview.png"
    counts = {"image": 0, "deck_publish": 0, "preview_render": 0}

    def generate(_prompts, *, size):
        assert size == "1024x1024"
        counts["image"] += 1
        image.write_bytes(b"image")
        return [{"path": image, "error": None}]

    def publish(_slides, **_kwargs):
        counts["deck_publish"] += 1
        deck.write_bytes(b"deck")
        return {"ok": True, "path": deck, "artifacts": []}

    class Renderer:
        @staticmethod
        def com_render_available() -> bool:
            return True

        @staticmethod
        def render_pptx_to_pngs_safe(_path, _out_dir, *, timeout):
            assert timeout == 150.0
            counts["preview_render"] += 1
            preview.write_bytes(b"preview")
            return [preview]

    monkeypatch.setattr(ppt_tools, "generate_images", generate)
    monkeypatch.setattr(ppt_tools, "_render_pro", publish)
    monkeypatch.setattr(ppt_tools, "_ppt_preview_render_enabled", lambda: True)
    monkeypatch.setattr(ppt_tools, "_get_ppt_renderer", lambda: Renderer())
    runtime = PptRuntime()

    async def run_all():
        generated = await runtime.generate_slide_image(
            slide_id="slide-1",
            prompt="prompt",
            size="1024x1024",
            input_hash="image-input",
        )
        published = await runtime.render_ppt(
            slides=[{"title": "Slide", "layout": "bullet"}],
            topic="Topic",
            title="Deck",
            author="DeskPet",
            theme="minimal",
            output_path=str(deck),
            render_mode="template",
            render_revision=0,
            input_hash="deck-input",
        )
        previews = await runtime.render_preview(
            path=str(deck), render_hash=str(published["output_hash"])
        )
        return generated, published, previews

    first = await run_all()
    recovered = await run_all()

    assert recovered == first
    assert counts == {"image": 1, "deck_publish": 1, "preview_render": 1}
    json.dumps(first, allow_nan=False)
    async with aiosqlite.connect(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT status,COUNT(*) FROM workflow_effects GROUP BY status"
            )
        ).fetchall()
    assert rows == [("committed", 3)]


@pytest.mark.asyncio
async def test_ppt_uncertain_image_generation_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from deskpet.tools import ppt_tools

    context, _ = await _effect_context(
        tmp_path / "workflow.db",
        workflow_name="ppt_pro",
        node_id="image_map",
        key="ppt-uncertain",
    )
    calls = 0

    def fail_generate(_prompts, *, size):
        nonlocal calls
        assert size == "1024x1024"
        calls += 1
        raise RuntimeError("image provider disconnected")

    monkeypatch.setattr(ppt_tools, "generate_images", fail_generate)
    runtime = PptRuntime(effect_context={"image": context})
    kwargs = {
        "slide_id": "slide-dangerous",
        "prompt": "prompt",
        "size": "1024x1024",
        "input_hash": "image-dangerous-input",
    }

    with pytest.raises(RuntimeError, match="image provider disconnected"):
        await runtime.generate_slide_image(**kwargs)
    with pytest.raises(EffectStateConflict, match="execution is blocked"):
        await runtime.generate_slide_image(**kwargs)

    assert calls == 1


@pytest.mark.asyncio
async def test_full_page_image_effect_reuses_normalized_asset_and_detects_retention_loss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from PIL import Image

    from deskpet.tools import ppt_tools

    context, _ = await _effect_context(
        tmp_path / "workflow.db",
        workflow_name="ppt_pro",
        node_id="image_map",
        key="ppt-full-page-effect",
    )
    provider_path = tmp_path / "provider.png"
    Image.new("RGB", (1792, 1024), (20, 40, 80)).save(provider_path)
    calls = 0

    def generate(_prompts, *, size, model):
        nonlocal calls
        calls += 1
        assert size == "1792x1024"
        assert model == "model-a"
        return [{"path": str(provider_path), "error": None}]

    monkeypatch.setattr(ppt_tools, "generate_images", generate)
    runtime = PptRuntime(effect_context={"image": context})
    kwargs = {
        "slide_id": "slide-full-page",
        "prompt": "complete slide",
        "size": "1792x1024",
        "input_hash": "full-page-input",
        "model": "model-a",
        "prompt_schema_version": "prompt-v1",
        "normalizer_version": "normalizer-v1",
        "page_revision": 0,
        "full_page": True,
    }

    first = await runtime.generate_slide_image(**kwargs)
    recovered = await runtime.generate_slide_image(**kwargs)
    normalized_path = Path(str(first["path"]))

    assert recovered == first
    assert calls == 1
    assert normalized_path.is_file()
    with Image.open(normalized_path) as image:
        assert image.size == (1792, 1008)

    normalized_path.unlink()
    with pytest.raises(EffectStateConflict, match="missing from disk"):
        await runtime.generate_slide_image(**kwargs)
    assert calls == 1
