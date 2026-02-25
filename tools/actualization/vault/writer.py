"""
FDO Writer + CrossLinker — writes FDOs to vault and patches backlinks.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class FDOWriter:
    """Writes FDO markdown files to the vault."""

    def __init__(self, vault_path: Path):
        self.vault = vault_path

    def write(self, fdo: Dict[str, Any], vault_rel_dir: Path) -> str:
        """
        Write an FDO dict to a markdown file.
        Returns the vault-relative path of the written file.
        """
        fdo_id = fdo["id"]
        target_dir = self.vault / vault_rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        # Use fdo_id as filename, but strip the repo prefix for readability
        # e.g. "myrepo-src-main" → "src-main.md" if inside repos/myrepo/
        filename = fdo_id.split("-", 1)[-1] if "-" in fdo_id else fdo_id
        # But for index files use _index.md
        if fdo.get("is_index"):
            filepath = target_dir / "_index.md"
        else:
            filepath = target_dir / f"{filename}.md"

        content = self._render_markdown(fdo)
        filepath.write_text(content, encoding="utf-8")

        return str(filepath.relative_to(self.vault)).replace("\\", "/")

    def _render_markdown(self, fdo: Dict[str, Any]) -> str:
        now = datetime.now().strftime("%Y-%m-%d")

        fm_lines = [
            "---",
            f"id: {fdo['id']}",
            f'title: "{fdo.get("title", "Untitled")}"',
            f"domain: {fdo.get('domain', 'tools')}",
            f"created: {now}",
            f"updated: {now}",
            f"status: {fdo.get('status', 'seed')}",
            f"confidence: {fdo.get('confidence', 0.3)}",
        ]

        related = fdo.get("related", [])
        if related:
            fm_lines.append(f"related: [{', '.join(related)}]")
        else:
            fm_lines.append("related: []")

        source_repos = fdo.get("source_repos", [])
        fm_lines.append(f"source_repos: [{', '.join(source_repos)}]")

        tags = fdo.get("tags", [])
        fm_lines.append(f"tags: [{', '.join(tags)}]")

        if fdo.get("pac_parent"):
            fm_lines.append(f"pac_parent: {fdo['pac_parent']}")
        if fdo.get("pac_children"):
            fm_lines.append(f"pac_children: [{', '.join(fdo['pac_children'])}]")
        if fdo.get("source_path"):
            fm_lines.append(f'source_path: "{fdo["source_path"]}"')

        fm_lines.append("---")

        # Body
        title = fdo.get("title", "Untitled")
        body = f"# {title}\n\n"

        for section in ["summary", "details", "connections", "open_questions", "references"]:
            content = fdo.get(section, "")
            if content:
                heading = section.replace("_", " ").title()
                body += f"## {heading}\n\n{content}\n\n"

        return "\n".join(fm_lines) + "\n\n" + body


class CrossLinker:
    """Patches existing FDOs to add backlinks when a new related FDO is created."""

    def __init__(self, vault_path: Path):
        self.vault = vault_path
        self.patches_applied = 0

    def patch(self, fdo_path: str, link_text: str) -> bool:
        """
        Add a backlink line to an existing FDO's Connections section.
        Returns True if patched, False if skipped or failed.
        """
        full_path = self.vault / fdo_path
        if not full_path.exists():
            return False

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return False

        # Skip if this link already exists
        link_id = link_text.split("]]")[0] if "]]" in link_text else link_text[:40]
        if link_id in content:
            return False

        # Find Connections section and append
        conn_match = re.search(r'(## Connections\s*\n)', content)
        if conn_match:
            insert_at = conn_match.end()
            # Find next section
            next_section = re.search(r'\n## ', content[insert_at:])
            if next_section:
                insert_at = insert_at + next_section.start()
            else:
                insert_at = len(content)

            new_content = (
                content[:insert_at].rstrip() + "\n" + link_text + "\n\n" +
                content[insert_at:].lstrip("\n")
            )
        else:
            # No Connections section — create one before Open Questions
            oq_match = re.search(r'\n## Open Questions', content)
            insert_at = oq_match.start() if oq_match else len(content)
            new_content = (
                content[:insert_at].rstrip() +
                f"\n\n## Connections\n\n{link_text}\n\n" +
                content[insert_at:].lstrip("\n")
            )

        # Update the "updated" date
        today = datetime.now().strftime("%Y-%m-%d")
        new_content = re.sub(
            r'updated: \d{4}-\d{2}-\d{2}',
            f'updated: {today}',
            new_content,
        )

        full_path.write_text(new_content, encoding="utf-8")
        self.patches_applied += 1
        return True

    def extend_fdo(
        self,
        fdo_path: str,
        additions: Dict[str, Any],
        new_source: str,
    ) -> bool:
        """
        Extend an existing FDO with new details/connections from another source.
        Used by the 'extend' decision path.
        """
        full_path = self.vault / fdo_path
        if not full_path.exists():
            return False

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return False

        modified = False

        # Append to Details section
        detail_adds = additions.get("additions_to_details", "")
        if detail_adds:
            details_match = re.search(r'(## Details\s*\n)', content)
            if details_match:
                # Find end of details section
                next_sec = re.search(r'\n## ', content[details_match.end():])
                if next_sec:
                    insert_at = details_match.end() + next_sec.start()
                else:
                    insert_at = len(content)
                content = (
                    content[:insert_at].rstrip() +
                    f"\n\n### From {new_source}\n\n{detail_adds}\n\n" +
                    content[insert_at:].lstrip("\n")
                )
                modified = True

        # Append to Connections
        conn_adds = additions.get("additions_to_connections", "")
        if conn_adds:
            self.patch(fdo_path, conn_adds)
            modified = True

        # Add new tags to frontmatter
        new_tags = additions.get("new_tags", [])
        if new_tags:
            tags_match = re.search(r'tags: \[([^\]]*)\]', content)
            if tags_match:
                existing = tags_match.group(1)
                for tag in new_tags:
                    if tag not in existing:
                        existing += f", {tag}"
                content = content[:tags_match.start()] + f"tags: [{existing}]" + content[tags_match.end():]
                modified = True

        if modified:
            today = datetime.now().strftime("%Y-%m-%d")
            content = re.sub(r'updated: \d{4}-\d{2}-\d{2}', f'updated: {today}', content)
            full_path.write_text(content, encoding="utf-8")

        return modified
