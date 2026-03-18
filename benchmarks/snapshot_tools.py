"""Snapshot utility — connects to live MCP servers and saves tool schemas to JSON.

Usage:
    python benchmarks/snapshot_tools.py --config smartmcp.json --output benchmarks/tools_snapshot.json
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import click

from smartmcp.config import load_config
from smartmcp.upstream import UpstreamManager

logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")
logger = logging.getLogger(__name__)


async def _snapshot(config_path: str, output_path: str) -> None:
    config = load_config(config_path)
    upstream = UpstreamManager()

    failed = await upstream.connect_all(config)
    if failed:
        logger.warning("Failed to connect: %s", ", ".join(failed))

    raw_tools = await upstream.collect_tools()

    snapshot = []
    for server_name, tool in raw_tools:
        snapshot.append({
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": tool.inputSchema,
        })

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2))

    await upstream.close()

    server_counts: dict[str, int] = {}
    for item in snapshot:
        server = item["name"].split("__", 1)[0]
        server_counts[server] = server_counts.get(server, 0) + 1

    logger.info("Saved %d tools to %s", len(snapshot), output_path)
    for server, count in sorted(server_counts.items()):
        logger.info("  %s: %d tools", server, count)


@click.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True),
              help="Path to smartmcp.json config file.")
@click.option("--output", "output_path", default="benchmarks/tools_snapshot.json",
              help="Output path for the snapshot JSON.")
def main(config_path: str, output_path: str) -> None:
    """Capture a snapshot of all tool schemas from configured MCP servers."""
    asyncio.run(_snapshot(config_path, output_path))


if __name__ == "__main__":
    main()
