"""Disposable stdio MCP server used by the dynamic lifecycle spike."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP


server = FastMCP("deskpet-capability-spike")


@server.tool()
def echo(value: str) -> dict[str, str]:
    """Return one value."""
    return {"echo": value}


@server.tool()
def crash() -> None:
    """Terminate the fixture server to exercise client crash handling."""
    os._exit(17)


if __name__ == "__main__":
    server.run(transport="stdio")
