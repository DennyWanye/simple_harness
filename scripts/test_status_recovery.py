#!/usr/bin/env python3
"""
自动化测试：状态恢复机制
测试修复后的错误处理和健康检查是否正常工作
"""
import asyncio
import json
import time
import websockets
from typing import Dict, Any


class StatusRecoveryTester:
    def __init__(self, backend_url: str = "ws://localhost:8100/ws/control"):
        self.backend_url = backend_url
        self.ws = None
        self.session_id = "test_session_status_recovery"
        self.messages_received = []
        self.current_status = "unknown"

    async def connect(self):
        """连接到后端 WebSocket"""
        print(f"🔌 连接到后端: {self.backend_url}")
        self.ws = await websockets.connect(self.backend_url)
        print("✅ 连接成功")

    async def send_message(self, text: str):
        """发送消息到后端"""
        message = {
            "type": "chat_v2",
            "payload": {
                "session_id": self.session_id,
                "text": text,
                "request_id": f"test_{int(time.time() * 1000)}",
            }
        }
        print(f"📤 发送消息: {text[:50]}...")
        await self.ws.send(json.dumps(message))

    async def receive_messages(self, timeout: float = 70.0):
        """接收消息，带超时"""
        start_time = time.time()
        final_received = False

        while time.time() - start_time < timeout:
            try:
                message = await asyncio.wait_for(
                    self.ws.recv(),
                    timeout=min(5.0, timeout - (time.time() - start_time))
                )
                data = json.loads(message)
                msg_type = data.get("type", "unknown")

                # 记录收到的消息
                self.messages_received.append(data)

                # 追踪状态变化
                if msg_type in ["chat_v2_run_reserved", "chat_v2_run_started"]:
                    self.current_status = "thinking"
                    print(f"📊 状态: {self.current_status}")
                elif msg_type == "chat_v2_tool_call":
                    self.current_status = "running"
                    print(f"📊 状态: {self.current_status} (工具执行中)")
                elif msg_type in ["chat_v2_final", "chat_v2_error"]:
                    self.current_status = "idle"
                    print(f"📊 状态: {self.current_status} (收到终止事件: {msg_type})")
                    final_received = True
                    break

                # 打印重要消息
                if msg_type in ["chat_v2_final", "chat_v2_error", "chat_v2_tool_call", "chat_v2_tool_result"]:
                    print(f"📥 收到: {msg_type}")
                    if msg_type == "chat_v2_error":
                        payload = data.get("payload", {})
                        print(f"   ⚠️ 错误: {payload.get('reason')}: {payload.get('detail')}")

            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                if not final_received:
                    print(f"⏰ 超时: {elapsed:.1f}秒后未收到终止事件")
                    print(f"   当前状态: {self.current_status}")
                break
            except Exception as e:
                print(f"❌ 接收消息出错: {e}")
                break

        return final_received

    async def close(self):
        """关闭连接"""
        if self.ws:
            await self.ws.close()
            print("🔌 连接已关闭")


async def test_case_1_tool_execution_with_empty_result():
    """测试用例 #1: 工具执行返回空结果"""
    print("\n" + "="*60)
    print("🧪 测试用例 #1: 工具执行返回空结果")
    print("="*60)

    tester = StatusRecoveryTester()

    try:
        await tester.connect()

        # 发送会触发 process_list 但返回空结果的查询
        await tester.send_message("请列出进程信息，只显示名称包含 'xxxxxxnonexistent123456' 的进程")

        # 等待响应
        print("\n⏳ 等待响应（最多 70 秒）...")
        final_received = await tester.receive_messages(timeout=70.0)

        # 验证结果
        print("\n" + "-"*60)
        print("📊 测试结果:")
        print(f"   终止事件收到: {'✅ 是' if final_received else '❌ 否'}")
        print(f"   最终状态: {tester.current_status}")
        print(f"   收到消息数: {len(tester.messages_received)}")

        if final_received and tester.current_status == "idle":
            print("\n✅ 测试通过: 状态正确恢复到 idle")
            return True
        else:
            print("\n❌ 测试失败: 状态未正确恢复")
            if not final_received:
                print("   原因: 未收到 chat_v2_final 或 chat_v2_error 事件")
            return False

    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await tester.close()


