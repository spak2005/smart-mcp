"""Configuration loading and validation for smartmcp."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class SmartMCPConfig:
    servers: dict[str, ServerConfig]
    top_k: int = 5
    embedding_model: str = "all-MiniLM-L6-v2"


def load_config(path: str | Path) -> SmartMCPConfig:
    """Load and validate a smartmcp.json configuration file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw: dict[str, Any] = json.loads(path.read_text())

    raw_servers = raw.get("mcpServers")
    if not raw_servers or not isinstance(raw_servers, dict):
        raise ValueError("Config must contain a non-empty 'mcpServers' object")

    servers: dict[str, ServerConfig] = {}
    for name, entry in raw_servers.items():
        if "command" not in entry:
            raise ValueError(f"Server '{name}' is missing required 'command' field")
        servers[name] = ServerConfig(
            command=entry["command"],
            args=entry.get("args", []),
            env=entry.get("env", {}),
        )

    return SmartMCPConfig(
        servers=servers,
        top_k=raw.get("top_k", 5),
        embedding_model=raw.get("embedding_model", "all-MiniLM-L6-v2"),
    )
