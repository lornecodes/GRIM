from . import server
import asyncio


def main():
    """Main entry point for kronos-mcp."""
    asyncio.run(server.main())


__all__ = ["main", "server"]
