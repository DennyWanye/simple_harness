# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import pytest

from deskpet.tools import ppt_tools
from deskpet.tools.ppt_tools import SlideOutline


def _slides() -> list[SlideOutline]:
    return [
        SlideOutline(
            layout="image_full",
            title="Cover",
            subtitle="Sub",
            bullets=["one", "two", "three"],
            image_prompt="cover prompt",
        ),
        SlideOutline(
            layout="image_full",
            title="Insight",
            bullets=["alpha", "beta", "gamma"],
            image_prompt="insight prompt",
        ),
    ]


def test_ppt_pro_accepts_short_decks_and_preserves_requested_page_count():
    assert ppt_tools._PPT_PRO_SCHEMA["parameters"]["properties"]["pages"]["minimum"] == 1
    assert ppt_tools._coerce_ppt_pro_args({"topic": "short", "pages": 2})["pages"] == 2
    assert ppt_tools._coerce_ppt_pro_args({"topic": "single", "pages": 1})["pages"] == 1


def test_callout_extraction_does_not_treat_citation_ids_as_metrics():
    items = [
        "Kernel lifecycle contract [^11]",
        "Coverage reached 85%",
    ]

    callouts, remaining = ppt_tools._extract_callouts(items)

    assert callouts == [("85%", "Coverage reached")]
    assert remaining == ["Kernel lifecycle contract [^11]"]


def test_callout_extraction_preserves_parenthetical_defect_counts_as_prose():
    items = [
        (
            "P0/P1缺陷（1项）：Voice绕过ProductTurnPreparer，直接影响用户入口的"
            "历史、Memory、Context OS、Profile Catalog 和请求级工具快照的一致性"
        ),
        "P1缺陷（2项）：核心模块过度膨胀、核心层依赖方向不干净",
        "Coverage reached 85%",
    ]

    callouts, remaining = ppt_tools._extract_callouts(items)

    assert callouts == [("85%", "Coverage reached")]
    assert remaining == items[:2]


def test_callout_extraction_preserves_spaced_parenthetical_counts_as_prose():
    items = [
        "P1缺陷（ 2项）：模块拆分",
        "问题( 1 条 )：history mismatch",
        "Coverage reached 85%",
    ]

    callouts, remaining = ppt_tools._extract_callouts(items)

    assert callouts == [("85%", "Coverage reached")]
    assert remaining == items[:2]


def test_callout_extraction_preserves_label_that_cannot_fit_metric_card():
    long_evidence = (
        "Harness 711 passed：覆盖核心运行时的稳定性，验证主循环、工具系统、"
        "上下文与状态管理的可靠性"
    )
    short_evidence = "PPT 415 passed：覆盖演示与功能逻辑的正确性"

    callouts, remaining = ppt_tools._extract_callouts(
        [long_evidence, short_evidence]
    )

    assert callouts == [("415", "PPT passed：覆盖演示与功能逻辑的正确性")]
    assert remaining == [long_evidence]


def test_callout_extraction_uses_powerpoint_two_line_capacity():
    label_at_limit = "中English混合证据HarnessPPT窗口控制中英说明"
    label_over_limit = label_at_limit + "B"

    callouts, remaining = ppt_tools._extract_callouts(
        [f"85% {label_at_limit}", f"38 {label_over_limit}"]
    )

    assert len(label_at_limit) == 30
    assert len(label_over_limit) == 31
    assert callouts == [("85%", label_at_limit)]
    assert remaining == [f"38 {label_over_limit}"]


def test_render_pro_probe_false_uses_template_and_skips_image_gen(monkeypatch):
    calls: list[tuple[object, dict]] = []
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: False, raising=False)
    # Template fallback uses the deterministic bundled-template path (bug#2 fix),
    # not the legacy _default_template / vision picker.
    monkeypatch.setattr(ppt_tools, "_fallback_template_path", lambda: "tpl")
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: None)

    def fake_ppt_create(outline, **kwargs):
        calls.append((outline, kwargs))
        return {"ok": True, "path": "deck.pptx", "artifacts": []}

    monkeypatch.setattr(ppt_tools, "ppt_create", fake_ppt_create)
    messages: list[str] = []

    result = ppt_tools._render_pro(
        _slides(),
        theme="minimal",
        title="Deck",
        author="Tester",
        output_path=None,
        image_mode=True,
        probe_timeout_s=0.01,
        notify=messages.append,
    )

    assert result["ok"] is True
    outline, kwargs = calls[0]
    assert kwargs["skip_image_gen"] is True
    assert kwargs["template"] == "tpl"
    # _render_pro passes asdict() dicts to ppt_create (bug#2-b fix), not SlideOutline.
    assert all(slide["image_prompt"] is None for slide in outline)
    assert messages


