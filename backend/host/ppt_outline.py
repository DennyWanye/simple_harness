"""PPT 大纲与 PPT 生产接线：大纲卡片通知与恢复、大纲决定的提示、聊天气泡、启动时清理悬挂大纲、PPT 工作流适配器与渲染线程池。"""

from __future__ import annotations

import concurrent.futures as _cf
from dataclasses import replace

from deskpet.tools import ppt_tools
from deskpet.tools.ppt_outline_store import expire_dangling_proposed, list_history
from deskpet.workflows.adapters.ppt_runtime import (
    PptRuntime,
    open_ppt_outline_decision,
    workflow_outline_run_id,
    workflow_run_session,
)
from deskpet.workflows.contracts import WorkflowContext
from deskpet.workflows.definitions.research_core import legacy_ports
from deskpet.workflows.definitions.v1 import ppt_pro_initial_state

from host.chat_delivery import _broadcast_control
from host.llm_calls import _make_str_llm_call
from host.provider_chain import _build_agent_provider_chain
from host.runtime import logger, service_context


async def _ppt_workflow_outline_notify(payload: dict) -> None:
    """Project a durable PPT decision into the existing session card UI."""

    sid = str(payload.get("session_id") or "default")
    oid = str(payload.get("outline_id") or "")
    topic = str(payload.get("topic") or "")
    outline_md = str(payload.get("outline_md") or "")
    card = {
        "outline_id": oid,
        "topic": topic,
        "outline_md": outline_md,
        "session_id": sid,
        "sources_count": int(payload.get("sources_count") or 0),
        "no_research": bool(payload.get("no_research")),
        "history": list_history(sid, 20),
    }
    await _broadcast_control({"type": "ppt_outline_proposed", "payload": card})

    session_db = service_context.get("session_db")
    if session_db is not None and oid:
        text = f"PPT 大纲确认 · {topic}\n\n{outline_md}".strip()
        try:
            await session_db.append_message(
                session_id=sid,
                role="assistant",
                content=text,
                workflow_event_id=f"ppt-outline:{oid}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_workflow_outline_persist_failed sid=%s err=%s", sid, exc)


async def _resume_workflow_ppt_outline(service, decision, response: dict, sid: str) -> None:
    try:
        await service.resolve_decision(
            decision.decision_id,
            nonce=decision.nonce,
            response=response,
            expected_version=decision.version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ppt_workflow_outline_resume_failed run_id=%s error=%s",
            decision.run_id,
            exc,
        )
        await _ppt_notify_chat_bubble(sid, f"PPT 大纲确认失败：{exc}")


async def _resolve_workflow_ppt_outline(oid: str, response: dict) -> str | None:

    run_id = workflow_outline_run_id(oid)
    service = service_context.get("workflow_service")
    if service is None or run_id is None:
        return None
    decision = await open_ppt_outline_decision(service, run_id, oid)
    if decision is None:
        return None
    sid = await workflow_run_session(service, run_id)
    await _resume_workflow_ppt_outline(service, decision, response, sid)
    return sid


def _ppt_outline_decision_notice(action: str) -> str:
    return {
        "modify": (
            "收到修改意见，正在修改 PPT 大纲。修改完成后会在当前会话展示新版本，请再次确认。"
        ),
        "reuse": "已采用历史大纲，正在生成 PPT。完成后文件会发送到当前会话。",
        "cancel": "已取消本次 PPT 生成。",
    }.get(
        str(action or "").strip().lower(),
        "大纲已确认，正在生成 PPT。完成后文件会发送到当前会话。",
    )


