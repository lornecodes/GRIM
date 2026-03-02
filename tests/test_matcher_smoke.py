"""Smoke tests for skill matching."""
import pytest

from core.config import GrimConfig
from core.skills.loader import load_skills
from core.skills.matcher import match_skills


@pytest.mark.smoke
class TestMatcherSmoke:
    """Verify skill matcher loads and scores correctly."""

    def test_skills_load(self, grim_config):
        """Skill registry should load without errors."""
        registry = load_skills(grim_config.skills_path)
        assert len(registry) > 0, "No skills loaded"

    def test_vault_skill_matches_capture(self, grim_config):
        """'remember this' should match a kronos capture skill."""
        registry = load_skills(grim_config.skills_path)
        matched = match_skills("remember this concept about topology", registry)
        names = [s.name for s in matched]
        assert any("capture" in n or "kronos" in n for n in names), f"No capture skill matched: {names}"

    def test_no_match_for_casual(self, grim_config):
        """Casual conversation should match few or no specific skills."""
        registry = load_skills(grim_config.skills_path)
        matched = match_skills("lets just talk about physics", registry)
        # Should either be empty or low-confidence matches
        assert len(matched) <= 3, f"Too many matches for casual: {[s.name for s in matched]}"
