"""Upstream MCP server connection management."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp import types
from mcp.client.stdio import StdioServerParameters, stdio_client

from smartmcp.config import SmartMCPConfig

logger = logging.getLogger(__name__)

SEPARATOR = "__"


def prefix_tool_name(server_name: str, tool_name: str) -> str:
    """Create a prefixed tool name: 'servername__toolname'."""
    return f"{server_name}{SEPARATOR}{tool_name}"


def parse_prefixed_name(prefixed: str) -> tuple[str, str]:
    """Split a prefixed tool name back into (server_name, tool_name)."""
    parts = prefixed.split(SEPARATOR, maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"Invalid prefixed tool name: {prefixed}")
    return parts[0], parts[1]


class UpstreamManager:
    """Manages connections to upstream MCP servers."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}

    async def connect_all(self, config: SmartMCPConfig) -> list[str]:
        """Spawn and connect to all configured upstream MCP servers.

        Returns a list of server names that failed to connect.
        """
        failed: list[str] = []
        for name, server_cfg in config.servers.items():
            try:
                params = StdioServerParameters(
                    command=server_cfg.command,
                    args=server_cfg.args,
                    env=server_cfg.env if server_cfg.env else None,
                )
                transport = await self._stack.enter_async_context(stdio_client(params))
                read_stream, write_stream = transport
                session = await self._stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                self.sessions[name] = session
                logger.info("Connected to upstream server: %s", name)
            except Exception as exc:
                logger.warning("Failed to connect to server '%s': %s", name, exc)
                failed.append(name)

        if not self.sessions:
            raise RuntimeError(
                "All upstream servers failed to connect. Cannot start smartmcp."
            )

        return failed

    async def collect_tools(self) -> list[tuple[str, types.Tool]]:
        """Fetch tool schemas from all connected upstream servers.

        Tool names are prefixed with 'servername__' to avoid collisions.
        Returns a list of (server_name, prefixed_tool) tuples.
        """
        all_tools: list[tuple[str, types.Tool]] = []
        for name, session in self.sessions.items():
            result = await session.list_tools()
            for tool in result.tools:
                prefixed = types.Tool(
                    name=prefix_tool_name(name, tool.name),
                    description=tool.description,
                    inputSchema=tool.inputSchema,
                )
                all_tools.append((name, prefixed))
            logger.info("Collected %d tool(s) from server: %s", len(result.tools), name)
        return all_tools

    async def close(self) -> None:
        """Shut down all upstream connections."""
        await self._stack.aclose()
