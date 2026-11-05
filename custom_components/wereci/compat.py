"""Bridge Home Assistant's library generations.

HA 2026.11 moved core to httpx2 and mcp 2.x; earlier releases ship httpx and
mcp 1.x. Import these names from here, never from httpx or mcp directly.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import httpx_client

# The clients HA hands out raise from whichever library its helper is built on.
if hasattr(httpx_client, "httpx2"):
    import httpx2 as httpx  # noqa: F401
else:
    import httpx  # type: ignore[no-redef]  # noqa: F401

try:
    from mcp import MCPError
except ImportError:  # mcp 1.x
    from mcp import McpError as MCPError  # type: ignore[attr-defined,no-redef]

__all__ = ["MCPError", "httpx", "tool_input_schema"]


def tool_input_schema(tool: Any) -> dict[str, Any]:
    """A listed tool's input schema (snake_case attribute since mcp 2.x)."""
    if hasattr(tool, "input_schema"):
        return tool.input_schema
    return tool.inputSchema
