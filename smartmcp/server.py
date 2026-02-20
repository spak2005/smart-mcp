"""smartmcp MCP server — faces the client, proxies to upstream servers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import types
from mcp.server import Server as MCPServer
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server

from smartmcp.config import SmartMCPConfig
from smartmcp.embedding import EmbeddingIndex
from smartmcp.upstream import UpstreamManager

logger = logging.getLogger(__name__)


class SmartMCPState:
    """Holds runtime state shared across handlers."""

    def __init__(
        self,
        upstream: UpstreamManager,
        index: EmbeddingIndex,
        all_tools: list[tuple[str, types.Tool]],
        config: SmartMCPConfig,
    ) -> None:
        self.upstream = upstream
        self.index = index
        self.all_tools = all_tools
        self.config = config


@asynccontextmanager
async def smartmcp_lifespan(server: MCPServer, config: SmartMCPConfig) -> AsyncIterator[SmartMCPState]:
    """Startup: connect upstream servers, collect tools, build index."""
    upstream = UpstreamManager()
    await upstream.connect_all(config)

    raw_tools = await upstream.collect_tools()
    tools_only = [tool for _, tool in raw_tools]

    index = EmbeddingIndex(config.embedding_model)
    index.build_index(tools_only)

    logger.info("smartmcp ready — %d tools indexed", len(tools_only))

    try:
        yield SmartMCPState(
            upstream=upstream,
            index=index,
            all_tools=raw_tools,
            config=config,
        )
    finally:
        await upstream.close()


async def handle_list_tools(
    ctx: Any, params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[])


async def handle_call_tool(
    ctx: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"Unknown tool: {params.name}")]
    )


def run_server(config: SmartMCPConfig) -> None:
    """Create and run the smartmcp MCP server over stdio."""

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[SmartMCPState]:
        async with smartmcp_lifespan(server, config) as state:
            yield state

    app = MCPServer(
        "smartmcp",
        lifespan=lifespan,
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            init_options = app.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True),
            )
            await app.run(read_stream, write_stream, init_options)

    anyio.run(_run)
