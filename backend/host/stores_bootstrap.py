"""会话与存储的启动装配：会话账本、各存储的模块级占位、技能加载与托管技能投影、项目绑定与会话创建服务、基础 Agent。"""

from __future__ import annotations

import paths as _paths
from agent.providers.simple_llm import SimpleLLMAgent
from agent.providers.tool_using import ToolUsingAgent

from host.llm_providers import llm
from host.runtime import config, logger, service_context
from host.state import host_state
from host.tools_bootstrap import memory_store, tool_registry
from host.window_control import _WINDOW_CONTROL_BOOTSTRAP

# 记忆系统升级 WI-M1.2: facts shadow 抽取器 + FactsStore。模块级默认
# None —— p4 服务块构造失败时 lifespan 的 fanout 仍能安全引用（同
# _summarizer_*）。_facts_store 还被 WI-M1.4 的 EnhancedRetriever 复用。
_fact_extractor = None  # type: ignore[assignment]
_facts_store = None  # type: ignore[assignment]
# Stage 2 / WI-S2.4 (D12 v2 / D-RISK-4)：episodic→semantic 异步抽 fact 的
# task 必须收进 set + add_done_callback(discard)；裸 asyncio.create_task
# 会被 Python 3.11+ GC 静默吞掉，引发"Task was destroyed but it is pending"
# warning + 数据丢失。lifespan shutdown 时 gather 等所有 task 完成。
_episodic_background_tasks: set = set()
_legacy_reflection_tasks: set = set()
_provider_workload_maintenance_tasks: set = set()
# 记忆系统升级 WI-M1.5: 长消息切块器。fanout 在 chunking flag 开时用它
# 切块 + embed 进 messages_chunks；EnhancedRetriever 读侧复用做向量召回。
_message_chunker = None  # type: ignore[assignment]
# 记忆系统升级 WI-M1.7b: procedural memory（反复问题→解法）存储。
_skill_memory_store = None  # type: ignore[assignment]

# --- Host 会话账本（SessionDB）---
# 2026-09-10：认知记忆 SDK（simple-harness-memory-sdk）已整条移除，本块只剩
# Host 自己的会话账本 SessionDB（state.db）。失败降级为 None，app 仍能启动。
_summarizer_state_db_path = None
_state_db_path = _paths.user_data_dir() / "data" / "state.db"
try:
    from deskpet.memory.session_db import SessionDB

    _session_db = SessionDB(db_path=_state_db_path)
except Exception as _session_db_exc:  # noqa: BLE001
    logger.warning("session_db_wiring_failed error=%s", str(_session_db_exc)[:200], exc_info=True)
    _session_db = None
# 旧记忆装配块曾赋值、且 main.py 别处仍可能引用的全局变量统一置 None，避免 NameError。
_file_memory = None
_image_worker = None
_fact_extractor = None
_embedder = None
_vector_worker = None
_retriever = None
try:
    from deskpet.companion.skills import (
        ManagedSkillDiscoveryProjection as _ManagedSkillDiscoveryProjection,
    )
    from deskpet.companion.skills import (
        inventory_first_party_skill_packs as _inventory_first_party_skill_packs,
    )
    from deskpet.skills.loader import SkillLoader as _SkillLoader

    _first_party_skill_inventory = _inventory_first_party_skill_packs(
        _paths.first_party_capability_pack_roots()
    )
    _managed_skill_projection = _ManagedSkillDiscoveryProjection(_first_party_skill_inventory)
    _skill_loader = _SkillLoader(
        skill_dirs=[],
        skill_scopes=[],
        enable_watch=False,
        knowledge_enabled=bool(
            getattr(getattr(config, "skills", None), "knowledge_enabled", False)
        ),
    )
except Exception as _skills_exc:  # noqa: BLE001
    logger.warning("skills_wiring_failed error=%s", str(_skills_exc)[:200], exc_info=True)
    _managed_skill_projection = None
    _skill_loader = None
_session_history_planner = None
_memory_manager = None
_workspace_mem_store = None
service_context.register("context_assembler", None)
service_context.register("session_db", _session_db)


def _freeze_current_session_owner():
    """Resolve the trusted owner when creation runs, not during startup wiring.

    Session services are composed before Companion identity opens. Capturing
    ``None`` at that point left every later Project Session ownerless.
    """

    identity_gate = service_context.get("companion_identity_gate")
    if identity_gate is None:
        return None
    return identity_gate.freeze()


if _session_db is not None:
    from deskpet.session.project_binding import (
        ProjectBindingService as _ProjectBindingService,
    )
    from deskpet.session.project_binding import (
        SessionCreationService as _SessionCreationService,
    )

    _project_binding_service = _ProjectBindingService(
        _state_db_path, write_lock=_session_db._write_lock
    )
    host_state.session_creation_service = _SessionCreationService(
        _project_binding_service,
        session_db=_session_db,
        owner_identity_resolver=_freeze_current_session_owner,
        provider_binding_validator=None,
        documents_root=(
            _WINDOW_CONTROL_BOOTSTRAP.documents_root
            if _WINDOW_CONTROL_BOOTSTRAP is not None
            else None
        ),
        documents_identity=(
            _WINDOW_CONTROL_BOOTSTRAP.documents_identity
            if _WINDOW_CONTROL_BOOTSTRAP is not None
            else None
        ),
        allow_test_projectless=False,
    )
else:
    _project_binding_service = None
    host_state.session_creation_service = None
service_context.register("project_binding_service", _project_binding_service)
service_context.register("session_creation_service", host_state.session_creation_service)
service_context.register("vector_worker", None)
service_context.register("embedder", None)
service_context.register("skill_loader", _skill_loader)
service_context.register("managed_skill_discovery_projection", _managed_skill_projection)
# ContextPageInStore 是请求作用域的 page-in 引用权威（Context OS），不依赖
# 记忆 SDK；product SDK tool catalog 的 context_page_in handler 需要它作为 provider。
from deskpet.tools.context_page_in_tools import ContextPageInStore as _ContextPageInStore

service_context.register("context_page_in_store", _ContextPageInStore())

base_agent = SimpleLLMAgent(llm, memory=memory_store)
agent = ToolUsingAgent(base=base_agent, registry=tool_registry)
service_context.register("agent_engine", agent)
