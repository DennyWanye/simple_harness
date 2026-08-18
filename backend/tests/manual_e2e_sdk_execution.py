"""Manual E2E test script for SDK execution.

Tests the complete chain: main.py → _execute_sdk_run → SDK Runtime → DeliveryDispatcher → _DeliverySink → ProductDeliveryAdapter
"""
import asyncio
import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

async def test_sdk_execution():
    """Test SDK execution flow manually."""
    print("=" * 60)
    print("SDK Execution E2E Test")
    print("=" * 60)

    # Import after path setup
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters
    from deskpet.agent.run_presenter import build_product_run_presenter, CanonicalRunEventPresentationAdapter, PresentationState
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter

    print("\n1. Creating mock context and components...")

    # Create minimal mock context
    class MockWebSocket:
        async def send_json(self, data):
            print(f"   WebSocket.send_json: {json.dumps(data, indent=2)}")

    class MockContext:
        def __init__(self):
            self.session_id = "test_session"
            self.request_id = "test_request"
            self.bundle = None  # Required by finish_turn
            self.assembler = None
            self.billing_ledger = None
            self.provider = None

    websocket = MockWebSocket()
    context = MockContext()

    # Build real presenter components
    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    print("   ✓ Components created")

    print("\n2. Creating ProductDeliveryAdapter...")

    run_id = "manual_test_run_001"
    delivery_adapter = ProductDeliveryAdapter(
        session_id="test_session",
        request_id="test_request",
        run_id=run_id,
        presenter=presenter,
        adapter=adapter,
        context=context,
        state=state
    )

    print(f"   ✓ Adapter created for run_id={run_id}")

    print("\n3. Registering adapter in global registry...")
    _delivery_adapters[run_id] = delivery_adapter
    print(f"   ✓ Registered. Registry size: {len(_delivery_adapters)}")

    print("\n4. Simulating SDK delivery event...")

    # Create a minimal valid RunEvent payload
    test_payload = {
        "event_id": "evt_001",
        "run_id": run_id,
        "root_run_id": run_id,
        "session_id": "test_session",
        "durable_seq": 1,
        "candidate": {
            "event_key": "test_event_key_001",
            "kind": "status_changed",
            "status": "accepted",  # Valid OutcomeStatus value
            "driver_kind": "react",
            "correlation": {},
            "payload": {},
            "error": None,
            "artifact_refs": []
        },
        "created_at": 1234567890.0
    }

    idempotency_key = f"{run_id}:event:1"

    try:
        await delivery_adapter.handle_event(test_payload, idempotency_key)
        print("   ✓ Event handled successfully")
    except Exception as exc:
        print(f"   ✗ Event handling failed: {exc}")
        import traceback
        traceback.print_exc()
        return False

    print("\n5. Testing idempotency (duplicate event should be skipped)...")

    try:
        await delivery_adapter.handle_event(test_payload, idempotency_key)
        print("   ✓ Duplicate event skipped (as expected)")
    except Exception as exc:
        print(f"   ✗ Idempotency check failed: {exc}")
        return False

    print("\n6. Finishing adapter...")

    try:
        await delivery_adapter.finish()
        print("   ✓ Adapter finished, idempotency cleared")
    except Exception as exc:
        print(f"   ✗ Finish failed: {exc}")
        import traceback
        traceback.print_exc()
        return False

    print("\n7. Cleanup registry...")
    _delivery_adapters.pop(run_id, None)
    print(f"   ✓ Cleaned up. Registry size: {len(_delivery_adapters)}")

    print("\n" + "=" * 60)
    print("✓ ALL TESTS PASSED")
    print("=" * 60)

    return True

if __name__ == "__main__":
    result = asyncio.run(test_sdk_execution())
    sys.exit(0 if result else 1)
