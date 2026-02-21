# smartmcp

**Intelligent MCP tool routing — reduce context bloat by serving only the tools your AI actually needs.**

> In my own setup — 8 MCP servers, 224 tools — every AI request was loading **~66,000 tokens** of tool schemas before the model even started thinking. With smartmcp, that dropped to **~1,600 tokens**. A **97% reduction**, every single request.

| | Without smartmcp | With smartmcp |
|---|---|---|
| Tools in context | All 224 | 1 (`search_tools`) + 5 matched |
| Tokens per request | ~66,000 | ~1,600 |
| Scales with | Every tool you add (O(n)) | Always top-k (O(1)) |

*Token counts estimated at ~4 characters per token. Actual counts vary by model tokenizer.*

Most MCP setups expose every tool from every server to the AI at once. With 5+ servers, that's 50–200+ tool schemas crammed into the context window before the AI even starts thinking. smartmcp fixes this.

smartmcp is a proxy MCP server that sits between your AI client and your upstream MCP servers. It indexes all available tools using semantic embeddings, then exposes a single `search_tools` tool. When the AI describes what it wants to do, smartmcp finds the most relevant tools and **dynamically surfaces their full schemas** — so the AI can see their parameters and call them directly.

## How it works

```
AI Client (Claude Desktop / Cursor / your agent)
    ↕  stdio
smartmcp (proxy server)
    ↕  stdio (one connection per server)
[github] [filesystem] [google-workspace] [git] [memory] [puppeteer] ...
```

smartmcp uses a two-phase flow: **discover**, then **call**.

### Phase 1 — Discovery

1. On startup, smartmcp connects to all your configured MCP servers, collects every tool schema, and builds a [FAISS](https://github.com/facebookresearch/faiss) vector index using [sentence-transformer](https://www.sbert.net/) embeddings.
2. The AI sees only one tool: `search_tools`. It calls it with a natural language query — e.g. `search_tools({ "query": "create a GitHub issue" })`.
3. smartmcp runs semantic search across all indexed tools and finds the top-k matches.
4. The **full schemas** of those matching tools (name, description, parameters, types) are dynamically added to the tool list. smartmcp sends a `tool_list_changed` notification so the AI client re-fetches and sees them.

### Phase 2 — Calling

5. The AI now sees the surfaced tool schemas with their complete `inputSchema`. It picks the right tool, constructs the correct arguments itself, and calls it.
6. smartmcp parses the namespaced tool name (e.g. `github__create_issue`), routes the call to the correct upstream server, and returns the result.

The intelligence is in the **discovery** step. The AI still does its own parameter construction based on the exposed schemas — smartmcp just narrows down *which* tools it sees.

## Installation

```bash
pip install smartmcp-router
```

Requires Python 3.10+.

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

Replace your list of MCP servers with a single smartmcp entry.

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

Your AI now sees a single `search_tools` tool. When it needs to do something, it searches:

> **AI calls:** `search_tools({ "query": "read files from disk" })`
>
> **smartmcp returns:** 3 matching tools from the filesystem server — their full schemas are now exposed.
>
> **AI sees:** The complete parameter definitions for `filesystem__read_file`, `filesystem__list_directory`, etc. It picks the right one, fills in the arguments, and calls it.
>
> **smartmcp proxies** the call to the filesystem server and returns the result.

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

- **Less context waste** — Instead of 100 tool schemas in every request, the AI sees 1 tool + only the few it actually needs.
- **Better tool selection** — Semantic search finds the right tools even when the AI doesn't know the exact name.
- **Full schema exposure** — Surfaced tools include their complete parameter definitions, so the AI constructs calls correctly.
- **Works with any MCP server** — If it speaks MCP over stdio, smartmcp can proxy it.
- **Drop-in replacement** — Replace your list of MCP servers with one smartmcp entry. No code changes needed.
- **Graceful degradation** — If some upstream servers fail to connect, smartmcp continues with whatever is available.

## Contributing

smartmcp is early-stage and actively improving. Contributions are welcome — especially around search accuracy, embedding strategies, and support for new transports.

If you have ideas, find bugs, or want to add features, open an issue or submit a PR on [GitHub](https://github.com/israelogbonna/smart-mcp).

## License

[MIT](LICENSE)
