from __future__ import annotations

from deskpet.tools.os_tools.registration import register_os_tools
from deskpet.tools.registry import ToolRegistry


def test_new_os_primitives_are_registered_default_on() -> None:
    registry = ToolRegistry()
    register_os_tools(registry)
    expected = {
        "move_file",
        "process_list",
        "process_start",
        "process_wait",
        "process_stop",
        "app_discover",
        "app_launch",
        "download_file",
    }
    registered = {spec.name for spec in registry.catalog_snapshot().specs}
    assert expected.issubset(registered)
    assert registry.get("process_start").dangerous is True
    assert registry.get("move_file").concurrency_safe is False
    assert registry.get("download_file").permission_category == "network"
