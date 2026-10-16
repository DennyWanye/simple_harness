"""阶段 8.7 特性化测试：下层（模型注册表、路由、OpenAI 兼容提供方）调用的四个上下文尝试函数。

经下层模块里的名字调用（依赖倒置前后都是这几个入口），锁住：
- 没设置尝试记录库时直接放行；
- 有完整外层尝试时只标记"已发出"、不另起尝试；
- 传输重试计数；
- 设置后再清空，恢复直接放行。
"""

from __future__ import annotations

import pytest
from agent.context_report import (
    ContextAttemptReport,
    ContextAttemptStore,
    set_context_attempt_store,
)
from llm import registry as llm_registry
from llm.context_messages import ProviderAttemptOptions, context_attempt_scope
from providers.openai_compatible_domains import chat as compat_chat
from providers.openai_compatible_domains import streaming as compat_streaming
from router import hybrid_router


def _scope():
    return context_attempt_scope(
        ProviderAttemptOptions(
            purpose="agent_response", session_id="s", request_id="r", attempt_id="a"
        )
    )


@pytest.fixture
def store():
    s = ContextAttemptStore()
    s.plan(ContextAttemptReport("s", "r", "a", "agent_response"))
    set_context_attempt_store(s)
    yield s
    set_context_attempt_store(None)


async def _invoke():
    return "ok"


async def _iterate():
    yield 1
    yield 2


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [llm_registry, compat_chat])
async def test_call_passes_through_without_store(module):
    set_context_attempt_store(None)
    result = await module.auto_context_attempt_call(
        invoke=_invoke, provider=object(), messages=[], tools=None, mark_sent_at_dispatch=True
    )
    assert result == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [llm_registry, compat_chat])
async def test_call_with_complete_outer_attempt_marks_sent(module, store):
    with _scope():
        result = await module.auto_context_attempt_call(
            invoke=_invoke, provider=object(), messages=[], tools=None, mark_sent_at_dispatch=True
        )
    assert result == "ok"
    assert [r.state for r in store.list_for_session("s")] == ["sent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [hybrid_router, compat_streaming])
async def test_iter_passes_through_without_store(module):
    set_context_attempt_store(None)
    items = [
        x
        async for x in module.auto_context_attempt_iter(
            iterator=_iterate,
            provider=object(),
            messages=[],
            tools=None,
            mark_sent_at_dispatch=True,
        )
    ]
    assert items == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [hybrid_router, compat_streaming])
async def test_iter_with_complete_outer_attempt_marks_sent(module, store):
    with _scope():
        items = [
            x
            async for x in module.auto_context_attempt_iter(
                iterator=_iterate,
                provider=object(),
                messages=[],
                tools=None,
                mark_sent_at_dispatch=True,
            )
        ]
    assert items == [1, 2]
    assert [r.state for r in store.list_for_session("s")] == ["sent"]


@pytest.mark.parametrize("module", [compat_chat, compat_streaming])
def test_mark_sent_and_transport_retry(module, store):
    with _scope():
        module.mark_current_attempt_sent()
        module.mark_current_attempt_transport_retry()
    saved = store.list_for_session("s")[0]
    assert saved.state == "sent"
    assert saved.transport_retry_count == 1


@pytest.mark.parametrize("module", [compat_chat, compat_streaming])
def test_markers_are_noops_after_store_cleared(module, store):
    set_context_attempt_store(None)
    with _scope():
        module.mark_current_attempt_sent()
        module.mark_current_attempt_transport_retry()
    saved = store.list_for_session("s")[0]
    assert saved.state == "planned"
    assert saved.transport_retry_count == 0
