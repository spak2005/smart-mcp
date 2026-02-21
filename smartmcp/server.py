"""smartmcp MCP server — faces the client, proxies to upstream servers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel.server import Server as MCPServer, NotificationOptions
from mcp.server.stdio import stdio_server

from smartmcp.config import SmartMCPConfig
from smartmcp.embedding import EmbeddingIndex
from smartmcp.upstream import UpstreamManager, parse_prefixed_name

logger = logging.getLogger(__name__)

SEARCH_TOOLS_NAME = "search_tools"

SEARCH_TOOLS_SCHEMA = types.Tool(
    name=SEARCH_TOOLS_NAME,
    description=(
        "Search for relevant tools across all connected MCP servers. "
        "Describe what you want to do and this will find the best matching tools."
    ),
    inputSchema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of what you want to do",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of tools to return (default: 3)",
            },
        },
    },
)


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
        self.active_tools: list[types.Tool] = []


def run_server(config: SmartMCPConfig) -> None:
    """Create and run the smartmcp MCP server over stdio."""

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[SmartMCPState]:
        upstream = UpstreamManager()
        failed = await upstream.connect_all(config)
        if failed:
            logger.warning(
                "Some servers failed to connect: %s. Continuing with %d server(s).",
                ", ".join(failed),
                len(upstream.sessions),
            )

        raw_tools = await upstream.collect_tools()
        tools_only = [tool for _, tool in raw_tools]

        index = EmbeddingIndex(config.embedding_model)
        index.build_index(tools_only)

        logger.info("smartmcp ready — %d tools indexed from %d server(s)", len(tools_only), len(upstream.sessions))

        try:
            yield SmartMCPState(
                upstream=upstream,
                index=index,
                all_tools=raw_tools,
                config=config,
            )
        finally:
            await upstream.close()

    app = MCPServer("smartmcp", lifespan=lifespan)

    @app.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        state: SmartMCPState = app.request_context.lifespan_context
        return [SEARCH_TOOLS_SCHEMA] + state.active_tools

    @app.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
        state: SmartMCPState = app.request_context.lifespan_context
        session = app.request_context.session

        if name == SEARCH_TOOLS_NAME:
            query = arguments.get("query", "")
            top_k = arguments.get("top_k", state.config.top_k)
            if not query:
                return [types.TextContent(type="text", text="Error: 'query' is required")]

            try:
                results = state.index.search(query, top_k=top_k)
            except Exception as exc:
                logger.error("Search failed for query '%s': %s", query, exc)
                return [types.TextContent(type="text", text=f"Search error: {exc}")]

            state.active_tools = [tool for tool, _ in results]
            logger.info("Active tools updated: %d tool(s)", len(state.active_tools))

            try:
                await session.send_tool_list_changed()
            except Exception as exc:
                logger.warning("Failed to send tool_list_changed notification: %s", exc)

            lines = [f"Found {len(results)} matching tool(s). They are now available to call:\n"]
            for tool, score in results:
                lines.append(f"- {tool.name} (score: {score:.3f}): {tool.description}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        try:
            server_name, original_name = parse_prefixed_name(name)
        except ValueError:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

        upstream_session = state.upstream.sessions.get(server_name)
        if not upstream_session:
            return [types.TextContent(type="text", text=f"No upstream server: {server_name}")]

        logger.info("Proxying tool call %s -> %s on %s", name, original_name, server_name)
        try:
            result = await upstream_session.call_tool(original_name, arguments)
            return list(result.content)
        except Exception as exc:
            logger.error("Tool call failed: %s on %s: %s", original_name, server_name, exc)
            return [types.TextContent(type="text", text=f"Error calling {name}: {exc}")]

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            init_options = app.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True),
            )
            await app.run(read_stream, write_stream, init_options)

    anyio.run(_run)
