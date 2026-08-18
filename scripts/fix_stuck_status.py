#!/usr/bin/env python3
"""
Fix stuck "工作中" status by manually resetting frontend state via WebSocket.

Usage: python3 scripts/fix_stuck_status.py
"""

import asyncio
import websockets
import json
import sys

async def fix_status():
    uri = "ws://127.0.0.1:8100/ws/control"

    try:
        async with websockets.connect(uri) as websocket:
            print("✅ Connected to backend WebSocket")

            # Send a reset message to clear stuck status
            # This simulates a chat_v2_final event to reset the session
            reset_msg = {
                "type": "chat_v2_final",
                "payload": {
                    "text": "",  # Empty text
                    "session_id": "default",  # Assuming default session
                    "run_id": "",
                    "task_scope_id": ""
                }
            }

            await websocket.send(json.dumps(reset_msg))
            print("✅ Sent status reset message")

            # Wait a moment for response
            await asyncio.sleep(1)

            print("✅ Status should now be reset to 'idle'")
            print("📝 Please check the UI - it should now show '✓ 空闲'")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("🔧 Fixing stuck '工作中' status...")
    asyncio.run(fix_status())
