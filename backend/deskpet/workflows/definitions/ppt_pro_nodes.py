# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable stage adapters shared by the PPT Pro v1 graph."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...tools import ppt_tools
from ..contracts import JsonValue, canonical_json, validate_json_value


FULL_PAGE_RENDER_MODE = "full_page_images"
_FULL_PAGE_REVISION_CODES = frozenset(
    {
        "blank",
        "layout_misfit",
        "text_extra",
        "text_incorrect",
        "text_missing",
        "text_occluded",
        "text_overflow",
        "text_unreadable",
    }
)
_REVISION_INSTRUCTIONS = {
    "blank": "Fill the page with the requested composition and exact copy.",
    "layout_misfit": "Recompose the page for a clearer presentation hierarchy.",
    "text_extra": "Remove every word or character not present in the exact copy.",
    "text_incorrect": "Correct all rendered text to match the exact copy verbatim.",
    "text_missing": "Render every non-empty string from the exact copy.",
    "text_occluded": "Keep all text unobstructed with strong contrast.",
    "text_overflow": "Use larger safe margins and keep all text inside the canvas.",
    "text_unreadable": "Increase text size, spacing, and contrast for readability.",
}


def content_hash(value: JsonValue) -> str:
    validate_json_value(value)
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_or_value_hash(path: str | None, value: JsonValue) -> str:
    if path:
        candidate = Path(path)
        try:
            if candidate.is_file():
                digest = hashlib.sha256()
                with candidate.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                return digest.hexdigest()
        except OSError:
            pass
    return content_hash(value)


def slide_payload(slide: ppt_tools.SlideOutline | Mapping[str, Any]) -> dict[str, JsonValue]:
    parsed = (
        copy.deepcopy(slide).normalize()
        if isinstance(slide, ppt_tools.SlideOutline)
        else ppt_tools.parse_outline([dict(slide)])[0]
    )
    payload = asdict(parsed)
    validate_json_value(payload)
    return payload


def stable_slide_records(
    slides: Sequence[ppt_tools.SlideOutline | Mapping[str, Any]],
    *,
    full_page_images: bool = False,
) -> list[dict[str, JsonValue]]:
    payloads = [slide_payload(slide) for slide in slides]
    planned_payloads = payloads
    if full_page_images:
        planned = ppt_tools.plan_full_page_layouts(
            [ppt_tools.parse_outline([payload])[0] for payload in payloads]
        )
        planned_payloads = [slide_payload(slide) for slide in planned]
    records: list[dict[str, JsonValue]] = []
    for index, payload in enumerate(planned_payloads):
        identity_payload = copy.deepcopy(payloads[index])
        identity_payload["image_path"] = None
        if full_page_images:
            identity_payload["notes"] = ""
            identity_payload["full_page_layout"] = ""
        stable_id = f"slide-{index + 1:03d}-{content_hash(identity_payload)[:12]}"
        if full_page_images:
            prompt, size = _full_page_prompt_payload(payload)
            model = ppt_tools._resolve_ppt_image_model()
            image: dict[str, JsonValue] = {
                "mode": FULL_PAGE_RENDER_MODE,
                "status": "pending",
                "prompt": prompt,
                "size": size,
                "model": model,
                "prompt_schema_version": ppt_tools.FULL_PAGE_PROMPT_SCHEMA_VERSION,
                "normalizer_version": ppt_tools.FULL_PAGE_NORMALIZER_VERSION,
                "layout_spec_version": ppt_tools.FULL_PAGE_LAYOUT_SPEC_VERSION,
                "compositor_version": ppt_tools.FULL_PAGE_COMPOSITOR_VERSION,
                "font_policy_version": ppt_tools.FULL_PAGE_FONT_POLICY_VERSION,
                "layout": str(payload.get("full_page_layout") or "text_left"),
                "page_revision": 0,
                "revision_codes": [],
                "pre_hash": _full_page_pre_hash(
                    slide_id=stable_id,
                    prompt=prompt,
                    size=size,
                    model=model,
                    visible_copy=visible_text_payload(payload),
                    layout=str(payload.get("full_page_layout") or "text_left"),
                    page_revision=0,
                    revision_codes=[],
                ),
                "post_hash": None,
                "path": None,
                "error": None,
            }
        else:
            prompt, size = _prompt_payload(payload)
            image = {
                "status": "pending" if prompt else "not_requested",
                "prompt": prompt,
                "size": size,
                "pre_hash": content_hash(
                    {"slide_id": stable_id, "prompt": prompt, "size": size}
                ),
                "post_hash": None,
                "path": payload.get("image_path"),
                "error": None,
            }
        records.append(
            {
                "slide_id": stable_id,
                "index": index,
                "slide": payload,
                "image": image,
            }
        )
    return records


