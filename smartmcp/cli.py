"""CLI entry point for smartmcp."""

from __future__ import annotations

import click

from smartmcp.config import load_config


@click.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True),
    help="Path to smartmcp.json configuration file.",
)
def main(config_path: str) -> None:
    """smartmcp — Intelligent MCP tool routing."""
    config = load_config(config_path)
    click.echo(f"Loaded {len(config.servers)} server(s) from config")
