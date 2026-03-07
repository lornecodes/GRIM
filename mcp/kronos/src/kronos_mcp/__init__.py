from . import server
import asyncio


def main():
    """Main entry point for kronos-mcp (stdio mode)."""
    asyncio.run(server.main())


def main_sse(host: str = "127.0.0.1", port: int = 8319):
    """Main entry point for kronos-mcp SSE mode."""
    asyncio.run(server.main_sse(host=host, port=port))


__all__ = ["main", "main_sse", "server"]
