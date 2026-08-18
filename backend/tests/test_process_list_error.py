# Test to reproduce process_list RuntimeError issue
import pytest
import asyncio
from deskpet.tools.os_tools.process_tools import process_list


@pytest.mark.asyncio
async def test_process_list_basic():
    """Test that process_list doesn't throw RuntimeError on basic execution."""
    result = await process_list(
        args={"query": "", "max_entries": 10},
        task_id="test_task_001",
    )

    # Should return success envelope
    assert '"status":"success"' in result or '"status": "success"' in result
    print(f"✅ process_list executed successfully: {result[:200]}...")


@pytest.mark.asyncio
async def test_process_list_with_query():
    """Test that process_list works with a query filter."""
    result = await process_list(
        args={"query": "python", "max_entries": 5},
        task_id="test_task_002",
    )

    assert '"status":"success"' in result or '"status": "success"' in result
    print(f"✅ process_list with query executed: {result[:200]}...")


@pytest.mark.asyncio
async def test_process_list_invalid_args():
    """Test that process_list handles invalid arguments gracefully."""
    result = await process_list(
        args={"max_entries": "not_an_integer"},  # Invalid type
        task_id="test_task_003",
    )

    # Should return error envelope, not throw exception
    assert '"status"' in result
    print(f"✅ process_list handled invalid args: {result[:200]}...")


if __name__ == "__main__":
    asyncio.run(test_process_list_basic())
    asyncio.run(test_process_list_with_query())
    asyncio.run(test_process_list_invalid_args())
