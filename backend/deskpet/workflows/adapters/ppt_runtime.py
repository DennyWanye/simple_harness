# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Executor-backed production operations for the PPT Pro durable graph."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import inspect
import math
import os
from concurrent.futures import Executor
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence, TypeVar, cast

from deskpet.execution.dispatch import dispatch_with_run_fence
from ...tools import ppt_tools
from ...tools import ppt_visual_review
from ..contracts import EffectKind, EffectPolicy, JsonValue, canonical_json, validate_json_value
from ..effects import (
    EffectAction,
    EffectExecutionContext,
    EffectJournal,
    EffectStateConflict,
    NormalizedToolOutcome,
    PreparedToolCall,
)


_T = TypeVar("_T")


def workflow_outline_run_id(outline_id: str) -> str | None:
    parts = outline_id.split(":", 2)
    return parts[1] if len(parts) == 3 and parts[0] == "workflow" and parts[1] else None


async def open_ppt_outline_decision(service: Any, run_id: str, outline_id: str):
    decisions = await service.human_store.list_open_decisions(run_id=run_id)
    return next((item for item in decisions if item.kind == "ppt_outline"
                 and isinstance(item.prompt, dict)
                 and str(item.prompt.get("outline_id") or "") == outline_id), None)


async def workflow_run_session(service: Any, run_id: str) -> str:
    row = await service.run_store.get_run(run_id)
    return str((row or {}).get("session_id") or "default")


def _json_safe(value: Any) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _value_hash(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: str, fallback: JsonValue) -> str:
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
    return _value_hash(fallback)


async def _resolve_effect_context(
    source: object | None, operation: str
) -> EffectExecutionContext | None:
    if source is None:
        return None
    selected = source.get(operation) if isinstance(source, Mapping) else source
    if callable(selected):
        selected = selected(operation)
    if inspect.isawaitable(selected):
        selected = await selected
    if not isinstance(selected, EffectExecutionContext):
        raise TypeError(f"effect context for {operation} is unavailable")
    return selected


async def _production_effect_context(operation: str) -> EffectExecutionContext | None:
    user_data_dir = os.environ.get("DESKPET_USER_DATA_DIR")
    if not user_data_dir:
        return None
    node_ids = {
        "image": "image_map",
        "deck_publish": "render",
        "preview_render": "preview",
    }
    node_id = node_ids.get(operation)
    if node_id is None:
        raise EffectStateConflict(f"unknown PPT effect operation: {operation}")
    journal = EffectJournal(Path(user_data_dir) / "data" / "workflow.db")
    return await journal.resolve_active_context(
        workflow_name="ppt_pro",
        workflow_version="v1",
        node_id=node_id,
    )


def _prepared_operation(
    operation: str,
    logical_effect_key: str,
    params: Mapping[str, Any],
    *,
    contract_version: str = "v1",
) -> PreparedToolCall:
    normalized = _json_safe(params)
    if not isinstance(normalized, dict):
        raise TypeError("PPT effect parameters must be a JSON object")
    stable_digest = _value_hash(
        {"operation": operation, "logical_effect_key": logical_effect_key}
    )
    schema_version: str | int = 1 if contract_version == "v1" else contract_version
    schema_hash = _value_hash(
        {
            "adapter": "ppt_runtime",
            "operation": operation,
            "version": schema_version,
        }
    )
    return PreparedToolCall.prepare(
        tool_name=f"ppt_runtime.{operation}",
        stable_call_id=f"ppt-{stable_digest[:32]}",
        final_params=normalized,
        tool_spec_version=f"ppt-runtime-{contract_version}",
        schema_hash=schema_hash,
        permission_policy_version="ppt-workflow-v1",
        effect_type=f"ppt_{operation}",
    )


