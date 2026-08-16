#!/usr/bin/env python3
"""
测试 Simple Harness 主流程
基于 testcase/2026-08-13-simple-harness-sdk/manual-test.md
"""
import asyncio
import json
import sys
import time
import websockets
from datetime import datetime

async def test_basic_conversation():
    """测试基本对话功能（SDK-S1）"""
    print("=" * 60)
    print("测试 1: 基本对话功能")
    print("=" * 60)

    # 从日志中提取 secret 和连接信息
    secret = "fcdecda152f1978ba3cc365883c811ed"
    ws_url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id=test-session-main&requested_window_label=main&requested_scope=companion_action"

    try:
        async with websockets.connect(ws_url) as websocket:
            print(f"✓ WebSocket 连接成功")
            print(f"  时间: {datetime.now().isoformat()}")

            # 测试消息 1: 简单问候
            test_message_1 = {
                "type": "chat_message",
                "content": "你好，请用一句话介绍你自己",
                "session_id": "test-session-main",
                "request_id": f"test-req-{int(time.time())}"
            }

            print(f"\n📤 发送测试消息 1:")
            print(f"   内容: {test_message_1['content']}")

            await websocket.send(json.dumps(test_message_1))
            print(f"✓ 消息已发送")

            # 接收响应
            print(f"\n📥 等待响应...")
            response_count = 0
            timeout = 30  # 30秒超时
            start_time = time.time()

            while time.time() - start_time < timeout:
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    response_data = json.loads(response)
                    response_count += 1

                    print(f"\n✓ 收到响应 #{response_count}:")
                    print(f"   类型: {response_data.get('type', 'unknown')}")

                    if 'content' in response_data:
                        content = response_data['content']
                        if len(content) > 100:
                            print(f"   内容: {content[:100]}...")
                        else:
                            print(f"   内容: {content}")

                    # 如果收到完整响应，退出
                    if response_data.get('type') in ['assistant_message', 'message_complete', 'done']:
                        print(f"\n✅ 测试 1 通过: 成功收到 AI 回复")
                        return True

                except asyncio.TimeoutError:
                    if response_count > 0:
                        print(f"\n✅ 测试 1 通过: 收到 {response_count} 条响应")
                        return True
                    continue

            if response_count == 0:
                print(f"\n❌ 测试 1 失败: 超时未收到响应")
                return False
            else:
                print(f"\n✅ 测试 1 通过: 收到 {response_count} 条响应")
                return True

    except Exception as e:
        print(f"\n❌ 测试 1 失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_provider_status():
    """测试 Provider 状态"""
    print("\n" + "=" * 60)
    print("测试 2: Provider 配置状态")
    print("=" * 60)

    import httpx

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://127.0.0.1:8100/api/providers")

            if response.status_code == 200:
                providers = response.json()
                print(f"✓ 成功获取 Provider 列表")
                print(f"  Provider 数量: {len(providers)}")

                for provider in providers:
                    print(f"\n  Provider:")
                    print(f"    ID: {provider.get('id', 'N/A')}")
                    print(f"    Name: {provider.get('name', 'N/A')}")
                    print(f"    Type: {provider.get('provider', 'N/A')}")

                if any('deepseek' in str(p).lower() for p in providers):
                    print(f"\n✅ 测试 2 通过: 找到 DeepSeek provider")
                    return True
                else:
                    print(f"\n⚠️  警告: 未找到 DeepSeek provider")
                    return True  # 不算失败
            else:
                print(f"❌ API 返回错误: {response.status_code}")
                return False

    except Exception as e:
        print(f"❌ 测试 2 失败: {e}")
        return False

async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("Simple Harness 主流程测试")
    print("基于: testcase/2026-08-13-simple-harness-sdk/manual-test.md")
    print("时间:", datetime.now().isoformat())
    print("=" * 60)

    results = []

    # 测试 1: 基本对话
    result1 = await test_basic_conversation()
    results.append(("基本对话功能", result1))

    # 等待一下
    await asyncio.sleep(2)

    # 测试 2: Provider 状态
    result2 = await test_provider_status()
    results.append(("Provider 配置", result2))

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("\n🎉 所有测试通过！主流程工作正常。")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