def _refresh_full_page_image(
    record: dict[str, Any], *, revision: int | None = None, codes: Sequence[str] | None = None
) -> None:
    slide = dict(record["slide"])
    image = dict(record.get("image", {}))
    page_revision = int(image.get("page_revision") or 0) if revision is None else revision
    revision_codes = list(image.get("revision_codes", [])) if codes is None else list(codes)
    prompt, size = _full_page_prompt_payload(
        slide, page_revision=page_revision, revision_codes=revision_codes
    )
    model = str(image.get("model") or ppt_tools._resolve_ppt_image_model())
    layout = str(slide.get("full_page_layout") or "text_left")
    image.update(
        {
            "mode": FULL_PAGE_RENDER_MODE,
            "status": "pending",
            "prompt": prompt,
            "size": size,
            "model": model,
            "prompt_schema_version": ppt_tools.FULL_PAGE_PROMPT_SCHEMA_VERSION,
            "normalizer_version": ppt_tools.FULL_PAGE_NORMALIZER_VERSION,
            "layout_spec_version": ppt_tools.FULL_PAGE_LAYOUT_SPEC_VERSION,
            "compositor_version": ppt_tools.FULL_PAGE_COMPOSITOR_VERSION,
            "font_policy_version": ppt_tools.FULL_PAGE_FONT_POLICY_VERSION,
            "layout": layout,
            "page_revision": page_revision,
            "revision_codes": sorted(revision_codes),
            "pre_hash": _full_page_pre_hash(
                slide_id=str(record["slide_id"]),
                prompt=prompt,
                size=size,
                model=model,
                visible_copy=visible_text_payload(slide),
                layout=layout,
                page_revision=page_revision,
                revision_codes=revision_codes,
            ),
            "post_hash": None,
            "path": None,
            "error": None,
        }
    )
    slide["image_path"] = None
    record["slide"] = slide
    record["image"] = image


def normalize_full_page_records(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, JsonValue]]:
    """Upgrade legacy durable records without repeating committed image effects."""
    copied = copy.deepcopy(list(records))
    full_page = [
        record for record in copied
        if isinstance(record.get("image"), Mapping)
        and record["image"].get("mode") == FULL_PAGE_RENDER_MODE
    ]
    needs_upgrade = any(
        str(record.get("slide", {}).get("full_page_layout") or "") not in ppt_tools.FULL_PAGE_LAYOUTS
        or (
            record.get("image", {}).get("status") == "ready"
            and not Path(
                str(
                    record.get("image", {}).get("path")
                    or record.get("slide", {}).get("image_path")
                    or ""
                )
            ).is_file()
        )
        for record in full_page
    )
    if not full_page or not needs_upgrade:
        return copied  # type: ignore[return-value]

    ordered = sorted(copied, key=lambda item: int(item.get("index", 0)))
    slides = [ppt_tools.parse_outline([dict(record["slide"])])[0] for record in ordered]
    locked: dict[int, str] = {}
    ready_ids: set[str] = set()
    for position, record in enumerate(ordered):
        image = dict(record.get("image", {}))
        path = str(image.get("path") or record.get("slide", {}).get("image_path") or "")
        if image.get("status") == "ready" and path and Path(path).is_file():
            ready_ids.add(str(record.get("slide_id") or ""))
            locked[position] = str(record["slide"].get("full_page_layout") or "text_left")
    planned = ppt_tools.plan_full_page_layouts(
        slides, locked_layouts=locked, strict_coverage=False
    )
    for position, (record, slide) in enumerate(zip(ordered, planned)):
        slide_id = str(record.get("slide_id") or "")
        payload = slide_payload(slide)
        if slide_id in ready_ids:
            existing = dict(record["slide"])
            existing["full_page_layout"] = locked[position]
            record["slide"] = existing
            continue
        record["slide"] = payload
        _refresh_full_page_image(record)
    validate_json_value(copied)
    return copied  # type: ignore[return-value]