class PptRuntime:
    """Production effect/evaluator port implemented by legacy PPT primitives."""

    def __init__(
        self,
        *,
        executor: Executor | None = None,
        preview_timeout_s: float = 150.0,
        effect_context: object | None = None,
        dispatch_fence_acquirer: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._executor = executor
        self._preview_timeout_s = preview_timeout_s
        self.effect_context = effect_context
        self.dispatch_fence_acquirer = dispatch_fence_acquirer

    async def _run(self, func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        call = partial(func, *args, **kwargs)
        return await loop.run_in_executor(self._executor, context.run, call)

    async def _run_effect(
        self,
        *,
        operation: str,
        logical_effect_key: str,
        params: Mapping[str, Any],
        execute: Callable[[], Any],
        succeeded: Callable[[JsonValue], bool],
        contract_version: str = "v1",
    ) -> JsonValue:
        context = await _resolve_effect_context(self.effect_context, operation)
        if context is None:
            context = await _production_effect_context(operation)
        if context is None:
            result = execute()
            result = await result if inspect.isawaitable(result) else result
            normalized = _json_safe(result)
            validate_json_value(normalized)
            return normalized

        prepared = _prepared_operation(
            operation,
            logical_effect_key,
            params,
            contract_version=contract_version,
        )
        policy = EffectPolicy(
            policy_id=f"deskpet:ppt-runtime:{operation}",
            version="v1",
            kind=EffectKind.OPAQUE_MANUAL,
        )
        begun = await context.journal.begin(
            context.fence,
            node_execution_id=context.node_execution_id,
            workflow_name=context.workflow_name,
            workflow_version=context.workflow_version,
            node_id=context.node_id,
            logical_effect_key=logical_effect_key,
            prepared=prepared,
            policy=policy,
            reuse_checkpoint=context.reuse_checkpoint,
        )
        effect_id = begun.effect.effect_id
        if begun.action in {EffectAction.REUSE, EffectAction.FAILED}:
            if begun.effect.outcome is None:
                raise EffectStateConflict(
                    f"terminal PPT effect {effect_id} has no durable outcome"
                )
            value = _json_safe(begun.effect.outcome.value)
            validate_json_value(value)
            return value
        if begun.action is not EffectAction.EXECUTE:
            raise EffectStateConflict(
                f"PPT effect {effect_id} is {begun.effect.status.value}; execution is blocked"
            )
        async def _execute_value() -> Any:
            value = execute()
            return await value if inspect.isawaitable(value) else value

        try:
            result = await dispatch_with_run_fence(
                acquire_fence=self.dispatch_fence_acquirer,
                run_id=context.fence.run_id,
                operation_kind=f"workflow.ppt.{operation}",
                operation_id=effect_id,
                invoke=_execute_value,
            )
            normalized = _json_safe(result)
            validate_json_value(normalized)
            outcome = (
                NormalizedToolOutcome.success(normalized)
                if succeeded(normalized)
                else NormalizedToolOutcome.failure(
                    f"ppt_{operation}_failed",
                    f"PPT {operation} did not produce a committed result",
                    value=normalized,
                )
            )
            committed = await context.journal.commit(context.fence, effect_id, outcome)
        except BaseException as exc:
            await context.journal.mark_uncertain(
                context.fence,
                effect_id,
                f"PPT {operation} raised {type(exc).__name__}",
            )
            raise
        if committed.outcome is None:
            raise EffectStateConflict(f"PPT effect {effect_id} committed without an outcome")
        value = _json_safe(committed.outcome.value)
        validate_json_value(value)
        return value

    async def probe_images(self, *, timeout_s: float) -> bool:
        return bool(
            await self._run(ppt_tools.probe_image_reachable, timeout_s=timeout_s)
        )

    async def generate_slide_image(
        self,
        *,
        slide_id: str,
        prompt: str,
        size: str,
        input_hash: str,
        model: str | None = None,
        prompt_schema_version: str = "legacy",
        normalizer_version: str = "legacy",
        page_revision: int = 0,
        full_page: bool = False,
        slide: Mapping[str, Any] | None = None,
    ) -> dict[str, JsonValue]:
        def generate() -> dict[str, JsonValue]:
            generate_kwargs: dict[str, Any] = {"size": size}
            if model:
                generate_kwargs["model"] = model
            raw = ppt_tools.generate_images([prompt], **generate_kwargs)
            first = raw[0] if isinstance(raw, list) and raw else {}
            normalized = _json_safe(
                first if isinstance(first, Mapping) else {"error": str(first)}
            )
            payload = cast(dict[str, JsonValue], normalized)
            path = str(payload.get("path") or "")
            if full_page and path:
                try:
                    path = ppt_tools._compose_full_page_image(path, slide or {})
                    payload["path"] = path
                except Exception as exc:  # noqa: BLE001
                    payload.update(
                        {
                            "path": None,
                            "error": f"full-page normalization failed: {exc}",
                            "error_kind": "normalization_failed",
                        }
                    )
                    path = ""
            payload.update(
                {
                    "slide_id": slide_id,
                    "input_hash": input_hash,
                    "path": path or None,
                    "model": model,
                    "prompt_schema_version": prompt_schema_version,
                    "normalizer_version": normalizer_version,
                    "page_revision": page_revision,
                    "output_hash": _file_hash(
                        path,
                        {
                            "slide_id": slide_id,
                            "input_hash": input_hash,
                            "result": payload,
                        },
                    ),
                }
            )
            validate_json_value(payload)
            return payload

        result = await self._run_effect(
            operation="image",
            logical_effect_key=f"slide:{slide_id}:{input_hash}",
            params={
                "slide_id": slide_id,
                "prompt": prompt,
                "size": size,
                "input_hash": input_hash,
                "model": model,
                "prompt_schema_version": prompt_schema_version,
                "normalizer_version": normalizer_version,
                "page_revision": page_revision,
                "full_page": full_page,
                "slide": _json_safe(dict(slide or {})),
            },
            execute=lambda: self._run(generate),
            succeeded=lambda value: isinstance(value, Mapping)
            and bool(value.get("path"))
            and not value.get("error"),
            contract_version="full-page-v1" if full_page else "v1",
        )
        if not isinstance(result, Mapping):
            raise TypeError("PPT image effect must return a JSON object")
        result_dict = cast(dict[str, JsonValue], dict(result))
        committed_path = str(result_dict.get("path") or "")
        if committed_path and not Path(committed_path).is_file():
            raise EffectStateConflict(
                f"committed PPT image is missing from disk: {committed_path}"
            )
        return result_dict

    async def render_ppt(
        self,
        *,
        slides: Sequence[Mapping[str, Any]],
        topic: str,
        title: str,
        author: str,
        theme: str,
        output_path: str | None,
        render_mode: str,
        render_revision: int,
        input_hash: str,
        editable_required: bool = False,
    ) -> dict[str, JsonValue]:
        del topic

        def render() -> dict[str, JsonValue]:
            parsed_slides = ppt_tools.parse_outline([dict(slide) for slide in slides])
            if render_mode == "full_page_images":
                raw = ppt_tools._render_full_page_images(
                    parsed_slides,
                    title=title,
                    author=author,
                    output_path=output_path,
                )
            else:
                raw = ppt_tools._render_pro(
                    parsed_slides,
                    theme=theme,
                    title=title,
                    author=author,
                    output_path=output_path,
                    image_mode=render_mode == "images",
                    probe_timeout_s=0.1,
                    notify=lambda _message: None,
                    editable_required=editable_required,
                    images_prepared=render_mode == "images",
                )
            normalized = _json_safe(raw)
            result = cast(dict[str, JsonValue], normalized)
            path = str(result.get("path") or "")
            result.update(
                {
                    "path": path or None,
                    "input_hash": input_hash,
                    "output_hash": _file_hash(path, result),
                    "render_mode": render_mode,
                    "render_revision": render_revision,
                }
            )
            validate_json_value(result)
            return result

        result = await self._run_effect(
            operation="deck_publish",
            logical_effect_key=f"deck:{input_hash}:{render_revision}",
            params={
                "slides": slides,
                "title": title,
                "author": author,
                "theme": theme,
                "output_path": output_path,
                "render_mode": render_mode,
                "render_revision": render_revision,
                "input_hash": input_hash,
                "editable_required": editable_required,
            },
            execute=lambda: self._run(render),
            succeeded=lambda value: isinstance(value, Mapping)
            and bool(value.get("ok"))
            and bool(value.get("path")),
            contract_version="full-page-v1"
            if render_mode == "full_page_images"
            else "v1",
        )
        if not isinstance(result, Mapping):
            raise TypeError("PPT deck publish effect must return a JSON object")
        return cast(dict[str, JsonValue], dict(result))

    async def render_preview(
        self, *, path: str, render_hash: str
    ) -> list[dict[str, JsonValue]]:
        def render() -> list[dict[str, JsonValue]]:
            if not path or not ppt_tools._ppt_preview_render_enabled():
                return []
            renderer = ppt_tools._get_ppt_renderer()
            if not renderer.com_render_available():
                return []
            output_dir = Path(path).expanduser().resolve().with_suffix(".preview")
            render_fn = getattr(renderer, "render_pptx_to_pngs_safe", None)
            if callable(render_fn):
                previews = list(
                    render_fn(
                        str(Path(path).expanduser().resolve()),
                        str(output_dir),
                        timeout=self._preview_timeout_s,
                    )
                    or []
                )
            else:
                previews = list(
                    renderer.render_pptx_to_pngs(
                        str(Path(path).expanduser().resolve()), str(output_dir)
                    )
                    or []
                )

            refs: list[dict[str, JsonValue]] = []
            for index, preview_path in enumerate(previews, start=1):
                normalized_path = str(Path(preview_path).expanduser().resolve())
                sha256 = _file_hash(
                    normalized_path,
                    {
                        "render_hash": render_hash,
                        "page": index,
                        "path": normalized_path,
                    },
                )
                refs.append(
                    {
                        "kind": "image",
                        "path": normalized_path,
                        "mime": "image/png",
                        "title": f"Preview slide {index}",
                        "page": index,
                        "sha256": sha256,
                        "artifact_ref": f"preview:{sha256}",
                        "render_hash": render_hash,
                    }
                )
            validate_json_value(refs)
            return refs

        result = await self._run_effect(
            operation="preview_render",
            logical_effect_key=f"preview:{render_hash}",
            params={"path": path, "render_hash": render_hash},
            execute=lambda: self._run(render),
            succeeded=lambda value: isinstance(value, list),
        )
        if not isinstance(result, list):
            raise TypeError("PPT preview render effect must return a JSON array")
        return cast(list[dict[str, JsonValue]], [dict(item) for item in result])

    async def evaluate_ppt(
        self,
        *,
        previews: Sequence[Mapping[str, Any]],
        slides: Sequence[Mapping[str, Any]],
    ) -> dict[str, JsonValue]:
        def evaluate() -> dict[str, JsonValue]:
            preview_values = [dict(item) for item in previews]
            slide_values = [dict(item) for item in slides]
            paths = [str(item.get("path") or "") for item in preview_values]
            paths = [path for path in paths if path]
            metadata: list[dict[str, Any]] = []
            for record in slide_values:
                slide = record.get("slide", record)
                slide = dict(slide) if isinstance(slide, Mapping) else {}
                bullets = slide.get("bullets")
                metadata.append(
                    {
                        "title": str(slide.get("title") or ""),
                        "variant": str(
                            slide.get("image_variant") or slide.get("layout") or ""
                        ),
                        "n_bullets": len(bullets) if isinstance(bullets, list) else 0,
                        "expected_text": {
                            "title": str(slide.get("title") or ""),
                            "subtitle": str(slide.get("subtitle") or ""),
                            "bullets": list(bullets) if isinstance(bullets, list) else [],
                            "left_title": str(slide.get("left_title") or ""),
                            "left": list(slide.get("left") or []),
                            "right_title": str(slide.get("right_title") or ""),
                            "right": list(slide.get("right") or []),
                            "quote": str(slide.get("quote") or ""),
                            "cite": str(slide.get("cite") or ""),
                            "caption": str(slide.get("caption") or ""),
                        },
                    }
                )
            full_page = any(
                isinstance(record.get("image"), Mapping)
                and record["image"].get("mode") == "full_page_images"
                for record in slide_values
            )
            image_ready = any(
                isinstance(record.get("image"), Mapping)
                and record["image"].get("status") == "ready"
                for record in slide_values
            )
            mode = (
                "full_page_images"
                if full_page
                else "image" if image_ready else "template"
            )
            raw_reviews = (
                ppt_visual_review.review_slides(paths, metadata, mode=mode)
                if paths
                else []
            )
            reviews_value = _json_safe(raw_reviews)
            reviews = cast(list[dict[str, JsonValue]], reviews_value)
            issues: list[dict[str, JsonValue]] = []
            for review in reviews:
                if bool(review.get("ok", True)):
                    continue
                issue = dict(review)
                try:
                    page = int(issue.get("page") or 0)
                except (TypeError, ValueError):
                    page = 0
                if 0 < page <= len(slide_values):
                    slide_id = slide_values[page - 1].get("slide_id")
                    if slide_id:
                        issue["slide_id"] = str(slide_id)
                issues.append(issue)
            score = None
            if reviews:
                score = sum(bool(item.get("ok", True)) for item in reviews) / len(
                    reviews
                )
            result: dict[str, JsonValue] = {
                "issues": issues,
                "reviews": reviews,
                "score": score,
                "reviewed_pages": len(reviews),
                "preview_hash": _value_hash(_json_safe(preview_values)),
            }
            validate_json_value(result)
            return result

        return await self._run(evaluate)


PptRuntimeAdapter = PptRuntime

__all__ = ["PptRuntime", "PptRuntimeAdapter"]
