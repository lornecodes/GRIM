"""Allow running kronos-mcp with `python -m kronos_mcp`.

Supports two modes:
  python -m kronos_mcp           # stdio (default — one process per client)
  python -m kronos_mcp --sse     # SSE HTTP server (persistent, shared)
"""
import argparse
import asyncio


def cli():
    parser = argparse.ArgumentParser(description="Kronos MCP Server")
    parser.add_argument(
        "--sse", action="store_true",
        help="Run as persistent SSE HTTP server instead of stdio",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="SSE server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=8319,
        help="SSE server port (default: 8319)",
    )
    args = parser.parse_args()

    if args.sse:
        from kronos_mcp.server import main_sse
        asyncio.run(main_sse(host=args.host, port=args.port))
    else:
        from kronos_mcp import main
        main()


cli()