def records_to_slides(records: Sequence[Mapping[str, Any]]) -> list[ppt_tools.SlideOutline]:
    ordered = sorted(records, key=lambda item: int(item.get("index", 0)))
    return [ppt_tools.parse_outline([dict(item["slide"])])[0] for item in ordered]


def prepare_slide_records(
    records: Sequence[Mapping[str, Any]], *, image_mode: bool, reachable: bool
) -> tuple[list[dict[str, JsonValue]], str]:
    copied = copy.deepcopy(list(records))
    is_full_page = bool(copied) and all(
        isinstance(record.get("image"), Mapping)
        and record["image"].get("mode") == FULL_PAGE_RENDER_MODE
        for record in copied
    )
    render_mode = (
        FULL_PAGE_RENDER_MODE
        if image_mode and reachable and is_full_page
        else "images" if image_mode and reachable else "template"
    )
    if render_mode == "template":
        degraded = ppt_tools._degrade_to_template(records_to_slides(copied))
        for record, slide in zip(copied, degraded):
            record["slide"] = slide_payload(slide)
            image = dict(record.get("image", {}))
            if image.get("status") == "pending":
                image["status"] = "skipped_template"
            record["image"] = image
    return copied, render_mode


def next_pending_slide(records: Sequence[Mapping[str, Any]]) -> dict[str, JsonValue] | None:
    for record in sorted(records, key=lambda item: int(item.get("index", 0))):
        image = record.get("image")
        if isinstance(image, Mapping) and image.get("status") == "pending":
            return copy.deepcopy(dict(record))  # type: ignore[return-value]
    return None


async def probe_images(port: object | None, *, timeout_s: float) -> bool:
    method = _port_method(port, "probe_images", "probe_image_reachable", "probe")
    if method is None:
        return bool(ppt_tools.probe_image_reachable(timeout_s=timeout_s))
    return bool(await _await(method(timeout_s=timeout_s)))


async def generate_slide_image(
    record: Mapping[str, Any], port: object | None
) -> dict[str, JsonValue]:
    updated = copy.deepcopy(dict(record))
    image = dict(updated.get("image", {}))
    prompt = str(image.get("prompt") or "")
    size = str(image.get("size") or "1536x1024")
    full_page = image.get("mode") == FULL_PAGE_RENDER_MODE
    model = str(image.get("model") or "") or None
    method = _port_method(port, "generate_slide_image", "generate_image")
    if method is None:
        kwargs: dict[str, Any] = {"size": size}
        if model:
            kwargs["model"] = model
        raw = ppt_tools.generate_images([prompt], **kwargs)
        result = raw[0] if raw else {}
    else:
        call_kwargs: dict[str, Any] = {
            "slide_id": str(updated["slide_id"]),
            "prompt": prompt,
            "size": size,
            "input_hash": str(image["pre_hash"]),
        }
        if full_page:
            call_kwargs.update(
                {
                    "model": model,
                    "prompt_schema_version": str(image["prompt_schema_version"]),
                    "normalizer_version": str(image["normalizer_version"]),
                    "page_revision": int(image.get("page_revision") or 0),
                    "full_page": True,
                    "slide": copy.deepcopy(updated["slide"]),
                }
            )
        result = await _await(method(**call_kwargs))
    result = dict(result or {}) if isinstance(result, Mapping) else {"error": str(result)}
    path = str(result.get("path") or "") or None
    if full_page and path and Path(path).is_file() and not ppt_tools._is_normalized_full_page_image(path):
        try:
            path = ppt_tools._compose_full_page_image(path, updated["slide"])
            result["path"] = path
        except Exception as exc:  # noqa: BLE001
            result = {
                "path": None,
                "error": f"full-page normalization failed: {exc}",
                "error_kind": "normalization_failed",
            }
            path = None
    if path and Path(path).is_file():
        image.update(
            {
                "status": "ready",
                "path": path,
                "post_hash": file_or_value_hash(path, result),
                "error": None,
            }
        )
        slide = dict(updated["slide"])
        slide["image_path"] = path
        updated["slide"] = slide
    else:
        error_kind = str(
            result.get("error_kind")
            or ("missing_page_image" if path else "generation_failed")
        )
        image.update(
            {
                "status": "fallback_required"
                if error_kind in {"connectivity", "model_unavailable"}
                else "failed",
                "post_hash": content_hash(result),
                "error": str(result.get("error") or error_kind),
            }
        )
    updated["image"] = image
    validate_json_value(updated)
    return updated  # type: ignore[return-value]


