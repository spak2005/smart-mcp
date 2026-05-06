# smartmcp

[![PyPI version](https://img.shields.io/pypi/v/smartmcp-router)](https://pypi.org/project/smartmcp-router/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/smartmcp-router)](https://pypi.org/project/smartmcp-router/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<p align="center">
  <img src="https://raw.githubusercontent.com/spak2005/smart-mcp/main/assets/banner.png" alt="Without smartmcp vs With smartmcp" width="700" />
</p>

**PyPI package:** [`smartmcp-router`](https://pypi.org/project/smartmcp-router/) (CLI: `smartmcp`)

**Intelligent MCP tool routing that reduces context bloat by serving only the tools your AI actually needs.**

> In my own setup with 8 MCP servers and 224 tools, every AI request was loading **~66,000 tokens** of tool schemas before the model even started thinking. With smartmcp, that dropped to **~1,600 tokens**. A **97% reduction** on every request.

| | Without smartmcp | With smartmcp |
|---|---|---|
| Tools in context | All 224 | 2 (`search_tools` + `call_discovered_tool`) |
| Tokens per request | ~66,000 | ~1,600 |
| Scales with | Every tool you add (O(n)) | Always 2 fixed tools (O(1)) |

*Token counts estimated at ~4 characters per token. Actual counts vary by model tokenizer.*

Most MCP setups expose every tool from every server to the AI at once. With 5+ servers, that's 50–200+ tool schemas crammed into the context window before the AI even starts thinking. smartmcp fixes this.

smartmcp is a proxy MCP server that sits between your AI client and your upstream MCP servers. It indexes all available tools using semantic embeddings and exposes a fixed two-tool surface: `search_tools` to discover the right upstream tool and `call_discovered_tool` to invoke it. The tool list never changes mid-session, so smartmcp works even with clients that ignore `notifications/tools/list_changed`.

## How it works

```
AI Client (Claude Desktop / Cursor / your agent)
    ↕  stdio
smartmcp (proxy server)
    ↕  stdio (one connection per server)
[github] [filesystem] [google-workspace] [git] [memory] [puppeteer] ...
```

smartmcp uses a two-phase flow: **discover**, then **call**.

### Phase 1: Discovery

1. On startup, smartmcp connects to all your configured MCP servers, collects every tool schema, and builds a [FAISS](https://github.com/facebookresearch/faiss) vector index using [sentence-transformer](https://www.sbert.net/) embeddings.
2. The AI always sees exactly two tools: `search_tools` and `call_discovered_tool`. It calls `search_tools` with a natural language query, for example `search_tools({ "query": "create a GitHub issue" })`.
3. smartmcp runs semantic search across all indexed tools and finds the top-k matches.
4. `search_tools` returns structured JSON. Each match includes the exact `target` identifier, the upstream `server` and `name`, the `description`, the relevance `score`, and the full upstream `input_schema`.

### Phase 2: Calling

5. The AI reads the matching tool's `input_schema` from the search response and constructs valid `arguments`.
6. The AI calls `call_discovered_tool({ "target": "<exact target from search>", "arguments": { ... } })`. smartmcp resolves the target (for example `github__create_issue`), routes the call to the correct upstream server, and returns the result. Arguments are forwarded to the upstream tool unchanged.

The intelligence is in the **discovery** step. The AI still does its own parameter construction based on the schema returned by `search_tools`. smartmcp just narrows down *which* tools it sees, and the static two-tool surface means the flow works even with clients that snapshot `tools/list` once and never refresh.

## Installation

```bash
pip install smartmcp-router
```

Requires Python 3.10+.

Recommended on macOS, especially Apple Silicon: install with [`pipx`](https://pipx.pypa.io/) so SmartMCP gets an isolated environment that cannot inherit a stale `torch` or `torchvision` from other projects. This avoids the most common class of install issues (see [Troubleshooting](#troubleshooting)).

```bash
pipx install smartmcp-router
```

## Quick start

### 1. Create a config file

Create a `smartmcp.json` with your upstream MCP servers (same format as Claude Desktop / Cursor config):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/documents"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "ghp_your_token_here"
      }
    },
    "slack": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-slack"],
      "env": {
        "SLACK_BOT_TOKEN": "xoxb-your-token-here"
      }
    }
  },
  "top_k": 5,
  "embedding_model": "all-MiniLM-L6-v2"
}
```

### 2. Add smartmcp to your AI client

Add `smartmcp` as your single MCP server entry. It now manages all your upstream servers defined in `smartmcp.json`.

#### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "smartmcp": {
      "command": "smartmcp",
      "args": ["--config", "/path/to/smartmcp.json"]
    }
  }
}
```

#### Cursor

Add to your `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "smartmcp": {
      "command": "smartmcp",
      "args": ["--config", "/path/to/smartmcp.json"]
    }
  }
}
```

#### Custom agents

Point your MCP client at smartmcp the same way you would any stdio MCP server:

```bash
smartmcp --config /path/to/smartmcp.json
```

### 3. Use it

Your AI now sees two tools: `search_tools` and `call_discovered_tool`. When it needs to do something, it searches and then invokes:

> **AI calls:** `search_tools({ "query": "read files from disk" })`
>
> **smartmcp returns:** JSON with up to top-k matches. Each match includes a `target` (such as `filesystem__read_file`), the upstream tool's `description`, and the full `input_schema`.
>
> **AI sees:** The complete parameter definitions inside each match. It picks the right one, builds `arguments` against that match's `input_schema`, and calls `call_discovered_tool({ "target": "filesystem__read_file", "arguments": { ... } })`.
>
> **smartmcp proxies** the call to the filesystem server unchanged and returns the result.

## Configuration reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mcpServers` | object | _(required)_ | Map of server names to MCP server configs |
| `mcpServers.<name>.command` | string | _(required)_ | Command to spawn the server |
| `mcpServers.<name>.args` | string[] | `[]` | Arguments for the command |
| `mcpServers.<name>.env` | object | `{}` | Environment variables for the server |
| `top_k` | integer | `5` | Default number of tools returned per search |
| `embedding_model` | string | `"all-MiniLM-L6-v2"` | Sentence-transformers model for embeddings |

## Why smartmcp?

- **Less context waste**: Instead of 100 tool schemas in every request, the AI sees a fixed two-tool surface and only inspects the few schemas returned by `search_tools`.
- **Better tool selection**: Semantic search finds the right tools even when the AI doesn't know the exact name.
- **Full schema in search results**: Each match returned by `search_tools` includes the upstream `input_schema`, so the AI can construct calls correctly without any further `tools/list` refresh.
- **Client-agnostic**: The tool list never changes mid-session, so smartmcp works with clients that ignore `notifications/tools/list_changed` (such as Qwen Code and similar agents).
- **Works with any MCP server**: If it speaks MCP over stdio, smartmcp can proxy it.
- **Drop-in replacement**: Replace your list of MCP servers with one smartmcp entry. No code changes needed.
- **Graceful degradation**: If some upstream servers fail to connect, smartmcp continues with whatever is available.

## Contributing

smartmcp is early-stage and actively improving. Contributions are welcome, especially around search accuracy, embedding strategies, and support for new transports.

If you have ideas, find bugs, or want to add features, open an issue or submit a PR on [GitHub](https://github.com/spak2005/smart-mcp).

## License

[MIT](LICENSE)
