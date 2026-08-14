"""Tools adapter - tool execution delegation bridge.

Implements SDK Tool protocol by bridging to product's tool registry and handlers.
Tool implementations remain product-owned (web, PPT, OCR, MCP, etc).
"""

from __future__ import annotations


# TODO T6.1: Implement SDK Tool protocol
# from simple_harness.tools import ToolPort, ToolExecutionContext


class ProductToolsAdapter:
    """Adapter between product tool handlers and SDK Tool protocol.

    Responsibilities:
    - Register product tools with SDK (web, PPT, OCR, MCP, shell, etc)
    - Delegate execution to product handlers
    - Return tool results in SDK format
    - Keep handler implementations product-owned
    """

    def __init__(self):
        # TODO T6.1: Initialize with product tool registry
        # - web tools (browser, search)
        # - office tools (PPT, Excel)
        # - media tools (OCR, image)
        # - MCP tools (marketplace)
        # - system tools (shell, files)
        pass


__all__ = ("ProductToolsAdapter",)