async def _ppt_notify_chat_bubble(sid: str, text: str) -> None:
    target_sid = sid or "default"
    msg = {
        "type": "chat_response",
        "payload": {
            "text": str(text or ""),
            "provider": "ppt_pro",
            "session_id": target_sid,
        },
    }
    sdb = service_context.get("session_db")
    if sdb is not None and str(text or "").strip():
        try:
            msg_id = await sdb.append_message(
                session_id=target_sid,
                role="assistant",
                content=str(text or ""),
            )
            vw = service_context.get("vector_worker")
            if vw is not None and msg_id is not None:
                await vw.enqueue(msg_id, str(text or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_notify_persist_failed sid=%s err=%s", target_sid, exc)

    await _broadcast_control(msg)


def _expire_ppt_outline_dangling_for_startup() -> int:
    try:
        expired = int(expire_dangling_proposed() or 0)
        if expired:
            logger.info("ppt_outline_expired_dangling", count=expired)
        return expired
    except Exception as exc:  # noqa: BLE001
        logger.warning("ppt_outline_expire_startup_failed", error=str(exc))
        return 0


_PPT_PRO_RENDER_EXECUTOR = _cf.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="ppt_pro_render"
)


def _configure_ppt_workflow_adapter(launcher):

    runtime = PptRuntime(
        executor=_PPT_PRO_RENDER_EXECUTOR,
        dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
    )

    async def frozen_llm_call(run_id: str):
        workflow_service = service_context.get("workflow_service")
        execution_uow = getattr(workflow_service, "execution_uow", None)
        registry = service_context.get("provider_registry")
        if execution_uow is None or registry is None:
            raise RuntimeError("ppt frozen provider authority unavailable")
        record = await execution_uow.read_run(run_id)
        if record is None:
            raise RuntimeError(f"ppt execution run not found:{run_id}")
        provider_plan = record.spec.context.provider_plan
        provider_ids = tuple(
            str(item).strip() for item in provider_plan.get("providers", ()) if str(item).strip()
        )
        if not provider_ids:
            raise RuntimeError("ppt frozen provider plan is empty")
        entries = tuple(registry.get_entry(provider_id) for provider_id in provider_ids)
        if any(entry is None for entry in entries):
            missing = ",".join(
                provider_id for provider_id, entry in zip(provider_ids, entries) if entry is None
            )
            raise RuntimeError(f"ppt frozen provider unavailable:{missing}")
        providers = _build_agent_provider_chain(entries=entries, registry=registry)
        return _make_str_llm_call(providers[0], max_tokens=4096, purpose="research")

    async def context_factory(row, _start_payload) -> WorkflowContext:
        llm_call = await frozen_llm_call(str(row.get("run_id") or ""))
        ports = legacy_ports(llm_call=llm_call, search=None, extract=None)
        # The legacy Research port can carry a process-global cloud reranker.
        # A durable child must not escape its frozen provider plan through that
        # auxiliary callback.
        ports = replace(ports, llm=replace(ports.llm, rerank=None))
        return WorkflowContext(
            ports={
                "llm": ports.llm,
                "search": ports.search,
                "fetch": ports.fetch,
                "effect": runtime,
                "evaluator": runtime,
                "notifier": _ppt_workflow_outline_notify,
            },
            request_id=str(row.get("request_id") or ""),
            turn_id=str(row.get("turn_id") or ""),
        )

    def state_factory(**values):
        return ppt_pro_initial_state(
            topic=str(values["topic"]),
            run_id=str(values["run_id"]),
            thread_id=str(values["thread_id"]),
            session_id=str(values["session_id"]),
            pages=int(values.get("pages") or 8),
            depth=str(values.get("depth") or "deep"),
            theme=str(values.get("theme") or "minimal"),
            image_mode=bool(values.get("image_mode", True)),
            editable_required=bool(values.get("editable_required", True)),
            full_page_images=bool(values.get("full_page_images", False)),
            title=str(values.get("title") or values["topic"]),
            author=str(values.get("author") or "Simple Harness"),
            output_path=values.get("output_path"),
            blob_root=str(values.get("blob_root") or ""),
        )

    launcher.register_adapter(
        "ppt_pro",
        "v1",
        state_factory=state_factory,
        context_factory=context_factory,
    )
    return state_factory, context_factory


def _configure_ppt_production(*, recover: bool = True) -> None:
    workflow_service = service_context.get("workflow_service")
    launcher = getattr(workflow_service, "launcher", None)
    if launcher is not None:
        _configure_ppt_workflow_adapter(launcher)
    # Recovery is owned by Harness bootstrap/supervisor; never spawn an
    # untracked PPT recovery task here.
    ppt_tools.set_ppt_pro_services(workflow_starter=None)
