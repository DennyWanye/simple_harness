"""
复杂场景测试套件
测试 Agent Loop 在各种复杂情况下的行为
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from agent.agent_loop import FinalEvent, ErrorEvent


class TestComplexErrorScenarios:
    """测试复杂的错误场景"""

    @pytest.mark.asyncio
    async def test_cascading_failures(self):
        """测试级联失败：_present_final 内部处理失败场景"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        # 创建上下文对象
        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()
        context.websocket.send_json.side_effect = Exception("WebSocket closed")

        # 创建状态对象
        state = MagicMock(spec=PresentationState)

        # 模拟 send_final 也失败
        with patch('deskpet.agent.run_presenter._send_both', side_effect=Exception("Send both failed")):
            event = FinalEvent(content="test output")

            # 不应该抛出异常，应该静默失败
            await _present_final(event, context, state)

            # 验证尝试了后备发送
            assert context.websocket.send_json.called

    @pytest.mark.asyncio
    async def test_partial_send_failure(self):
        """测试部分发送失败：SSE 成功但 WebSocket 失败"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 第一次调用 _send_both 失败，但后备的 websocket.send_json 成功
        call_count = 0
        async def mock_send_both(ctx, payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("First send failed")

        with patch('deskpet.agent.run_presenter._send_both', side_effect=mock_send_both):
            event = ErrorEvent(reason="test_error", detail="Test error detail")
            await _present_error(event, context, state)

            # 验证尝试了后备发送
            assert context.websocket.send_json.called

    @pytest.mark.asyncio
    async def test_concurrent_event_delivery(self):
        """测试并发事件传递"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 模拟同时发送多个事件
        events = [
            ErrorEvent(reason=f"error_{i}", detail=f"Error {i}")
            for i in range(5)
        ]

        tasks = [_present_error(event, context, state) for event in events]
        await asyncio.gather(*tasks)

        # 验证所有事件都尝试发送
        assert context.websocket.send_json.call_count >= 5


class TestToolExecutionEdgeCases:
    """测试工具执行的边界情况"""

    @pytest.mark.asyncio
    async def test_tool_with_very_large_output(self):
        """测试工具返回超大输出"""
        from deskpet.tools.os_tools.process_tools import process_list

        # process_list 可能返回很多进程
        result = await process_list(args={"max_entries": 1000})

        # 验证结果是有效的 JSON
        assert isinstance(result, str)
        assert len(result) > 0

        # 验证可以解析
        import json
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "ok" in parsed
        assert "processes" in parsed
        assert isinstance(parsed["processes"], list)

    @pytest.mark.asyncio
    async def test_tool_with_special_characters(self):
        """测试工具处理特殊字符"""
        from deskpet.tools.os_tools.process_tools import process_list

        # 使用包含特殊字符的查询
        special_queries = [
            "python",  # 正常
            "测试",     # 中文
            "test\nname",  # 换行符
            "test\ttab",   # 制表符
            "test\"quote", # 引号
        ]

        for query in special_queries:
            result = await process_list(args={"query": query, "max_entries": 10})
            assert isinstance(result, str)

            # 验证返回的是有效 JSON
            import json
            parsed = json.loads(result)
            assert isinstance(parsed, dict)
            assert "ok" in parsed
            assert "processes" in parsed

    @pytest.mark.asyncio
    async def test_tool_timeout_handling(self):
        """测试工具超时处理"""
        from deskpet.tools.os_tools.process_tools import process_list

        # 使用 asyncio.wait_for 模拟超时
        try:
            result = await asyncio.wait_for(
                process_list(args={"max_entries": 100}),
                timeout=5.0  # 5 秒超时
            )
            assert isinstance(result, str)
        except asyncio.TimeoutError:
            pytest.fail("Tool execution timed out")


