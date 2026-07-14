from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.workflows.adapters.ppt_runtime import PptRuntime


@pytest.mark.asyncio
async def test_runtime_operations_use_executor_and_return_json_safe_refs(
    monkeypatch, tmp_path: Path
) -> None:
    # The test exercises executor/JSON normalization without a durable graph
    # run. Evidence automation sets this globally, which would otherwise make
    # PptRuntime enter the production EffectJournal path and fail closed.
    monkeypatch.delenv("DESKPET_USER_DATA_DIR", raising=False)
    from deskpet.tools import ppt_tools

    loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    image = tmp_path / "slide.png"
    deck = tmp_path / "deck.pptx"
    preview = tmp_path / "slide1.png"
    image.write_bytes(b"image")
    preview.write_bytes(b"preview")

    def probe(*, timeout_s):
        worker_threads.append(threading.get_ident())
        assert timeout_s == 2.0
        return True

    def generate(prompts, *, size):
        worker_threads.append(threading.get_ident())
        assert prompts == ["prompt"] and size == "1024x1024"
        return [{"path": image, "error": None}]

    def render(slides, **kwargs):
        worker_threads.append(threading.get_ident())
        deck.write_bytes(b"deck")
        return {"ok": True, "path": deck, "artifacts": []}

    class Renderer:
        @staticmethod
        def com_render_available():
            worker_threads.append(threading.get_ident())
            return True

        @staticmethod
        def render_pptx_to_pngs_safe(path, out_dir, *, timeout):
            assert path == str(deck.resolve())
            assert timeout == 150.0
            return [str(preview)]

    monkeypatch.setattr(ppt_tools, "probe_image_reachable", probe)
    monkeypatch.setattr(ppt_tools, "generate_images", generate)
    monkeypatch.setattr(ppt_tools, "_render_pro", render)
    monkeypatch.setattr(ppt_tools, "_ppt_preview_render_enabled", lambda: True)
    monkeypatch.setattr(ppt_tools, "_get_ppt_renderer", lambda: Renderer())

    runtime = PptRuntime()
    assert await runtime.probe_images(timeout_s=2.0)
    generated = await runtime.generate_slide_image(
        slide_id="slide-1", prompt="prompt", size="1024x1024", input_hash="input"
    )
    rendered = await runtime.render_ppt(
        slides=[{"title": "Slide", "layout": "bullet"}],
        topic="Topic",
        title="Deck",
        author="DeskPet",
        theme="minimal",
        output_path=str(deck),
        render_mode="template",
        render_revision=1,
        input_hash="render-input",
    )
    previews = await runtime.render_preview(
        path=str(deck), render_hash=str(rendered["output_hash"])
    )

    assert generated["path"] == str(image)
    assert len(str(generated["output_hash"])) == 64
    assert rendered["path"] == str(deck)
    assert previews[0]["artifact_ref"] == f"preview:{previews[0]['sha256']}"
    assert all(thread_id != loop_thread for thread_id in worker_threads)
    json.dumps([generated, rendered, previews], allow_nan=False)


@pytest.mark.asyncio
async def test_runtime_visual_evaluator_filters_issues_and_maps_slide_ids(
    monkeypatch, tmp_path: Path
) -> None:
    from deskpet.tools import ppt_visual_review

    preview = tmp_path / "slide1.png"
    preview.write_bytes(b"preview")
    seen = {}

    def review(paths, metadata, *, mode):
        seen.update(paths=paths, metadata=metadata, mode=mode, thread=threading.get_ident())
        return [
            {"page": 1, "ok": False, "issues": ["overflow"], "action": "shrink_text"},
            {"page": 2, "ok": True, "issues": [], "action": "ok"},
        ]

    monkeypatch.setattr(ppt_visual_review, "review_slides", review)
    loop_thread = threading.get_ident()
    result = await PptRuntime().evaluate_ppt(
        previews=[{"path": str(preview), "sha256": "preview-hash"}],
        slides=[
            {
                "slide_id": "slide-1",
                "slide": {"title": "One", "layout": "image_full", "bullets": ["a"]},
                "image": {"status": "ready"},
            },
            {
                "slide_id": "slide-2",
                "slide": {"title": "Two", "layout": "image_full", "bullets": []},
                "image": {"status": "ready"},
            },
        ],
    )

    assert seen["mode"] == "image"
    assert seen["thread"] != loop_thread
    assert result["issues"] == [
        {
            "page": 1,
            "ok": False,
            "issues": ["overflow"],
            "action": "shrink_text",
            "slide_id": "slide-1",
        }
    ]
    assert result["score"] == 0.5
    json.dumps(result, allow_nan=False)


@pytest.mark.asyncio
async def test_startup_wires_runtime_to_effect_and_evaluator_ports(monkeypatch) -> None:
    import main
    from deskpet.tools import research_tools

    captured = {}

    class Launcher:
        def register_adapter(self, *args, **kwargs):
            captured["registered_adapter"] = (args, kwargs)

        async def recover_pending(self):
            return []

        async def launch(self, **kwargs):
            captured.update(kwargs)
            context = await kwargs["context_factory"](
                {"request_id": "request", "turn_id": "turn"},
                kwargs["start_payload"],
            )
            captured["context"] = context
            return {"run_id": "ppt-run"}

    launcher = Launcher()
    class _NoopLock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    service = SimpleNamespace(launcher=launcher, session_lock=lambda _sid: _NoopLock())
    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: service if name == "workflow_service" else None,
    )
    monkeypatch.setattr(
        main.config,
        "workflows",
        SimpleNamespace(enabled=True, ppt_pro=True),
    )
    monkeypatch.setattr(
        research_tools,
        "_resolve_default_llm_call",
        lambda: asyncio.sleep(0, result=lambda _prompt: "ok"),
    )
    services = {}
    monkeypatch.setattr(main.ppt_tools, "set_ppt_pro_services", lambda **kw: services.update(kw))

    main._wire_ppt_pro_services_for_startup()
    result = await services["workflow_starter"](
        {
            "topic": "Production deck",
            "session_id": "session",
            "request_id": "request",
            "turn_id": "turn",
        }
    )

    runtime = captured["context"].ports["effect"]
    assert result == {"run_id": "ppt-run"}
    assert isinstance(runtime, PptRuntime)
    assert captured["context"].ports["evaluator"] is runtime
    assert captured["context"].ports["notifier"] is main._ppt_workflow_outline_notify
    assert runtime._executor is main._PPT_PRO_RENDER_EXECUTOR


@pytest.mark.parametrize(
    ("enabled", "ppt_pro"),
    [(False, True), (True, False)],
)
def test_startup_flags_keep_legacy_fallback(monkeypatch, enabled: bool, ppt_pro: bool) -> None:
    import main

    services = {}
    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: SimpleNamespace(launcher=object())
        if name == "workflow_service"
        else None,
    )
    monkeypatch.setattr(
        main.config,
        "workflows",
        SimpleNamespace(enabled=enabled, ppt_pro=ppt_pro),
    )
    monkeypatch.setattr(main.ppt_tools, "set_ppt_pro_services", lambda **kw: services.update(kw))

    main._wire_ppt_pro_services_for_startup()

    assert services["workflow_starter"] is None
    assert services["outline_propose"] is main._ppt_outline_propose
    assert callable(services["run_blocking"])
