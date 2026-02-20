"""Upstream MCP server connection management."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp import types
from mcp.client.stdio import StdioServerParameters, stdio_client

from smartmcp.config import SmartMCPConfig

logger = logging.getLogger(__name__)


class UpstreamManager:
    """Manages connections to upstream MCP servers."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}

    async def connect_all(self, config: SmartMCPConfig) -> None:
        """Spawn and connect to all configured upstream MCP servers."""
        for name, server_cfg in config.servers.items():
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

    async def collect_tools(self) -> list[tuple[str, types.Tool]]:
        """Fetch tool schemas from all connected upstream servers.

        Returns a list of (server_name, tool) tuples.
        """
        all_tools: list[tuple[str, types.Tool]] = []
        for name, session in self.sessions.items():
            result = await session.list_tools()
            for tool in result.tools:
                all_tools.append((name, tool))
            logger.info("Collected %d tool(s) from server: %s", len(result.tools), name)
        return all_tools

    async def close(self) -> None:
        """Shut down all upstream connections."""
        await self._stack.aclose()
