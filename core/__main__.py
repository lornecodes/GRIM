"""GRIM entry point — CLI interface for the companion.

Usage:
    python -m core              # Start interactive session
    python -m core --debug      # Debug mode (test vault, no MCP)
    python -m core --once "msg" # Single message, print response, exit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from core.config import load_config
from core.graph import build_graph


def setup_logging(debug: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down chatty libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


@asynccontextmanager
async def kronos_mcp_session(config):
    """Async context manager for the Kronos MCP connection.

    Properly manages transport + session lifecycle so everything
    gets cleaned up when the session ends.
    """
    logger = logging.getLogger("grim.mcp")

    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(
            command=config.kronos_mcp_command,
            args=config.kronos_mcp_args,
            env={
                "KRONOS_VAULT_PATH": str(config.vault_path),
                "KRONOS_SKILLS_PATH": str(config.skills_path),
                **os.environ,
            },
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                logger.info("Kronos MCP connected (vault: %s)", config.vault_path)
                yield session

    except ImportError:
        logger.warning("MCP library not installed — running without Kronos")
        yield None
    except Exception as exc:
        logger.warning("Failed to connect to Kronos MCP: %s", exc)
        yield None


async def run_session(
    config_path: Path | None = None,
    single_message: str | None = None,
    debug: bool = False,
) -> None:
    """Run a GRIM interactive session."""
    # Load .env
    grim_root = Path(__file__).resolve().parent.parent
    load_dotenv(grim_root / ".env")

    if debug:
        os.environ.setdefault("GRIM_ENV", "debug")

    config = load_config(config_path, grim_root)

    # Set workspace root for tools
    workspace_root = grim_root.parent  # core_workspace root
    from core.tools.workspace import set_workspace_root
    set_workspace_root(workspace_root)

    logger = logging.getLogger("grim")
    logger.info("GRIM starting — env: %s, vault: %s", config.env, config.vault_path)
    logger.info("Skills: %s", config.skills_path)
    logger.info("Workspace: %s", workspace_root)

    # Connect MCP and run within its lifecycle
    async with kronos_mcp_session(config) as mcp_session:
        if mcp_session:
            logger.info("Kronos MCP session active")
        else:
            logger.info("Running without Kronos MCP (debug mode or unavailable)")

        # Build graph
        graph = build_graph(config, mcp_session=mcp_session)

        # Session ID for checkpointing
        session_id = str(uuid.uuid4())[:8]
        graph_config = {"configurable": {"thread_id": f"grim-{session_id}"}}

        if single_message:
            # Single message mode
            result = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content=single_message)],
                    "session_start": datetime.now(),
                },
                config=graph_config,
            )
            _print_response(result)
            return

        # Interactive mode
        print("\n  GRIM — General Recursive Intelligence Machine")
        print(f"  Session: {session_id} | Env: {config.env}")
        if mcp_session:
            print(f"  Vault: {config.vault_path}")
        print("  Type 'quit' or 'exit' to end session.\n")

        while True:
            try:
                user_input = input("you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\nGRIM: Until next time.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("\nGRIM: Until next time.")
                break

            try:
                result = await graph.ainvoke(
                    {
                        "messages": [HumanMessage(content=user_input)],
                        "session_start": datetime.now(),
                    },
                    config=graph_config,
                )
                _print_response(result)
            except Exception as exc:
                logger.exception("Error processing message")
                print(f"\nGRIM: Something went wrong — {exc}\n")


def _print_response(result: dict) -> None:
    """Extract and print GRIM's response from graph state."""
    messages = result.get("messages", [])
    if messages:
        # Find the last AI message (skip tool messages)
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai":
                content = msg.content if hasattr(msg, "content") else str(msg)
                if content:
                    print(f"\nGRIM: {content}\n")
                    return
        # Fallback: print last message
        last = messages[-1]
        content = last.content if hasattr(last, "content") else str(last)
        print(f"\nGRIM: {content}\n")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="GRIM — General Recursive Intelligence Machine")
    parser.add_argument("--debug", action="store_true", help="Run in debug mode (test vault)")
    parser.add_argument("--once", type=str, help="Send a single message and exit")
    parser.add_argument("--config", type=str, help="Path to grim.yaml config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()
    setup_logging(debug=args.verbose)

    config_path = Path(args.config) if args.config else None

    asyncio.run(run_session(
        config_path=config_path,
        single_message=args.once,
        debug=args.debug,
    ))


if __name__ == "__main__":
    main()
