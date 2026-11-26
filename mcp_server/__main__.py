"""Entry point: python -m mcp_server [--transport stdio|streamable-http] [--host H] [--port P]."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server.server import build_server  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Organizational Synapse MCP server")
    parser.add_argument(
        "--transport", choices=["stdio", "streamable-http"], default=os.environ.get("MCP_TRANSPORT", "stdio")
    )
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8765")))
    args = parser.parse_args()

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
