"""CLI entry point for smartmcp."""

from __future__ import annotations

import logging

import click

from smartmcp.config import load_config
from smartmcp.server import run_server


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
    logging.basicConfig(level=logging.INFO, format="%(name)s — %(message)s")
    config = load_config(config_path)
    run_server(config)
