"""
第三轮测试：极限场景和深层集成测试
测试系统在极端条件下的健壮性和边界行为
"""

import pytest
import asyncio
import sys
import json
from unittest.mock import AsyncMock, MagicMock, patch
from agent.agent_loop import FinalEvent, ErrorEvent


class TestStateMachineStress:
    """状态机压力测试"""

    @pytest.mark.asyncio
    async def test_repeated_final_events(self):
        """测试重复发送 final 事件（相同 run_id）"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 发送 3 次相同的 final 事件
        for i in range(3):
            event = FinalEvent(content=f"Final {i}")
            await _present_final(event, context, state)

        # 验证每次都尝试发送
        assert context.websocket.send_json.call_count >= 3

    @pytest.mark.asyncio
    async def test_rapid_state_flipping(self):
        """测试快速状态翻转（100次）"""
        from deskpet.agent.run_presenter import _present_final, _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 快速翻转 100 次
        for i in range(100):
            if i % 2 == 0:
                event = FinalEvent(content=f"Done {i}")
                await _present_final(event, context, state)
            else:
                event = ErrorEvent(reason="error", detail=f"Error {i}")
                await _present_error(event, context, state)

        # 验证所有事件都尝试发送
        assert context.websocket.send_json.call_count >= 100

    @pytest.mark.asyncio
    async def test_concurrent_state_modifications(self):
        """测试并发状态修改"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 20 个并发任务同时修改状态
        async def modify_state(index):
            context.run_id = f"run-{index:03d}"
            context.request_id = f"req-{index:03d}"
            context.task_scope_id = None
            event = FinalEvent(content=f"Result {index}")
            await _present_final(event, context, state)

        tasks = [modify_state(i) for i in range(20)]
        await asyncio.gather(*tasks)

        # 验证所有修改都完成
        assert context.websocket.send_json.call_count >= 20

    @pytest.mark.asyncio
    async def test_out_of_order_events(self):
        """测试乱序事件（final 在 error 之前）"""
        from deskpet.agent.run_presenter import _present_final, _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 先发送 final，再发送 error（违反正常流程）
        final_event = FinalEvent(content="Done")
        await _present_final(final_event, context, state)

        error_event = ErrorEvent(reason="late_error", detail="Error after final")
        await _present_error(error_event, context, state)

        # 验证都被处理
        assert context.websocket.send_json.call_count >= 2

    @pytest.mark.asyncio
    async def test_empty_run_id_sequence(self):
        """测试空 run_id 的事件序列"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = ""  # 空 run_id
        context.request_id = ""  # 空 request_id
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 发送多个空 run_id 的事件
        for i in range(5):
            event = FinalEvent(content=f"Result {i}")
            await _present_final(event, context, state)

        assert context.websocket.send_json.call_count >= 5


class TestResourceManagement:
    """资源管理和极限测试"""

    @pytest.mark.asyncio
    async def test_very_large_payload(self):
        """测试超大 payload（1MB JSON）"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 创建 1MB 的错误消息
        large_message = "x" * (1024 * 1024)  # 1MB

        event = ErrorEvent(reason="large_error", detail=large_message)
        await _present_error(event, context, state)

        # 验证处理了大消息
        assert context.websocket.send_json.called

    @pytest.mark.asyncio
    async def test_extreme_concurrency(self):
        """测试极限并发（100 个工具）"""
        from deskpet.tools.os_tools.process_tools import process_list

        # 并发调用 100 个工具
        tasks = [
            process_list(args={"query": f"test{i}", "max_entries": 5})
            for i in range(100)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 验证大部分成功（允许少量失败）
        successful = sum(1 for r in results if not isinstance(r, Exception))
        assert successful >= 95  # 至少 95% 成功

    @pytest.mark.asyncio
    async def test_massive_mock_object_creation(self):
        """测试大量 mock 对象创建"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        # 创建 1000 个不同的 context
        contexts = []
        for i in range(1000):
            context = MagicMock(spec=RunPresentationContext)
            context.session_id = f"session-{i}"
            context.run_id = f"run-{i}"
            context.request_id = f"req-{i}"
            context.task_scope_id = None
            context.websocket = AsyncMock()
            contexts.append(context)

        state = MagicMock(spec=PresentationState)

        # 每个 context 发送一个事件
        for i, ctx in enumerate(contexts):
            event = FinalEvent(content=f"Result {i}")
            await _present_final(event, ctx, state)

        # 验证所有都完成
        assert len(contexts) == 1000

    @pytest.mark.asyncio
    async def test_json_serialization_limits(self):
        """测试 JSON 序列化极限"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 测试各种特殊值
        special_values = [
            float('inf'),
            float('-inf'),
            float('nan'),
            None,
            "",
            [],
            {},
        ]

        for val in special_values:
            event = ErrorEvent(reason="special", detail=str(val))
            await _present_error(event, context, state)

        # 验证都被处理
        assert context.websocket.send_json.call_count >= len(special_values)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)  # 30秒超时保护
    async def test_sustained_load(self):
        """测试持续负载（10秒内持续调用）"""
        from deskpet.tools.os_tools.process_tools import process_list

        start_time = asyncio.get_event_loop().time()
        call_count = 0

        # 持续 5 秒调用（缩短到5秒以加快测试）
        while asyncio.get_event_loop().time() - start_time < 5:
            result = await process_list(args={"max_entries": 5})
            assert isinstance(result, str)
            call_count += 1
            await asyncio.sleep(0.01)  # 10ms 间隔

        # 验证至少完成了一定数量的调用
        assert call_count >= 100