@pytest.mark.parametrize("error_kind", ["connectivity", "model_unavailable"])
def test_autofill_first_image_connectivity_or_model_unavailable_falls_back(monkeypatch, error_kind):
    calls = []

    def fake_generate(prompts, *, size, model=None):
        calls.append(list(prompts))
        return [{"prompt": prompts[0], "path": None, "error": "nope", "error_kind": error_kind}]

    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)

    reachable, n_ok = ppt_tools._autofill_with_connectivity_gate(_slides())

    assert (reachable, n_ok) == (False, 0)
    assert len(calls) == 1


def test_autofill_first_image_content_failure_continues(monkeypatch):
    calls = []

    def fake_generate(prompts, *, size, model=None):
        calls.append(list(prompts))
        if len(calls) == 1:
            return [{"prompt": prompts[0], "path": None, "error": "blocked", "error_kind": "content"}]
        return [{"prompt": prompts[0], "path": "ok.png", "error": None, "error_kind": None}]

    slides = _slides()
    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)

    reachable, n_ok = ppt_tools._autofill_with_connectivity_gate(slides)

    assert (reachable, n_ok) == (True, 1)
    assert slides[0].image_path is None
    assert slides[1].image_path == "ok.png"
    assert len(calls) == 2


def test_autofill_all_images_fail_returns_fallback(monkeypatch):
    def fake_generate(prompts, *, size, model=None):
        return [{"prompt": p, "path": None, "error": "blocked", "error_kind": "content"} for p in prompts]

    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)

    assert ppt_tools._autofill_with_connectivity_gate(_slides()) == (False, 0)


def test_render_pro_all_success_uses_image_path_without_second_generation(monkeypatch):
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: True, raising=False)

    def fake_generate(prompts, *, size, model=None):
        return [{"prompt": p, "path": f"{i}.png", "error": None, "error_kind": None} for i, p in enumerate(prompts)]

    calls: list[tuple[object, dict]] = []
    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: None)
    monkeypatch.setattr(ppt_tools, "ppt_create", lambda outline, **kwargs: calls.append((outline, kwargs)) or {"ok": True})

    result = ppt_tools._render_pro(
        _slides(),
        theme="minimal",
        title="Deck",
        author="Tester",
        output_path=None,
        image_mode=True,
        probe_timeout_s=0.01,
        notify=lambda _msg: None,
    )

    assert result["ok"] is True
    outline, kwargs = calls[0]
    assert kwargs["skip_image_gen"] is True
    assert "template" not in kwargs or kwargs["template"] is None
    # outline items are asdict() dicts (bug#2-b), not SlideOutline objects.
    assert [s["image_path"] for s in outline] == ["0.png", "0.png"]


def test_render_pro_trusts_prepared_images_and_forces_editable_native_engine(
    monkeypatch,
):
    calls: list[tuple[object, dict]] = []
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: None)
    monkeypatch.setattr(
        ppt_tools,
        "probe_image_reachable",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("prepared workflow images must not be probed again")
        ),
    )
    monkeypatch.setattr(
        ppt_tools,
        "_autofill_with_connectivity_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("prepared workflow images must not be generated again")
        ),
    )
    monkeypatch.setattr(
        ppt_tools,
        "ppt_create",
        lambda outline, **kwargs: calls.append((outline, kwargs))
        or {"ok": True, "path": "editable.pptx"},
    )

    result = ppt_tools._render_pro(
        [
            SlideOutline(
                layout="table",
                title="Defects",
                table={"header": True, "rows": [["ID", "Result"], ["1", "Pass"]]},
            ),
            SlideOutline(
                layout="chart",
                title="Coverage",
                chart={
                    "type": "bar",
                    "categories": ["Unit"],
                    "series": [{"name": "Percent", "values": [85]}],
                },
            ),
        ],
        theme="dark",
        title="Editable",
        author="DeskPet",
        output_path=None,
        image_mode=True,
        probe_timeout_s=0.1,
        notify=lambda _message: None,
        editable_required=True,
        images_prepared=True,
    )

    assert result["ok"] is True
    outline, kwargs = calls[0]
    assert [slide["layout"] for slide in outline] == ["table", "chart"]
    assert kwargs["force_fromscratch"] is True
    assert "template" not in kwargs


