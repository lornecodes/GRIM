#!/usr/bin/env python3
"""
One-time migration: move stories from feat-*.md to proj-*.md FDOs.

Part of the Task System Simplification (stories as pool work orders).
- Scans feat-*.md for stories/archived_stories
- Finds parent proj-* via the feat's `related` field
- Moves story YAML into proj-*.md frontmatter
- Strips `tasks:` arrays from each story
- Updates story IDs from story-{feat}-{N} to story-{proj}-{N}
- Updates board.yaml and schedule.yaml with new IDs
- Cleans feat-* FDOs (removes stories/archived_stories fields)

Run:
    python scripts/migrate_stories_to_projects.py [--dry-run] [--vault PATH]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


def load_fdo(path: Path) -> tuple[dict, str]:
    """Parse YAML frontmatter and body from an FDO .md file."""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm = yaml.safe_load(parts[1]) or {}
    body = "---".join(parts[2:])  # preserve rest
    return fm, body


def save_fdo(path: Path, fm: dict, body: str):
    """Write frontmatter + body back to an FDO .md file."""
    dumped = yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{dumped}---{body}", encoding="utf-8")


def find_feat_fdos(vault: Path) -> list[Path]:
    """Find all feat-*.md FDOs in the vault."""
    return sorted(vault.rglob("feat-*.md"))


def find_proj_fdos(vault: Path) -> dict[str, Path]:
    """Map proj-* IDs to their file paths."""
    result = {}
    for p in vault.rglob("proj-*.md"):
        stem = p.stem
        result[stem] = p
    return result


def find_parent_project(feat_fm: dict, proj_map: dict[str, Path]) -> str | None:
    """Find the parent proj-* ID from a feat FDO's related field."""
    related = feat_fm.get("related", []) or []
    for r in related:
        if r.startswith("proj-") and r in proj_map:
            return r
    return None


def strip_tasks(story: dict) -> dict:
    """Remove tasks array from a story dict."""
    story.pop("tasks", None)
    story.pop("archived_tasks", None)
    return story


def migrate(vault_path: str, dry_run: bool = True):
    vault = Path(vault_path)
    if not vault.exists():
        print(f"ERROR: Vault path not found: {vault}")
        sys.exit(1)

    feat_files = find_feat_fdos(vault)
    proj_map = find_proj_fdos(vault)
    print(f"Found {len(feat_files)} feat-* FDOs, {len(proj_map)} proj-* FDOs")

    # Collect all stories to migrate, grouped by target project
    id_remap: dict[str, str] = {}  # old_id -> new_id
    proj_stories: dict[str, list[dict]] = {}  # proj_id -> [stories]
    proj_archived: dict[str, list[dict]] = {}  # proj_id -> [archived_stories]
    proj_counters: dict[str, int] = {}  # proj_id -> next sequential number
    feats_to_clean: list[Path] = []
    orphaned: list[tuple[str, str]] = []  # (feat_id, reason)

    for feat_path in feat_files:
        fm, body = load_fdo(feat_path)
        feat_id = feat_path.stem
        stories = fm.get("stories", []) or []
        archived = fm.get("archived_stories", []) or []

        if not stories and not archived:
            continue

        proj_id = find_parent_project(fm, proj_map)
        if not proj_id:
            orphaned.append((feat_id, f"{len(stories)} stories, {len(archived)} archived — no parent proj-* found"))
            continue

        feats_to_clean.append(feat_path)

        proj_short = proj_id.replace("proj-", "")
        if proj_id not in proj_stories:
            proj_stories[proj_id] = []
            proj_archived[proj_id] = []
            proj_counters[proj_id] = 1

        def next_id(proj_id: str, proj_short: str) -> str:
            n = proj_counters[proj_id]
            proj_counters[proj_id] = n + 1
            return f"story-{proj_short}-{n:03d}"

        # Archived first (they're older), then active
        for s in archived:
            s = strip_tasks(s)
            old_id = s.get("id", "")
            new_id = next_id(proj_id, proj_short)
            if old_id and old_id != new_id:
                id_remap[old_id] = new_id
            s["id"] = new_id
            proj_archived[proj_id].append(s)

        for s in stories:
            s = strip_tasks(s)
            old_id = s.get("id", "")
            new_id = next_id(proj_id, proj_short)
            if old_id and old_id != new_id:
                id_remap[old_id] = new_id
            s["id"] = new_id
            proj_stories[proj_id].append(s)

    # Report
    print(f"\nMigration plan:")
    print(f"  Stories to migrate: {sum(len(v) for v in proj_stories.values())} active, "
          f"{sum(len(v) for v in proj_archived.values())} archived")
    print(f"  Target projects: {list(proj_stories.keys())}")
    print(f"  ID remaps: {len(id_remap)}")
    print(f"  Feat FDOs to clean: {len(feats_to_clean)}")
    if orphaned:
        print(f"\n  ORPHANED (skipped — no parent proj-*):")
        for fid, reason in orphaned:
            print(f"    {fid}: {reason}")

    if id_remap:
        print(f"\n  ID remaps:")
        for old, new in sorted(id_remap.items()):
            print(f"    {old} -> {new}")

    if dry_run:
        print("\n[DRY RUN] No changes written. Run without --dry-run to apply.")
        return

    # 1. Add stories to proj-* FDOs
    for proj_id, stories in proj_stories.items():
        proj_path = proj_map[proj_id]
        fm, body = load_fdo(proj_path)
        existing = fm.get("stories", []) or []
        existing_ids = {s.get("id") for s in existing}
        for s in stories:
            if s.get("id") not in existing_ids:
                existing.append(s)
        fm["stories"] = existing

        archived = proj_archived.get(proj_id, [])
        if archived:
            existing_arch = fm.get("archived_stories", []) or []
            existing_arch_ids = {s.get("id") for s in existing_arch}
            for s in archived:
                if s.get("id") not in existing_arch_ids:
                    existing_arch.append(s)
            fm["archived_stories"] = existing_arch

        save_fdo(proj_path, fm, body)
        print(f"  Updated {proj_id}: +{len(stories)} stories, +{len(archived)} archived")

    # 2. Clean feat-* FDOs
    for feat_path in feats_to_clean:
        fm, body = load_fdo(feat_path)
        fm.pop("stories", None)
        fm.pop("archived_stories", None)
        save_fdo(feat_path, fm, body)
        print(f"  Cleaned {feat_path.stem}")

    # 3. Update board.yaml
    board_path = vault / "projects" / "board.yaml"
    if board_path.exists() and id_remap:
        text = board_path.read_text(encoding="utf-8")
        for old_id, new_id in id_remap.items():
            text = text.replace(old_id, new_id)
        board_path.write_text(text, encoding="utf-8")
        print(f"  Updated board.yaml ({len(id_remap)} ID remaps)")

    # 4. Update schedule.yaml
    schedule_path = vault / "calendar" / "schedule.yaml"
    if schedule_path.exists() and id_remap:
        text = schedule_path.read_text(encoding="utf-8")
        for old_id, new_id in id_remap.items():
            text = text.replace(old_id, new_id)
        schedule_path.write_text(text, encoding="utf-8")
        print(f"  Updated schedule.yaml ({len(id_remap)} ID remaps)")

    print(f"\nMigration complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate stories from feat-* to proj-* FDOs")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Preview changes without writing")
    parser.add_argument("--vault", default=str(Path(__file__).resolve().parent.parent.parent / "kronos-vault"),
                        help="Path to kronos-vault")
    args = parser.parse_args()
    migrate(args.vault, dry_run=args.dry_run)
