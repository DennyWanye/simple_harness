"""上下文压缩模型与任务投影：压缩模型选择、任务投影器、初始上下文快照与请求附带任务快照"""

from __future__ import annotations

from config import load_config
from deskpet.agent.assembler.bundle import ContextFragment
from deskpet.agent.context_task import (
    GoalProjectionAdapter,
    ReceiptProjectionAdapter,
    TaskContextProjector,
    TaskFact,
    WorkflowProjectionAdapter,
)
from llm.context_messages import ContextMessageMeta, tag_message
from p4_ipc import _context_compaction_config_path, _read_context_compaction_model

from host.runtime import _CONFIG_PATH, config, logger, service_context
from host.tools_bootstrap import _get_receipt_store


# ─── WI-T2.1 v3 build_agent 工厂（接电 VerifyGate）─────────────────
#
# Testability refactor: AgentLoop 构造逻辑从 chat handler inline 抽出，
# 让 wiring 测试可直接 `from main import build_agent; agent = build_agent(cfg, ...)`
# 断言 `agent.verify_gate is not None`，**不依赖** import main 全跑（v1 评审 P1-1
# 反驳 `import main; reload` 在 monolithic main.py 99% 翻车）。
#
# 修 last-mile P0-1：last-mile 已写好 VerifyGate / ReceiptStore / agent_loop
# 内部 verify check，但 main.py:_AgentLoop(...) 构造调用没传 `verify_gate=` /
# `receipt_store=` / `max_verify_nudges=`，导致生产抓获率 0%。本工厂兜底所有
# 接电点 — 调用方只需替换 `_AgentLoop(...)` 为 `build_agent(cfg, ...)`。
def _configured_context_compaction_model(cfg) -> str:
    context_cfg = getattr(cfg, "context", None)
    compaction_cfg = getattr(context_cfg, "compaction", None)
    return str(getattr(compaction_cfg, "model", "follow_session") or "follow_session")


def _live_context_compaction_model() -> str:
    """Read the mtime-cached user config for the next compaction cycle."""

    try:
        # The settings IPC persists the user override under the active
        # DESKPET_USER_DATA_DIR.  Read that exact source first; consulting only
        # the boot DESKPET_CONFIG path made the UI acknowledge a save that the
        # next AgentLoop compaction never observed.

        if _context_compaction_config_path().is_file():
            return _read_context_compaction_model()
        return _configured_context_compaction_model(load_config(_CONFIG_PATH))
    except Exception as exc:  # noqa: BLE001 - preserve the last startup value
        logger.warning("context_compaction_model_reload_failed err=%s", str(exc)[:160])
        return _configured_context_compaction_model(config)


def _build_context_task_projector(
    *,
    session_goal_store=None,
    receipt_store=None,
):
    """Build the shared read-only projection owner from live authority stores."""

    services = service_context
    task_graph = None
    workflow_service = None
    if services is not None:
        try:
            task_graph = services.get("task_graph_store")
        except Exception:
            pass
        try:
            workflow_service = services.get("workflow_service")
        except Exception:
            pass
        if session_goal_store is None:
            try:
                session_goal_store = services.get("session_goal_store")
            except Exception:
                pass
    if receipt_store is None:
        getter = _get_receipt_store
        if callable(getter):
            receipt_store = getter()
    return TaskContextProjector(
        workflow_source=(
            WorkflowProjectionAdapter(workflow_service) if workflow_service is not None else None
        ),
        goal_source=(
            GoalProjectionAdapter(session_goal_store, task_graph)
            if session_goal_store is not None
            else None
        ),
        receipt_source=(
            ReceiptProjectionAdapter(receipt_store) if receipt_store is not None else None
        ),
    )


async def _project_initial_context_snapshot(
    *,
    session_id: str,
    request_id: str,
    task_scope_id: str = "",
    user_text: str,
    explicit_new: bool = False,
):
    """Project active authorities plus typed evidence for a new long task."""

    evidence = (
        (
            TaskFact(
                fact_id=f"request:{request_id}",
                text=str(user_text).strip(),
                source="session_request",
                status="pending",
            ),
        )
        if explicit_new and str(user_text).strip()
        else ()
    )
    return await _build_context_task_projector().project(
        effective_sid=session_id,
        request_id=request_id,
        task_scope_id=task_scope_id,
        user_text=user_text,
        explicit_new=explicit_new,
        structured_evidence=evidence,
    )


def _attach_task_snapshot_to_request(bundle, messages, snapshot) -> None:
    """Mount one protected task fragment into bundle facts and wire messages."""

    if snapshot is None:
        return

    task = snapshot.to_protected_fragment()
    if not any(fragment.fragment_id == task.fragment_id for fragment in bundle.fragments):
        bundle.fragments.append(
            ContextFragment(
                fragment_id=task.fragment_id,
                source=task.source,
                role="system",
                content=task.content,
                lifetime="task",
                placement="prefix",
                priority=task.priority,
                trim_policy="never",
                protected=True,
                reason=task.reason,
            )
        )
    tagged = tag_message(
        {"role": "system", "content": task.content},
        ContextMessageMeta(
            placement="prefix",
            lifetime="task",
            source=task.source,
            protected=True,
            trim_policy="never",
            fragment_id=task.fragment_id,
            priority=task.priority,
            reason=task.reason,
        ),
    )
    insert_at = next(
        (index for index, message in enumerate(messages) if message.get("role") != "system"),
        len(messages),
    )
    messages.insert(insert_at, tagged)