class TestStatusFlowIntegrity:
    """测试状态流完整性"""

    @pytest.mark.asyncio
    async def test_status_transition_sequence(self):
        """测试完整的状态转换序列"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 模拟完整的执行流程
        # 1. thinking → 2. running → 3. idle

        # 最终事件应该重置状态
        event = FinalEvent(content="Complete")
        await _present_final(event, context, state)

        # 验证发送了 final 事件
        assert context.websocket.send_json.called
        call_args = context.websocket.send_json.call_args_list

        # 至少应该有一次调用
        assert len(call_args) > 0

    @pytest.mark.asyncio
    async def test_error_recovery_flow(self):
        """测试错误恢复流程"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 模拟错误 → 恢复流程
        error_event = ErrorEvent(reason="test_error", detail="Test error")
        await _present_error(error_event, context, state)

        # 验证发送了错误事件
        assert context.websocket.send_json.called


class TestMemoryAndPerformance:
    """测试内存和性能"""

    @pytest.mark.asyncio
    async def test_repeated_tool_calls(self):
        """测试重复调用工具"""
        from deskpet.tools.os_tools.process_tools import process_list

        # 连续调用 50 次
        for i in range(50):
            result = await process_list(args={"query": "python", "max_entries": 5})
            assert isinstance(result, str)

            import json
            parsed = json.loads(result)
            assert isinstance(parsed, dict)
            assert "ok" in parsed
            assert "processes" in parsed

    @pytest.mark.asyncio
    async def test_concurrent_tool_calls(self):
        """测试并发工具调用"""
        from deskpet.tools.os_tools.process_tools import process_list

        # 并发调用 10 个工具
        tasks = [
            process_list(args={"query": f"test{i}", "max_entries": 5})
            for i in range(10)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 验证所有调用都成功
        for result in results:
            if isinstance(result, Exception):
                pytest.fail(f"Concurrent call failed: {result}")
            assert isinstance(result, str)


class TestBoundaryConditions:
    """测试边界条件"""

    @pytest.mark.asyncio
    async def test_empty_context_fields(self):
        """测试上下文字段为空的情况"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = ""  # 空 session_id
        context.run_id = None
        context.request_id = None
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 应该能够处理空字段
        event = FinalEvent(content="")
        await _present_final(event, context, state)

        # 不应该抛出异常
        assert True

    @pytest.mark.asyncio
    async def test_very_long_error_message(self):
        """测试超长错误消息"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 创建一个超长错误消息（10KB）
        long_message = "Error: " + "x" * 10000

        event = ErrorEvent(reason="long_error", detail=long_message)
        await _present_error(event, context, state)

        # 验证处理了长消息
        assert context.websocket.send_json.called

    @pytest.mark.asyncio
    async def test_unicode_and_emoji_in_events(self):
        """测试事件中的 Unicode 和 Emoji"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 包含各种 Unicode 字符和 Emoji
        unicode_output = "测试 🚀 ✨ 完成 ✅ データ 🎉"

        event = FinalEvent(content=unicode_output)
        await _present_final(event, context, state)

        # 验证处理了 Unicode
        assert context.websocket.send_json.called


class TestRealWorldScenarios:
    """测试真实世界场景"""

    @pytest.mark.asyncio
    async def test_quick_successive_requests(self):
        """测试快速连续请求"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 快速发送 20 个请求
        for i in range(20):
            context.run_id = f"run-{i:03d}"
            context.request_id = f"req-{i:03d}"
            context.task_scope_id = None

            event = FinalEvent(content=f"Result {i}")
            await _present_final(event, context, state)

            # 模拟极短延迟
            await asyncio.sleep(0.001)

        # 验证所有请求都尝试发送
        assert context.websocket.send_json.call_count >= 20

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        """测试混合成功和失败的场景"""
        from deskpet.agent.run_presenter import _present_final, _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 交替成功和失败
        for i in range(10):
            if i % 2 == 0:
                # 成功
                context.websocket.send_json.side_effect = None
                event = FinalEvent(content=f"Success {i}")
                await _present_final(event, context, state)
            else:
                # 失败
                context.websocket.send_json.side_effect = Exception("Intermittent failure")
                event = ErrorEvent(reason="test_error", detail=f"Error {i}")
                await _present_error(event, context, state)

        # 不应该抛出异常
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
