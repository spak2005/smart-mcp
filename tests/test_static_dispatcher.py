"""End-to-end test: a client can discover and invoke an upstream tool with a
single ``tools/list`` and no ``tools/list_changed`` support.

The test exercises the SmartMCP static dispatcher contract:

1. ``tools/list`` returns exactly two stable tools: ``search_tools`` and
   ``call_discovered_tool``. They never change for the lifetime of the session.
2. ``search_tools`` returns structured JSON containing the exact ``target`` and
   full upstream ``input_schema`` needed to invoke the matched tool.
3. ``call_discovered_tool`` proxies the call to the real upstream tool using
   the returned ``target``, forwarding ``arguments`` unchanged.

The test never relies on ``notifications/tools/list_changed``.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any

from mcp import types

from smartmcp.config import SmartMCPConfig
from smartmcp.server import (
    CALL_DISCOVERED_TOOL_NAME,
    SEARCH_TOOLS_NAME,
    SmartMCPState,
    handle_call_discovered_tool,
    handle_search_tools,
    list_static_tools,
)


class _FakeIndex:
    """Stand-in for ``EmbeddingIndex`` that returns canned matches."""

    def __init__(self, tools: list[types.Tool]) -> None:
        self._tools = tools

    def search(self, query: str, top_k: int = 5) -> list[tuple[types.Tool, float]]:
        return [(tool, 0.9) for tool in self._tools[:top_k]]


@dataclass
class _FakeCallResult:
    content: list[Any]


class _FakeSession:
    """Records upstream ``call_tool`` invocations for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _FakeCallResult:
        self.calls.append((name, arguments))
        return _FakeCallResult(
            content=[types.TextContent(type="text", text=f"called {name}")]
        )


class _FakeUpstream:
    def __init__(self, sessions: dict[str, _FakeSession]) -> None:
        self.sessions = sessions


def _build_state() -> tuple[SmartMCPState, _FakeSession]:
    upstream_tool = types.Tool(
        name="github__create_issue",
        description="Create a GitHub issue",
        inputSchema={
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
        },
    )
    session = _FakeSession()
    upstream = _FakeUpstream(sessions={"github": session})
    index = _FakeIndex(tools=[upstream_tool])
    config = SmartMCPConfig(servers={}, top_k=5)
    state = SmartMCPState(
        upstream=upstream,  # type: ignore[arg-type]
        index=index,  # type: ignore[arg-type]
        all_tools=[("github", upstream_tool)],
        config=config,
    )
    return state, session


class StaticDispatcherEndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def test_static_tools_list_contains_only_search_and_call(self) -> None:
        state, _ = _build_state()

        tools = list_static_tools(state)
        names = {tool.name for tool in tools}

        self.assertEqual(names, {SEARCH_TOOLS_NAME, CALL_DISCOVERED_TOOL_NAME})

    async def test_search_then_call_discovered_tool_proxies_unchanged(self) -> None:
        state, session = _build_state()

        search_result = await handle_search_tools(
            state, {"query": "create a github issue"}
        )
        self.assertEqual(len(search_result), 1)
        payload = json.loads(search_result[0].text)
        self.assertIn("matches", payload)
        self.assertGreaterEqual(len(payload["matches"]), 1)

        match = payload["matches"][0]
        self.assertEqual(match["target"], "github__create_issue")
        self.assertIn("input_schema", match)
        self.assertEqual(
            match["input_schema"]["properties"]["title"]["type"], "string"
        )

        arguments = {"title": "hello", "body": "world"}
        call_result = await handle_call_discovered_tool(
            state,
            {"target": match["target"], "arguments": arguments},
        )

        self.assertEqual(len(session.calls), 1)
        called_name, called_args = session.calls[0]
        self.assertEqual(called_name, "create_issue")
        self.assertEqual(called_args, arguments)

        self.assertEqual(len(call_result), 1)
        self.assertIn("called create_issue", call_result[0].text)


if __name__ == "__main__":
    unittest.main()