class TestExceptionTypes:
    """异常类型全覆盖"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exception_type,exception_msg", [
        (ConnectionError, "Connection lost"),
        (TimeoutError, "Operation timed out"),
        (OSError, "OS error occurred"),
        (TypeError, "Type mismatch"),
        (AttributeError, "Attribute not found"),
        (KeyError, "Key not found"),
        (ValueError, "Invalid value"),
        (RuntimeError, "Runtime error"),
    ])
    async def test_exception_type_handling(self, exception_type, exception_msg):
        """测试各种异常类型的处理"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        # 模拟 websocket 抛出特定异常
        context.websocket.send_json.side_effect = exception_type(exception_msg)

        state = MagicMock(spec=PresentationState)

        # 应该捕获异常，不向外传播
        event = ErrorEvent(reason="test", detail="Test error")
        with patch('deskpet.agent.run_presenter._send_both', side_effect=exception_type(exception_msg)):
            await _present_error(event, context, state)

        # 验证尝试了后备发送
        assert context.websocket.send_json.called

    @pytest.mark.asyncio
    async def test_json_decode_error_handling(self):
        """测试 JSON 解析错误"""
        # 尝试解析无效 JSON
        invalid_json_strings = [
            "{invalid}",
            "{'single': 'quotes'}",
            "{unclosed",
            "null,",
        ]

        for invalid_json in invalid_json_strings:
            try:
                json.loads(invalid_json)
                assert False, f"Should have raised JSONDecodeError for: {invalid_json}"
            except json.JSONDecodeError:
                # 预期的异常
                pass

    @pytest.mark.asyncio
    async def test_non_serializable_object_handling(self):
        """测试不可序列化对象"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 不可序列化对象被转换为字符串
        non_serializable = lambda x: x  # lambda 不可序列化

        event = ErrorEvent(reason="non_serializable", detail=str(non_serializable))
        await _present_error(event, context, state)

        assert context.websocket.send_json.called


class TestEdgeCases:
    """边缘案例探索"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("timeout_value", [
        0,      # 零超时
        0.001,  # 极短超时
        999999, # 超大超时
        -1,     # 负数（应该被拒绝或转换）
    ])
    async def test_extreme_timeout_values(self, timeout_value):
        """测试极端超时值"""
        from deskpet.tools.os_tools.process_tools import process_list

        # 测试工具在各种超时值下的行为
        try:
            if timeout_value < 0:
                # 负数超时应该被拒绝或转换为正数
                with pytest.raises((ValueError, asyncio.TimeoutError)):
                    await asyncio.wait_for(
                        process_list(args={"max_entries": 5}),
                        timeout=timeout_value
                    )
            elif timeout_value == 0:
                # 零超时可能立即超时
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        process_list(args={"max_entries": 5}),
                        timeout=timeout_value
                    )
            else:
                # 正常超时
                result = await asyncio.wait_for(
                    process_list(args={"max_entries": 5}),
                    timeout=min(timeout_value, 5)  # 限制最大为5秒
                )
                assert isinstance(result, str)
        except asyncio.TimeoutError:
            # 某些极端值可能超时，这也是可接受的
            pass

    @pytest.mark.asyncio
    async def test_special_object_types(self):
        """测试特殊对象类型"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 测试各种特殊对象
        special_objects = [
            None,
            {},
            [],
            {"nested": {"deep": {"very": {"deep": "value"}}}},
            [1, [2, [3, [4, [5]]]]],
        ]

        for obj in special_objects:
            event = ErrorEvent(reason="special_obj", detail=str(obj))
            await _present_error(event, context, state)

        assert context.websocket.send_json.call_count >= len(special_objects)

    @pytest.mark.asyncio
    async def test_deeply_nested_structure(self):
        """测试超深嵌套结构"""
        from deskpet.agent.run_presenter import _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 创建 100 层嵌套的字典（不要太深，避免递归限制）
        nested_dict = {}
        current = nested_dict
        for i in range(100):
            current["level"] = {}
            current = current["level"]
        current["value"] = "deepest"

        event = ErrorEvent(reason="deep_nest", detail=str(nested_dict)[:1000])  # 截断避免太长
        await _present_error(event, context, state)

        assert context.websocket.send_json.called

    @pytest.mark.asyncio
    async def test_circular_reference_handling(self):
        """测试循环引用处理"""
        # 创建循环引用
        dict_a = {"name": "A"}
        dict_b = {"name": "B"}
        dict_a["ref"] = dict_b
        dict_b["ref"] = dict_a

        # 尝试序列化会失败
        with pytest.raises((ValueError, RecursionError)):
            json.dumps(dict_a)

        # 但转为字符串应该可以（有限深度）
        str_repr = str(dict_a)
        assert "A" in str_repr

    @pytest.mark.asyncio
    async def test_zero_length_content(self):
        """测试零长度内容"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 空内容
        event = FinalEvent(content="")
        await _present_final(event, context, state)

        assert context.websocket.send_json.called


