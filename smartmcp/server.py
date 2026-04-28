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
CALL_DISCOVERED_TOOL_NAME = "call_discovered_tool"

SEARCH_TOOLS_SCHEMA = types.Tool(
    name=SEARCH_TOOLS_NAME,
    description=(
        "Your gateway to all available tools across connected MCP servers. "
        "Call this first to find the right tool for your task. "
        "Each match returned by search_tools includes a 'target' identifier and the full 'input_schema' "
        "you must use to call the matched upstream tool through the call_discovered_tool tool. "
        "Always describe the task you want to perform using an action verb and object (for example: "
        "'read the contents of a file from disk' or 'create a new issue in a GitHub repository'). "
        "When you are certain about the product or service you want to use (such as GitHub, Google Drive, Gmail, or Google Calendar), "
        "include it in your description, but do not guess or invent internal tool names like 'github__create_issue' — "
        "simply state the task and, if known, the system where it should happen."
    ),
    inputSchema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Describe the specific task you want to perform using an action verb and object. "
                    "For example: 'list all files in a directory' not 'files'. "
                    "'create a new issue in a GitHub repository' not 'GitHub'. "
                    "When you are certain about the product or service you want to use (for example, GitHub, Google Drive, Gmail, or Google Calendar), "
                    "mention it in the description, but never guess or invent internal tool/function names — "
                    "smartmcp will select the right tool based on your description."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Number of tools to return (default: 5)",
            },
        },
    },
)

CALL_DISCOVERED_TOOL_SCHEMA = types.Tool(
    name=CALL_DISCOVERED_TOOL_NAME,
    description=(
        "Invoke an upstream MCP tool that was returned by search_tools. "
        "Copy the 'target' value from a search_tools match exactly — do not invent or modify it. "
        "Build 'arguments' to satisfy the matching tool's 'input_schema' as returned by search_tools. "
        "Arguments are forwarded to the upstream tool unchanged."
    ),
    inputSchema={
        "type": "object",
        "required": ["target", "arguments"],
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "The exact 'target' identifier returned by a search_tools match "
                    "(for example 'github__create_issue'). Do not guess or modify this value."
                ),
            },
            "arguments": {
                "type": "object",
                "description": (
                    "Arguments object that satisfies the matched tool's 'input_schema'. "
                    "Forwarded to the upstream tool without modification."
                ),
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
