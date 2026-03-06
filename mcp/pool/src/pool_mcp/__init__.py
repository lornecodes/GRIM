"""Pool MCP server — execution pool introspection and control via MCP."""

from . import server
import asyncio


def main():
    """Main entry point for pool-mcp."""
    asyncio.run(server.main())


__all__ = ["main", "server"]
