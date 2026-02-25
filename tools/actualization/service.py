"""
Actualization Service — CLI entry point for the knowledge ingestion pipeline.

Usage:
    python -m actualization.service scan <repo_path> [--vault <vault_path>] [--force]
    python -m actualization.service status [--vault <vault_path>]

Commands:
    scan     Ingest a repository into the Kronos vault
    status   Show vault statistics and recent ingestions
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

console = Console()

# =========================================================================
# Defaults
# =========================================================================

DEFAULT_VAULT = Path(__file__).resolve().parents[3] / "kronos-vault"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
VAULT_REPOS_DIR = "repos"
VAULT_SYNC_DIR = ".sync"


# =========================================================================
# Service
# =========================================================================

class ActualizationService:
    """Orchestrates the full ingestion pipeline."""

    def __init__(
        self,
        vault_path: Path,
        model: str = DEFAULT_MODEL,
        force: bool = False,
        domain: str = "tools",
    ):
        self.vault_path = vault_path.resolve()
        self.model = model
        self.force = force
        self.domain = domain

        # Lazy imports so --help is fast
        self._client = None
        self._graph = None
        self._vault_index = None
        self._writer = None
        self._crosslinker = None

    def _init_client(self):
        """Initialize Anthropic client."""
        from anthropic import Anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            console.print("[red]ERROR: ANTHROPIC_API_KEY not set in environment or .env[/red]")
            sys.exit(1)

        self._client = Anthropic(api_key=api_key)

    def _init_vault(self):
        """Initialize vault index and writer."""
        from .vault.index import VaultIndex
        from .vault.writer import FDOWriter, CrossLinker

        self._vault_index = VaultIndex(self.vault_path)
        count = self._vault_index.build()
        console.print(f"  Vault index: [cyan]{count}[/cyan] existing FDOs")

        self._writer = FDOWriter(self.vault_path)
        self._crosslinker = CrossLinker(self.vault_path)

    def _init_graph(self):
        """Build the LangGraph state machine."""
        from .graph import build_actualization_graph
        self._graph = build_actualization_graph()

    # -----------------------------------------------------------------
    # Scan command
    # -----------------------------------------------------------------

    def scan(self, repo_path: str, source_id: Optional[str] = None):
        """Full ingestion of a repository."""
        from .sources.repo import RepoSource
        from .sources.base import SyncManifest

        t0 = time.time()
        repo = Path(repo_path).resolve()
        if not repo.is_dir():
            console.print(f"[red]ERROR: Not a directory: {repo}[/red]")
            sys.exit(1)

        console.print(Panel(
            f"[bold]Actualizing:[/bold] {repo}\n"
            f"[bold]Vault:[/bold] {self.vault_path}\n"
            f"[bold]Model:[/bold] {self.model}\n"
            f"[bold]Force:[/bold] {self.force}",
            title="GRIM Actualization Service",
            border_style="blue",
        ))

        # Initialize components
        console.print("\n[dim]Initializing...[/dim]")
        # .env lives at GRIM/.env — service.py is at GRIM/tools/actualization/
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        self._init_client()
        self._init_vault()
        self._init_graph()

        # Create source
        source = RepoSource(repo, source_id=source_id)
        console.print(f"  Source: [cyan]{source.source_id}[/cyan] ({source.source_type})")

        # Load manifest
        sync_dir = self.vault_path / VAULT_SYNC_DIR
        manifest = source.load_manifest(sync_dir)
        if manifest.last_sync and not self.force:
            console.print(f"  Last sync: [yellow]{manifest.last_sync}[/yellow]")

        # Phase 1: Discover files
        console.print("\n[bold]Phase 1: Discovery[/bold]")
        chunks = list(source.discover())
        console.print(f"  Found [cyan]{len(chunks)}[/cyan] files")

        # Filter unchanged (unless --force)
        if not self.force:
            changed = []
            for chunk in chunks:
                if manifest.is_changed(chunk.path, chunk.content_hash):
                    changed.append(chunk)
            skipped = len(chunks) - len(changed)
            if skipped > 0:
                console.print(f"  Skipping [dim]{skipped}[/dim] unchanged files")
            chunks = changed

        if not chunks:
            console.print("[green]Nothing to actualize — vault is up to date.[/green]")
            return

        # Phase 2: Process files through graph
        console.print(f"\n[bold]Phase 2: Actualization ({len(chunks)} files)[/bold]")
        accumulators = {
            "fdos_created": [],
            "fdos_linked": [],
            "fdos_skipped": [],
            "errors": [],
            "api_calls": 0,
            "api_input_tokens": 0,
            "api_output_tokens": 0,
        }

        vault_repo_dir = Path(VAULT_REPOS_DIR) / source.source_id

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            task = progress.add_task("Processing files...", total=len(chunks))

            for i, chunk in enumerate(chunks):
                short_path = chunk.path
                if len(short_path) > 50:
                    short_path = "..." + short_path[-47:]
                progress.update(task, description=f"[cyan]{short_path}[/cyan]")

                try:
                    result = self._process_chunk(
                        chunk, source, vault_repo_dir, accumulators
                    )
                    # Accumulate results
                    for key in ["fdos_created", "fdos_linked", "fdos_skipped", "errors"]:
                        new_items = result.get(key, [])
                        if new_items:
                            accumulators[key].extend(new_items)
                    for key in ["api_calls", "api_input_tokens", "api_output_tokens"]:
                        accumulators[key] += result.get(key, 0)

                    # Record in manifest
                    manifest.record(chunk.path, chunk.content_hash)

                except KeyboardInterrupt:
                    console.print("\n[yellow]Interrupted — saving progress...[/yellow]")
                    source.save_manifest(manifest, sync_dir)
                    self._print_summary(accumulators, time.time() - t0)
                    sys.exit(130)
                except Exception as e:
                    accumulators["errors"].append(f"{chunk.path}: {e}")
                    console.print(f"\n  [red]ERROR[/red] {chunk.path}: {e}")

                progress.advance(task)

        # Phase 3: Directory PAC parents
        console.print(f"\n[bold]Phase 3: Directory Synthesis[/bold]")
        dir_chunks = list(source.discover_directories())
        console.print(f"  Building [cyan]{len(dir_chunks)}[/cyan] directory indices")

        for chunk in dir_chunks:
            try:
                result = self._process_directory(
                    chunk, source, vault_repo_dir, accumulators
                )
                for key in ["fdos_created", "errors"]:
                    new_items = result.get(key, [])
                    if new_items:
                        accumulators[key].extend(new_items)
                for key in ["api_calls", "api_input_tokens", "api_output_tokens"]:
                    accumulators[key] += result.get(key, 0)
            except Exception as e:
                accumulators["errors"].append(f"dir:{chunk.path}: {e}")

        # Save manifest
        source.save_manifest(manifest, sync_dir)

        # Summary
        elapsed = time.time() - t0
        self._print_summary(accumulators, elapsed)

    def _process_chunk(
        self,
        chunk,
        source,
        vault_repo_dir: Path,
        accumulators: Dict,
    ) -> Dict[str, Any]:
        """Process a single file chunk through the full graph."""
        initial_state = {
            # Source context
            "source_type": source.source_type,
            "source_id": source.source_id,
            "source_path": str(source.repo_path),
            "domain": self.domain,

            # Current chunk
            "current_content": chunk.content,
            "current_meta": chunk.to_meta(),

            # Graph will fill these
            "concepts": [],
            "entities": [],
            "vault_matches": [],
            "vault_context": "",
            "decision": "new",
            "duplicate_of": None,
            "extend_target": None,
            "skip_reason": None,
            "fdo_draft": None,
            "fdo_id": "",
            "validation": {"passed": False, "errors": [], "warnings": [], "fixes_applied": []},
            "retry_count": 0,
            "cross_links": [],

            # Per-chunk accumulators (start fresh, service aggregates)
            "fdos_created": [],
            "fdos_linked": [],
            "fdos_skipped": [],
            "errors": [],
            "api_calls": 0,
            "api_input_tokens": 0,
            "api_output_tokens": 0,

            # Injected dependencies (not serialized by LangGraph)
            "_client": self._client,
            "_model": self.model,
            "_vault_index": self._vault_index,
            "_writer": self._writer,
            "_crosslinker": self._crosslinker,
            "_vault_repo_dir": str(vault_repo_dir),
        }

        result = self._graph.invoke(initial_state)
        return result

    def _process_directory(
        self,
        chunk,
        source,
        vault_repo_dir: Path,
        accumulators: Dict,
    ) -> Dict[str, Any]:
        """Process a directory chunk — builds PAC parent index file."""
        from .nodes.actualize import actualize_directory, make_fdo_id, make_pac_parent_id
        from .nodes.commit import commit

        fdo_id = make_fdo_id(source.source_id, chunk.path or source.source_id)
        pac_parent_id = make_pac_parent_id(source.source_id, chunk.parent_path) if chunk.path else ""

        # Build pac_children from chunk's immediate children
        pac_children = []
        for child in chunk.children:
            child_id = make_fdo_id(source.source_id, child)
            if self._vault_index.has(child_id):
                pac_children.append(child_id)

        # Don't self-reference (the root index bug fix)
        pac_children = [c for c in pac_children if c != fdo_id]

        state = {
            "source_type": source.source_type,
            "source_id": source.source_id,
            "source_path": str(source.repo_path),
            "domain": self.domain,
            "current_content": chunk.content,
            "current_meta": chunk.to_meta(),
            "fdo_id": fdo_id,
            "concepts": [],
            "entities": [],
            "vault_matches": [],
            "vault_context": "",
            "decision": "new",
            "fdo_draft": None,
            "cross_links": [],
            "fdos_created": [],
            "fdos_linked": [],
            "fdos_skipped": [],
            "errors": [],
            "api_calls": 0,
            "api_input_tokens": 0,
            "api_output_tokens": 0,
            "_client": self._client,
            "_model": self.model,
            "_vault_index": self._vault_index,
            "_writer": self._writer,
            "_crosslinker": self._crosslinker,
            "_vault_repo_dir": str(vault_repo_dir),
        }

        # Build child summaries from vault index for the Claude call
        child_summaries = []
        for child_path in chunk.children:
            child_fdo_id = make_fdo_id(source.source_id, child_path)
            entry = self._vault_index.get(child_fdo_id)
            if entry:
                child_summaries.append({
                    "name": child_path.rsplit("/", 1)[-1] if "/" in child_path else child_path,
                    "summary": entry.get("summary", entry.get("title", "")),
                })

        # Call actualize_directory with its explicit positional args
        dir_result = actualize_directory(
            source_id=source.source_id,
            dir_path=chunk.path or ".",
            domain=self.domain,
            child_summaries=child_summaries,
            vault_context="",
            client=self._client,
            model=self.model,
        )

        # Build the FDO draft
        fdo_draft = {
            "id": fdo_id,
            "title": dir_result.get("title", chunk.path or source.source_id),
            "domain": self.domain,
            "summary": dir_result.get("summary", ""),
            "details": dir_result.get("details", ""),
            "connections": dir_result.get("connections", ""),
            "status": "seed",
            "confidence": 0.5,
            "related": [],
            "tags": dir_result.get("tags", []),
            "pac_parent": pac_parent_id if pac_parent_id else None,
            "pac_children": pac_children,
            "source_path": chunk.path or source.source_id,
            "source_repos": [source.source_id],
            "is_index": True,
        }

        state["fdo_draft"] = fdo_draft
        state["fdo_id"] = fdo_id

        # Commit directly
        state = commit(state)

        # Track API usage from directory call
        state["api_input_tokens"] = dir_result.get("input_tokens", 0)
        state["api_output_tokens"] = dir_result.get("output_tokens", 0)
        state["api_calls"] = 1 if dir_result.get("input_tokens", 0) > 0 else 0

        return state

    # -----------------------------------------------------------------
    # Status command
    # -----------------------------------------------------------------

    def status(self):
        """Show vault statistics."""
        from .vault.index import VaultIndex

        idx = VaultIndex(self.vault_path)
        count = idx.build()

        table = Table(title="Kronos Vault Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Vault Path", str(self.vault_path))
        table.add_row("Total FDOs", str(count))

        # Count by domain
        domains: Dict[str, int] = {}
        statuses: Dict[str, int] = {}
        repos: set = set()
        for entry in idx.entries.values():
            d = entry.get("domain", "unknown")
            domains[d] = domains.get(d, 0) + 1
            s = entry.get("status", "unknown")
            statuses[s] = statuses.get(s, 0) + 1
            for r in entry.get("source_repos", []):
                repos.add(r)

        table.add_row("Domains", ", ".join(f"{k}: {v}" for k, v in sorted(domains.items())))
        table.add_row("Statuses", ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items())))
        table.add_row("Source Repos", ", ".join(sorted(repos)) if repos else "none")

        # Check sync manifests
        sync_dir = self.vault_path / VAULT_SYNC_DIR
        if sync_dir.is_dir():
            manifests = list(sync_dir.glob("*.json"))
            table.add_row("Sync Manifests", str(len(manifests)))

        console.print(table)

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------

    def _print_summary(self, acc: Dict, elapsed: float):
        """Print a summary of the ingestion run."""
        table = Table(title="Actualization Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("FDOs Created", str(len(acc["fdos_created"])))
        table.add_row("FDOs Linked", str(len(acc["fdos_linked"])))
        table.add_row("Files Skipped", str(len(acc["fdos_skipped"])))
        table.add_row("Errors", str(len(acc["errors"])))
        table.add_row("API Calls", str(acc["api_calls"]))

        # Estimate cost (Claude Sonnet: $3/MTok input, $15/MTok output)
        input_cost = (acc["api_input_tokens"] / 1_000_000) * 3
        output_cost = (acc["api_output_tokens"] / 1_000_000) * 15
        total_cost = input_cost + output_cost
        table.add_row(
            "Tokens (in / out)",
            f"{acc['api_input_tokens']:,} / {acc['api_output_tokens']:,}"
        )
        table.add_row("Est. Cost", f"${total_cost:.4f}")
        table.add_row("Elapsed", f"{elapsed:.1f}s")

        console.print()
        console.print(table)

        if acc["errors"]:
            console.print("\n[bold red]Errors:[/bold red]")
            for err in acc["errors"][:10]:
                console.print(f"  [red]•[/red] {err}")
            if len(acc["errors"]) > 10:
                console.print(f"  ... and {len(acc['errors']) - 10} more")

        if acc["fdos_skipped"]:
            console.print(f"\n[dim]Skipped ({len(acc['fdos_skipped'])}):[/dim]")
            for skip in acc["fdos_skipped"][:10]:
                console.print(f"  [dim]•[/dim] {skip}")


# =========================================================================
# CLI
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="actualization",
        description="GRIM Actualization Service — ingest knowledge into Kronos vault",
    )
    subparsers = parser.add_subparsers(dest="command")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Ingest a repository")
    scan_parser.add_argument("repo_path", help="Path to repository")
    scan_parser.add_argument("--vault", default=str(DEFAULT_VAULT), help="Vault path")
    scan_parser.add_argument("--model", default=DEFAULT_MODEL, help="Claude model to use")
    scan_parser.add_argument("--force", action="store_true", help="Re-process all files")
    scan_parser.add_argument("--domain", default="tools", help="Default domain (tools, physics, ai-systems)")
    scan_parser.add_argument("--source-id", default=None, help="Override source ID")

    # status command
    status_parser = subparsers.add_parser("status", help="Show vault statistics")
    status_parser.add_argument("--vault", default=str(DEFAULT_VAULT), help="Vault path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "scan":
        svc = ActualizationService(
            vault_path=Path(args.vault),
            model=args.model,
            force=args.force,
            domain=args.domain,
        )
        svc.scan(args.repo_path, source_id=args.source_id)

    elif args.command == "status":
        svc = ActualizationService(vault_path=Path(args.vault))
        svc.status()


if __name__ == "__main__":
    main()