async def render_deck(
    records: Sequence[Mapping[str, Any]],
    *,
    port: object | None,
    topic: str,
    title: str,
    author: str,
    theme: str,
    output_path: str | None,
    render_mode: str,
    render_revision: int,
) -> dict[str, JsonValue]:
    slides = records_to_slides(records)
    input_payload: dict[str, JsonValue] = {
        "slides": [dict(record) for record in records],
        "topic": topic,
        "title": title,
        "author": author,
        "theme": theme,
        "output_path": output_path,
        "render_mode": render_mode,
        "render_revision": render_revision,
    }
    input_hash = content_hash(input_payload)
    method = _port_method(port, "render_ppt", "render_deck", "render")
    if method is None:
        if render_mode == FULL_PAGE_RENDER_MODE:
            result = ppt_tools._render_full_page_images(
                slides,
                title=title,
                author=author,
                output_path=output_path,
            )
        else:
            result = ppt_tools._render_pro(
                slides,
                theme=theme,
                title=title,
                author=author,
                output_path=output_path,
                image_mode=render_mode == "images",
                probe_timeout_s=0.1,
                notify=lambda _message: None,
            )
    else:
        result = await _await(
            method(
                slides=[asdict(slide) for slide in slides],
                topic=topic,
                title=title,
                author=author,
                theme=theme,
                output_path=output_path,
                render_mode=render_mode,
                render_revision=render_revision,
                input_hash=input_hash,
            )
        )
    payload = dict(result or {}) if isinstance(result, Mapping) else {"ok": False, "error": str(result)}
    payload["input_hash"] = input_hash
    path = str(payload.get("path") or "") or None
    payload["output_hash"] = file_or_value_hash(path, payload)
    validate_json_value(payload)
    return payload  # type: ignore[return-value]


async def render_preview(render_ref: Mapping[str, Any], port: object | None) -> list[dict[str, JsonValue]]:
    page_images = render_ref.get("page_images", [])
    if (
        render_ref.get("render_mode") == FULL_PAGE_RENDER_MODE
        and isinstance(page_images, Sequence)
        and not isinstance(page_images, (str, bytes))
    ):
        refs: list[dict[str, JsonValue]] = []
        render_hash = str(render_ref.get("output_hash") or "")
        for index, item in enumerate(page_images, start=1):
            if not isinstance(item, Mapping):
                continue
            path = str(item.get("path") or "")
            if not path or not Path(path).is_file():
                continue
            page = int(item.get("page") or index)
            sha256 = file_or_value_hash(path, {"page": page, "path": path})
            refs.append(
                {
                    "kind": "image",
                    "path": path,
                    "mime": "image/png",
                    "title": f"Preview slide {page}",
                    "page": page,
                    "sha256": sha256,
                    "artifact_ref": f"preview:{sha256}",
                    "render_hash": render_hash,
                }
            )
        validate_json_value(refs)
        return refs
    method = _port_method(port, "render_preview", "preview_ppt", "preview")
    if method is None:
        artifacts = render_ref.get("artifacts", [])
        return [dict(item) for item in artifacts if isinstance(item, Mapping) and item.get("kind") == "image"]
    raw = await _await(
        method(
            path=str(render_ref.get("path") or ""),
            render_hash=str(render_ref.get("output_hash") or ""),
        )
    )
    values = raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else []
    result = [dict(item) for item in values if isinstance(item, Mapping)]
    validate_json_value(result)
    return result  # type: ignore[return-value]


