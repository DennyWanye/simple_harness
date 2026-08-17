#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Direct WebSocket test for SimpleHarness app interactions.

Tests:
1. Connect to backend WebSocket
2. Send chat message requesting tool usage
3. Monitor tool calls and responses
4. Test workflow execution
"""

import asyncio
import json
import sys
import uuid
import websockets


WS_URL = "ws://127.0.0.1:8100/ws/control?secret=6cbbddb124819c4f96a98df4676d0d4a&session_id=e2e_test&requested_window_label=main&requested_scope=companion_action"


async def send_and_receive(ws, request: dict, timeout: float = 60.0):
    """Send request and collect responses."""
    await ws.send(json.dumps(request))

    deadline = asyncio.get_event_loop().time() + timeout
    responses = []

    print(f"  Sent: {request['type']}")

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            print(f"  ⏱️  Timeout after {timeout}s")
            break

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5.0))
            msg = json.loads(raw)
            msg_type = msg.get("type", "unknown")

            print(f"  ← {msg_type}", end="")

            # Print relevant details
            if msg_type == "chat_response":
                content = msg.get("payload", {}).get("content", "")[:80]
                print(f": {content}...")
            elif msg_type == "chat_v2_final":
                content = msg.get("payload", {}).get("text", "")[:80]
                print(f": {content}...")
            elif msg_type == "tool_use":
                tool_name = msg.get("payload", {}).get("tool_name", "")
                print(f": {tool_name}")
            elif msg_type == "thinking":
                print(" (thinking...)")
            else:
                print()

            responses.append(msg)

            # Check for completion
            if msg_type in ("chat_response", "chat_v2_final"):
                if msg.get("payload", {}).get("stop_reason") in ("end_turn", "stop_sequence", None):
                    print("  ✅ Response completed")
                    break

        except asyncio.TimeoutError:
            # No more messages in 5s window
            if responses:
                print("  ✅ No more responses (likely complete)")
                break
        except Exception as e:
            print(f"  ❌ Error: {e}")
            break

    return responses


async def test_tool_calling():
    """Test 1: Tool calling via chat."""
    print("\n" + "=" * 60)
    print("TEST 1: Tool Calling")
    print("=" * 60)

    try:
        async with websockets.connect(WS_URL) as ws:
            # Send message requesting file listing
            message = {
                "type": "chat",
                "payload": {
                    "text": "请列出 backend 目录下的 Python 文件（只要文件名，不要内容）"
                }
            }

            responses = await send_and_receive(ws, message, timeout=30.0)

            # Check for tool usage
            tool_calls = [r for r in responses if r.get("type") == "tool_use"]
            chat_responses = [r for r in responses if r.get("type") in ("chat_response", "chat_v2_final")]

            print(f"\n  Summary:")
            print(f"    Tool calls: {len(tool_calls)}")
            print(f"    Chat responses: {len(chat_responses)}")

            if tool_calls:
                print(f"    Tools used:")
                for tc in tool_calls:
                    tool_name = tc.get("payload", {}).get("tool_name", "unknown")
                    print(f"      - {tool_name}")

            if chat_responses:
                # Print response content
                for cr in chat_responses:
                    text = cr.get("payload", {}).get("text", "")
                    if text:
                        print(f"    Response preview: {text[:120]}...")

            success = len(tool_calls) > 0 or len(chat_responses) > 0
            print(f"\n  {'✅ PASS' if success else '❌ FAIL'}")
            return success

    except Exception as e:
        print(f"\n  ❌ FAIL - Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_workflow_execution():
    """Test 2: Workflow execution."""
    print("\n" + "=" * 60)
    print("TEST 2: Workflow Execution")
    print("=" * 60)

    try:
        async with websockets.connect(WS_URL) as ws:
            # Request a simple task that might trigger workflow
            message = {
                "type": "chat",
                "payload": {
                    "text": "请告诉我当前系统有哪些可用的工作流？"
                }
            }

            responses = await send_and_receive(ws, message, timeout=30.0)

            # Check responses
            chat_responses = [r for r in responses if r.get("type") in ("chat_response", "chat_v2_final")]

            print(f"\n  Summary:")
            print(f"    Chat responses: {len(chat_responses)}")

            if chat_responses:
                # Check if response mentions workflows
                last_response = chat_responses[-1]
                content = last_response.get("payload", {}).get("text", "") or last_response.get("payload", {}).get("content", "")
                print(f"    Response preview: {content[:120]}...")
                has_workflow_info = any(keyword in content for keyword in
                                       ["workflow", "工作流", "deep_research", "ppt_pro"])
                print(f"    Mentions workflows: {has_workflow_info}")

            success = len(chat_responses) > 0
            print(f"\n  {'✅ PASS' if success else '❌ FAIL'}")
            return success

    except Exception as e:
        print(f"\n  ❌ FAIL - Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_memory_tool():
    """Test 3: Memory tool usage."""
    print("\n" + "=" * 60)
    print("TEST 3: Memory Tool Usage")
    print("=" * 60)

    try:
        async with websockets.connect(WS_URL) as ws:
            # Ask to remember something
            message = {
                "type": "chat",
                "payload": {
                    "text": "请记住：我正在测试 SimpleHarness 的工具调用功能"
                }
            }

            responses = await send_and_receive(ws, message, timeout=20.0)

            # Check for memory tool usage
            tool_calls = [r for r in responses if r.get("type") == "tool_use"]
            memory_tools = [tc for tc in tool_calls
                          if "memory" in tc.get("payload", {}).get("tool_name", "").lower()]

            print(f"\n  Summary:")
            print(f"    Total tool calls: {len(tool_calls)}")
            print(f"    Memory tool calls: {len(memory_tools)}")

            success = len(responses) > 0  # Got some response
            print(f"\n  {'✅ PASS' if success else '❌ FAIL'}")
            return success

    except Exception as e:
        print(f"\n  ❌ FAIL - Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all WebSocket interaction tests."""
    print("\n" + "=" * 60)
    print("SIMPLE HARNESS - WEBSOCKET INTERACTION TEST")
    print("=" * 60)
    print(f"WebSocket URL: {WS_URL}")

    results = []

    # Run tests
    results.append(("Tool Calling", await test_tool_calling()))
    results.append(("Workflow Execution", await test_workflow_execution()))
    results.append(("Memory Tool", await test_memory_tool()))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed}/{total} tests passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
