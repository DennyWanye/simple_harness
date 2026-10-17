# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S1 L2 session database — clean-room rewrite.

Attribution:
    Interface style 参考 Hermes AIAgent `SessionStore`（MIT license），但
    代码是 clean-room 重写，不含 Hermes 源码复制。设计依据在
    ``openspec/changes/p4-poseidon-agent-harness/design.md`` §D-ARCH-1 /
    §D-IMPL-2，spec Requirement "Session Database (L2) — SQLite with FTS5"。

Responsibilities:
    * 拉起 state.db（通过 ``schema.initialize_state_db`` 做迁移 + .bak 回滚）
    * WAL 模式 + 应用层 SQLITE_BUSY 重试（jitter exponential backoff，≤5 次）
    * 可选 sqlite-vec 虚拟表 ``messages_vec``（load_extension 失败 → warn + 降级）
    * 具体读写按领域拆在 ``session_db_domains/`` 的各个混入类里（会话、消息、
      提供方尝试账本、上下文用量、工作项等），``SessionDB`` 把它们组合起来。

这是 Host 的会话账本，不做向量召回、嵌入计算或文件记忆（认知记忆系统已移除）。
使用方：``host/stores_bootstrap.py`` 启动时建库，``deskpet/workflows/adapters/product_delivery.py``
与 ``scripts/reset_agent_data.py`` 也直接用它。
"""

from __future__ import annotations

from .session_db_domains.companion_ingress import CompanionIngressMixin
from .session_db_domains.companion_routes import CompanionRouteMixin
from .session_db_domains.context_usage import ContextUsageMixin
from .session_db_domains.messages import MessageMixin
from .session_db_domains.projections import ProjectionMixin
from .session_db_domains.provider_attempts import ProviderAttemptLedgerMixin
from .session_db_domains.provider_bindings import ProviderBindingMixin
from .session_db_domains.sessions import SessionMixin
from .session_db_domains.work_items import WorkItemMixin


class SessionDB(
    SessionMixin,
    ProviderAttemptLedgerMixin,
    ContextUsageMixin,
    CompanionIngressMixin,
    ProjectionMixin,
    CompanionRouteMixin,
    MessageMixin,
    ProviderBindingMixin,
    WorkItemMixin,
):
    """L2 会话存储 —— aiosqlite + WAL + FTS5（+ 可选 sqlite-vec）."""
