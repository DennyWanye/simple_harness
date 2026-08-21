# Test to reproduce process_list RuntimeError issue
import asyncio
import json

import pytest

from deskpet.tools.os_tools.process_tools import process_list


@pytest.mark.asyncio
async def test_process_list_basic():
    """Test that process_list doesn't throw RuntimeError on basic execution."""
    result = await process_list(
        args={"query": "", "max_entries": 10},
        task_id="test_task_001",
    )

    # Should return success envelope
    payload = json.loads(result)
    assert payload["ok"] is True
    assert len(payload["processes"]) <= 10


@pytest.mark.asyncio
async def test_process_list_with_query():
    """Test that process_list works with a query filter."""
    result = await process_list(
        args={"query": "python", "max_entries": 5},
        task_id="test_task_002",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert all("python" in item["name"].casefold() for item in payload["processes"])


@pytest.mark.asyncio
async def test_process_list_invalid_args():
    """Test that process_list handles invalid arguments gracefully."""
    result = await process_list(
        args={"max_entries": "not_an_integer"},  # Invalid type
        task_id="test_task_003",
    )

    # Should return error envelope, not throw exception
    payload = json.loads(result)
    assert payload == {
        "ok": False,
        "error": {
            "code": "invalid_arguments",
            "message": "max_entries must be an integer",
        },
    }


if __name__ == "__main__":
    asyncio.run(test_process_list_basic())
    asyncio.run(test_process_list_with_query())
    asyncio.run(test_process_list_invalid_args())