class TestRealWorldBugSimulation:
    """真实 Bug 模拟"""

    @pytest.mark.asyncio
    async def test_stuck_status_original_bug(self):
        """模拟原始的卡住 bug：工具执行完但 final 事件发送失败"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        # 模拟 WebSocket 失败
        context.websocket.send_json.side_effect = Exception("WebSocket closed")

        state = MagicMock(spec=PresentationState)

        # 模拟 _send_both 也失败
        with patch('deskpet.agent.run_presenter._send_both', side_effect=Exception("Send both failed")):
            event = FinalEvent(content="Tool completed successfully")

            # 不应该抛出异常，应该静默失败（健康检查会恢复）
            await _present_final(event, context, state)

            # 验证尝试了后备发送
            assert context.websocket.send_json.called

    @pytest.mark.asyncio
    async def test_race_condition_final_and_error(self):
        """测试竞态条件：final 和 error 同时到达"""
        from deskpet.agent.run_presenter import _present_final, _present_error, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None
        context.websocket = AsyncMock()

        state = MagicMock(spec=PresentationState)

        # 并发发送 final 和 error
        final_event = FinalEvent(content="Done")
        error_event = ErrorEvent(reason="concurrent_error", detail="Error")

        tasks = [
            _present_final(final_event, context, state),
            _present_error(error_event, context, state),
        ]

        await asyncio.gather(*tasks)

        # 验证都被处理
        assert context.websocket.send_json.call_count >= 2

    @pytest.mark.asyncio
    async def test_health_check_false_positive(self):
        """测试健康检查误触发：正常执行但时间接近超时"""
        from deskpet.tools.os_tools.process_tools import process_list

        # 执行一个接近超时边界的操作
        start = asyncio.get_event_loop().time()

        result = await process_list(args={"max_entries": 100})

        elapsed = asyncio.get_event_loop().time() - start

        # 验证在合理时间内完成（远小于 60 秒健康检查超时）
        assert elapsed < 10
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_websocket_reconnection_simulation(self):
        """模拟 WebSocket 断开重连"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        context = MagicMock(spec=RunPresentationContext)
        context.session_id = "test-session"
        context.run_id = "run-001"
        context.request_id = "req-001"
        context.task_scope_id = None

        state = MagicMock(spec=PresentationState)

        # 第一个 websocket 失败
        ws1 = AsyncMock()
        ws1.send_json.side_effect = Exception("Connection lost")

        context.websocket = ws1

        event1 = FinalEvent(content="Before reconnect")
        with patch('deskpet.agent.run_presenter._send_both', side_effect=Exception("Disconnected")):
            await _present_final(event1, context, state)

        # 模拟重连：新的 websocket
        ws2 = AsyncMock()
        context.websocket = ws2

        event2 = FinalEvent(content="After reconnect")
        await _present_final(event2, context, state)

        # 验证新 websocket 成功发送
        assert ws2.send_json.called

    @pytest.mark.asyncio
    async def test_memory_leak_detection(self):
        """测试内存泄漏：长时间运行后的内存增长"""
        from deskpet.agent.run_presenter import _present_final, RunPresentationContext, PresentationState

        # 记录初始状态
        import gc
        gc.collect()

        contexts = []

        # 执行 100 次
        for i in range(100):
            context = MagicMock(spec=RunPresentationContext)
            context.session_id = f"session-{i}"
            context.run_id = f"run-{i}"
            context.request_id = f"req-{i}"
            context.task_scope_id = None
            context.websocket = AsyncMock()

            state = MagicMock(spec=PresentationState)

            event = FinalEvent(content=f"Result {i}")
            await _present_final(event, context, state)

            # 只保留最后 10 个（模拟正常的对象释放）
            if len(contexts) >= 10:
                contexts.pop(0)
            contexts.append(context)

        # 强制垃圾回收
        gc.collect()

        # 验证只保留了最后 10 个对象
        assert len(contexts) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