async def evaluate_visuals(
    previews: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    evaluator: object | None,
) -> dict[str, JsonValue]:
    method = _port_method(evaluator, "evaluate_ppt", "review_slides", "evaluate")
    if method is None:
        return {"issues": [], "score": None, "review_hash": content_hash([])}
    raw = await _await(
        method(
            previews=[dict(item) for item in previews],
            slides=[dict(item) for item in records],
        )
    )
    if isinstance(raw, Mapping):
        result = dict(raw)
    else:
        result = {"issues": list(raw or [])}
    issues = result.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    result["issues"] = issues
    result["review_hash"] = content_hash(result)
    validate_json_value(result)
    return result  # type: ignore[return-value]


def apply_visual_revision(
    records: Sequence[Mapping[str, Any]], issues: Sequence[Mapping[str, Any]]
) -> list[dict[str, JsonValue]]:
    copied = copy.deepcopy(list(records))
    by_index = {int(item.get("index", -1)): item for item in copied}
    by_id = {str(item.get("slide_id", "")): item for item in copied}
    grouped: dict[str, set[str]] = {}
    for issue in issues:
        record = by_id.get(str(issue.get("slide_id") or ""))
        if record is None:
            raw_page = issue.get("page", issue.get("slide_index", -1))
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            record = by_index.get(page - 1 if page > 0 else page)
        if record is None:
            continue
        image = record.get("image", {})
        if isinstance(image, Mapping) and image.get("mode") == FULL_PAGE_RENDER_MODE:
            slide_id = str(record.get("slide_id") or "")
            grouped.setdefault(slide_id, set()).update(_normalized_revision_codes(issue))
            continue
        slide = dict(record["slide"])
        action = str(issue.get("action") or "shrink_text")
        if action == "change_variant" and issue.get("variant"):
            slide["image_variant"] = str(issue["variant"])
        elif action == "change_layout" and issue.get("layout"):
            slide["layout"] = str(issue["layout"])
        else:
            current = float(slide.get("font_scale") or 1.0)
            slide["font_scale"] = max(0.5, round(current * 0.9, 3))
        record["slide"] = slide

    for slide_id, code_set in grouped.items():
        record = by_id[slide_id]
        slide = dict(record["slide"])
        image = dict(record["image"])
        revision = int(image.get("page_revision") or 0) + 1
        codes = sorted(code_set or {"text_unreadable"})
        if "layout_misfit" in codes:
            index = int(record.get("index", 0))
            neighbors = {
                str(item.get("slide", {}).get("full_page_layout") or "")
                for item in copied
                if abs(int(item.get("index", -99)) - index) == 1
            }
            slide["full_page_layout"] = ppt_tools.next_full_page_layout(
                slide,
                str(slide.get("full_page_layout") or image.get("layout") or "text_left"),
                forbidden=neighbors,
            )
        record["slide"] = slide
        record["image"] = image
        _refresh_full_page_image(record, revision=revision, codes=codes)
    validate_json_value(copied)
    return copied  # type: ignore[return-value]


def visible_text_payload(slide: Mapping[str, Any]) -> dict[str, JsonValue]:
    parsed = ppt_tools.parse_outline([dict(slide)])[0]
    return {
        "title": parsed.title,
        "subtitle": parsed.subtitle,
        "bullets": list(parsed.bullets),
        "left_title": parsed.left_title,
        "left": list(parsed.left),
        "right_title": parsed.right_title,
        "right": list(parsed.right),
        "quote": parsed.quote,
        "cite": parsed.cite,
        "caption": parsed.caption,
    }