async def test_case_2_multiple_tool_calls():
    """测试用例 #2: 多轮工具调用"""
    print("\n" + "="*60)
    print("🧪 测试用例 #2: 连续 3 次工具调用")
    print("="*60)

    queries = [
        "列出所有包含 python 的进程，只显示前 3 个",
        "列出所有包含 node 的进程，只显示前 3 个",
        "列出所有包含 zsh 的进程，只显示前 3 个",
    ]

    all_passed = True

    for i, query in enumerate(queries, 1):
        print(f"\n--- 第 {i}/{len(queries)} 轮 ---")
        tester = StatusRecoveryTester()

        try:
            await tester.connect()
            await tester.send_message(query)

            print(f"⏳ 等待响应...")
            final_received = await tester.receive_messages(timeout=70.0)

            if final_received and tester.current_status == "idle":
                print(f"✅ 第 {i} 轮通过")
            else:
                print(f"❌ 第 {i} 轮失败: 状态 = {tester.current_status}")
                all_passed = False

        except Exception as e:
            print(f"❌ 第 {i} 轮异常: {e}")
            all_passed = False
        finally:
            await tester.close()

        # 轮次间休息 2 秒
        if i < len(queries):
            await asyncio.sleep(2)

    print("\n" + "-"*60)
    if all_passed:
        print("✅ 测试用例 #2 通过: 所有轮次状态正确恢复")
    else:
        print("❌ 测试用例 #2 失败: 存在状态未恢复的轮次")
    print("-"*60)

    return all_passed


async def test_case_5_timeout_check():
    """测试用例 #5: 健康检查超时机制"""
    print("\n" + "="*60)
    print("🧪 测试用例 #5: 健康检查超时（等待 70 秒）")
    print("="*60)
    print("注意: 如果工具正常完成，健康检查不会触发")
    print("      只有当真正超时时，健康检查才会在 60 秒后强制重置")

    tester = StatusRecoveryTester()

    try:
        await tester.connect()

        # 发送正常查询
        await tester.send_message("请列出所有进程，只显示前 10 个")

        # 等待 70 秒
        print("\n⏳ 等待响应（最多 70 秒）...")
        final_received = await tester.receive_messages(timeout=70.0)

        print("\n" + "-"*60)
        print("📊 测试结果:")
        print(f"   终止事件收到: {'✅ 是' if final_received else '❌ 否'}")
        print(f"   最终状态: {tester.current_status}")

        if final_received and tester.current_status == "idle":
            print("\n✅ 测试通过: 状态正确恢复")
            print("   (正常完成或健康检查触发)")
            return True
        else:
            print("\n❌ 测试失败: 70 秒后状态仍未恢复")
            return False

    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        return False
    finally:
        await tester.close()


async def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("🚀 开始自动化测试 - 状态恢复机制")
    print("="*60)
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    results = {}

    # P0 测试用例
    print("\n\n" + "🎯 P0 优先级测试".center(60, "="))

    results["test_case_1"] = await test_case_1_tool_execution_with_empty_result()
    await asyncio.sleep(3)

    results["test_case_2"] = await test_case_2_multiple_tool_calls()
    await asyncio.sleep(3)

    results["test_case_5"] = await test_case_5_timeout_check()

    # 汇总结果
    print("\n\n" + "="*60)
    print("📊 测试汇总")
    print("="*60)

    total = len(results)
    passed = sum(1 for r in results.values() if r)

    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name}: {status}")

    print("-"*60)
    print(f"总计: {passed}/{total} 通过")

    if passed == total:
        print("\n🎉 所有测试通过！")
    else:
        print(f"\n⚠️ {total - passed} 个测试失败，需要修复")

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
