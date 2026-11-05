"""Shared entry point so every server can run over stdio (default) or HTTP (for the inspector)."""

import argparse
import functools
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


def register_tools(server: MCPServer, functions: list[Callable]) -> None:
    """Register plain functions as MCP tools.

    A ValueError (bad argument, unknown id) is passed to the client as a readable tool error,
    so the agent can correct itself. Any other exception stays a generic server error.
    """
    for fn in functions:
        @functools.wraps(fn)
        def wrapper(*args, _fn=fn, **kwargs):
            try:
                return _fn(*args, **kwargs)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        server.tool()(wrapper)


def run_server(server: MCPServer, default_port: int) -> None:
    parser = argparse.ArgumentParser(description=f"MCP server: {server.name}")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()
    if args.transport == "stdio":
        server.run()
    else:
        server.run(transport="streamable-http", host=args.host, port=args.port)