def _full_page_prompt_payload(
    slide: Mapping[str, Any],
    *,
    page_revision: int = 0,
    revision_codes: Sequence[str] = (),
) -> tuple[str, str]:
    parsed = ppt_tools.parse_outline([dict(slide)])[0]
    visual_direction = str(parsed.image_prompt or "").strip()
    visual_direction = re.sub(
        r"\bno\s+(?:text|words?|letters?|typography)\b[,; ]*",
        "",
        visual_direction,
        flags=re.IGNORECASE,
    ).strip()
    safe_codes = sorted({str(code) for code in revision_codes if code in _FULL_PAGE_REVISION_CODES})
    revision_text = " ".join(
        _REVISION_INSTRUCTIONS[code]
        for code in safe_codes
        if code == "layout_misfit"
    )
    layout = parsed.full_page_layout or "text_left"
    spec = ppt_tools.FULL_PAGE_LAYOUT_SPECS[layout]
    prompt = (
        "Create a professional 16:9 presentation visual background only. "
        "The application adds all typography after generation. "
        f"LAYOUT={layout}. {spec.negative_space_instruction} "
        "Render absolutely no words, letters, numbers, labels, UI text, logos, "
        "signatures, or watermarks anywhere in the image. "
        f"VISUAL_DIRECTION={visual_direction or 'clean editorial presentation design'}. "
        f"PAGE_REVISION={int(page_revision)}. {revision_text}"
    ).strip()
    return prompt, ppt_tools.FULL_PAGE_IMAGE_SIZE


def _full_page_pre_hash(
    *,
    slide_id: str,
    prompt: str,
    size: str,
    model: str,
    visible_copy: Mapping[str, JsonValue],
    layout: str = "text_left",
    page_revision: int,
    revision_codes: Sequence[str],
) -> str:
    return content_hash(
        {
            "mode": FULL_PAGE_RENDER_MODE,
            "slide_id": slide_id,
            "prompt": prompt,
            "size": size,
            "model": model,
            "visible_copy": dict(visible_copy),
            "layout": layout,
            "layout_spec_version": ppt_tools.FULL_PAGE_LAYOUT_SPEC_VERSION,
            "prompt_schema_version": ppt_tools.FULL_PAGE_PROMPT_SCHEMA_VERSION,
            "normalizer_version": ppt_tools.FULL_PAGE_NORMALIZER_VERSION,
            "compositor_version": ppt_tools.FULL_PAGE_COMPOSITOR_VERSION,
            "font_policy_version": ppt_tools.FULL_PAGE_FONT_POLICY_VERSION,
            "target_size": list(ppt_tools.FULL_PAGE_TARGET_SIZE),
            "page_revision": page_revision,
            "revision_codes": list(sorted(revision_codes)),
        }
    )


def _normalized_revision_codes(issue: Mapping[str, Any]) -> set[str]:
    raw_codes = issue.get("reason_codes", [])
    if not isinstance(raw_codes, Sequence) or isinstance(raw_codes, (str, bytes)):
        raw_codes = []
    codes = {
        str(code)
        for code in raw_codes
        if str(code) in _FULL_PAGE_REVISION_CODES
    }
    action = str(issue.get("action") or "")
    action_map = {
        "change_layout": "layout_misfit",
        "change_variant": "layout_misfit",
        "regenerate_page": "text_incorrect",
        "shrink_text": "text_overflow",
    }
    if action in action_map:
        codes.add(action_map[action])
    return codes or {"text_unreadable"}


def _prompt_payload(slide: Mapping[str, Any]) -> tuple[str, str]:
    parsed = ppt_tools.parse_outline([dict(slide)])[0]
    if not parsed.image_prompt:
        return "", ""
    return ppt_tools._image_prompt_payload(parsed)


def _port_method(port: object | None, *names: str):
    if port is None:
        return None
    for name in names:
        method = getattr(port, name, None)
        if callable(method):
            return method
    return None


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = [
    "FULL_PAGE_RENDER_MODE",
    "apply_visual_revision",
    "content_hash",
    "evaluate_visuals",
    "file_or_value_hash",
    "generate_slide_image",
    "next_pending_slide",
    "prepare_slide_records",
    "normalize_full_page_records",
    "probe_images",
    "records_to_slides",
    "render_deck",
    "render_preview",
    "slide_payload",
    "stable_slide_records",
    "visible_text_payload",
]