def test_disk_preflight_blocks_when_low_and_passes_when_enough(monkeypatch):
    # Low free space → clear user-facing error.
    monkeypatch.setattr(ppt_tools, "_drive_free_bytes", lambda _p: 10 * 1024 * 1024)
    msg = ppt_tools._disk_preflight(image_mode=True, pages=8)
    assert msg is not None and "磁盘空间不足" in msg
    # Plenty of space → no block.
    monkeypatch.setattr(ppt_tools, "_drive_free_bytes", lambda _p: 50 * 1024 * 1024 * 1024)
    assert ppt_tools._disk_preflight(image_mode=True, pages=8) is None
    # Unknown free space (probe failed) → must never block a valid render.
    monkeypatch.setattr(ppt_tools, "_drive_free_bytes", lambda _p: None)
    assert ppt_tools._disk_preflight(image_mode=True, pages=8) is None


def test_render_pro_aborts_early_on_low_disk(monkeypatch):
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: "磁盘空间不足：C:\\ 仅剩 5MB，请清理后重试。")
    called = {"ppt_create": 0, "probe": 0}
    monkeypatch.setattr(ppt_tools, "ppt_create", lambda *a, **k: called.__setitem__("ppt_create", called["ppt_create"] + 1) or {"ok": True})
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: called.__setitem__("probe", called["probe"] + 1) or True, raising=False)

    result = ppt_tools._render_pro(
        _slides(), theme="minimal", title="D", author="T",
        output_path=None, image_mode=True, probe_timeout_s=0.01, notify=lambda _m: None,
    )

    assert result["ok"] is False
    assert "磁盘空间不足" in result["error"]
    assert called["ppt_create"] == 0  # aborted before any render
    assert called["probe"] == 0       # aborted before image probe too


def test_render_pro_partial_image_failure_notifies(monkeypatch):
    monkeypatch.setattr(ppt_tools, "probe_image_reachable", lambda *, timeout_s: True, raising=False)
    monkeypatch.setattr(ppt_tools, "_disk_preflight", lambda **_k: None)
    seq = {"n": 0}

    def fake_generate(prompts, *, size, model=None):
        seq["n"] += 1
        if seq["n"] == 1:  # first image (connectivity gate) succeeds
            return [{"prompt": prompts[0], "path": "cover.png", "error": None, "error_kind": None}]
        return [{"prompt": prompts[0], "path": None, "error": "blocked", "error_kind": "content"}]

    monkeypatch.setattr(ppt_tools, "generate_images", fake_generate, raising=False)
    monkeypatch.setattr(ppt_tools, "ppt_create", lambda outline, **kwargs: {"ok": True, "path": "deck.pptx"})
    messages: list[str] = []

    result = ppt_tools._render_pro(
        _slides(), theme="minimal", title="D", author="T",
        output_path=None, image_mode=True, probe_timeout_s=0.01, notify=messages.append,
    )

    assert result["ok"] is True
    # Reachable (gate passed) but 1 of 2 images failed → user is told, not silent.
    assert any("没生成成功" in m for m in messages)
    assert any("1/2" in m for m in messages)


def test_degrade_to_template_clears_image_fields_and_keeps_content():
    degraded = ppt_tools._degrade_to_template(_slides())

    assert degraded[0].layout in {"bullet", "section"}
    assert degraded[0].title == "Cover"
    assert degraded[0].subtitle == "Sub"
    assert degraded[0].bullets == ["one", "two", "three"]
    assert all(s.image_prompt is None and s.image_path is None for s in degraded)


def test_ppt_create_explicit_skip_false_preserves_default_image_autofill(monkeypatch, tmp_path):
    called = {"autofill": 0}

    monkeypatch.setattr(ppt_tools, "_HAS_PPTX", True)
    monkeypatch.setattr(ppt_tools, "_autofill_image_prompts", lambda slides: called.__setitem__("autofill", called["autofill"] + 1))
    monkeypatch.setattr(
        ppt_tools,
        "_render_fromscratch",
        lambda slides, theme_obj, out_path, **kwargs: {"ok": True, "path": str(out_path), "artifacts": []},
    )
    monkeypatch.setattr(ppt_tools, "_visual_review_loop", lambda *a, **k: None)
    monkeypatch.setattr(ppt_tools, "_maybe_render_preview", lambda result: None)

    result = ppt_tools.ppt_create(
        [{"layout": "image_full", "title": "T", "image_prompt": "draw it"}],
        output_path=str(tmp_path / "deck.pptx"),
        skip_image_gen=False,
    )

    assert result["ok"] is True
    assert called["autofill"] == 1
