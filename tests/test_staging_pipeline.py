"""Tests for Phase 4: Zero-Trust Staging Pipeline.

Tests cover:
- State types (AuditVerdict, StagingArtifact)
- Staging tools (list, read, accept, reject)
- Audit agent verdict parsing
- Audit gate decision logic
- Re-dispatch feedback building
- Audit decision routing
- Dispatch node staging setup
- Integrate node staging cleanup

Run: cd GRIM && python -m pytest tests/test_staging_pipeline.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.state import AgentResult, AuditVerdict, GrimState, StagingArtifact


def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# State types
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditVerdict(unittest.TestCase):
    """Test AuditVerdict dataclass."""

    def test_create_passing_verdict(self):
        v = AuditVerdict(passed=True, summary="All clear")
        self.assertTrue(v.passed)
        self.assertEqual(v.issues, [])
        self.assertEqual(v.suggestions, [])
        self.assertEqual(v.security_flags, [])
        self.assertEqual(v.summary, "All clear")

    def test_create_failing_verdict(self):
        v = AuditVerdict(
            passed=False,
            issues=["hardcoded API key", "incomplete output"],
            suggestions=["add error handling"],
            security_flags=["potential credential exposure"],
            summary="Security violation found",
        )
        self.assertFalse(v.passed)
        self.assertEqual(len(v.issues), 2)
        self.assertEqual(len(v.security_flags), 1)

    def test_default_values(self):
        v = AuditVerdict(passed=True)
        self.assertEqual(v.summary, "")
        self.assertEqual(v.issues, [])


class TestStagingArtifact(unittest.TestCase):
    """Test StagingArtifact dataclass."""

    def test_create_artifact(self):
        a = StagingArtifact(
            path="output.py",
            size_bytes=1024,
            artifact_type="file",
            created_by="ironclaw",
        )
        self.assertEqual(a.path, "output.py")
        self.assertEqual(a.size_bytes, 1024)
        self.assertEqual(a.artifact_type, "file")
        self.assertEqual(a.created_by, "ironclaw")


# ═══════════════════════════════════════════════════════════════════════════
# Staging tools
# ═══════════════════════════════════════════════════════════════════════════


class TestStagingTools(unittest.TestCase):
    """Test staging_list, staging_read, staging_accept, staging_reject tools."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.job_id = "test1234"
        self.job_dir = Path(self.tmpdir) / self.job_id
        self.output_dir = self.job_dir / "output"
        self.output_dir.mkdir(parents=True)

        # Create test files
        (self.output_dir / "hello.py").write_text("print('hello')")
        (self.output_dir / "data.json").write_text('{"key": "value"}')

        # Patch STAGING_BASE
        import core.tools.staging_tools as st
        self._orig_base = st.STAGING_BASE
        st.STAGING_BASE = Path(self.tmpdir)

    def tearDown(self):
        import core.tools.staging_tools as st
        st.STAGING_BASE = self._orig_base
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_staging_list(self):
        from core.tools.staging_tools import staging_list
        result = run_async(staging_list.ainvoke({"job_id": self.job_id}))
        self.assertIn("2 file(s)", result)
        self.assertIn("hello.py", result)
        self.assertIn("data.json", result)

    def test_staging_list_nonexistent(self):
        from core.tools.staging_tools import staging_list
        result = run_async(staging_list.ainvoke({"job_id": "nonexistent"}))
        self.assertIn("[ERROR]", result)

    def test_staging_list_empty(self):
        from core.tools.staging_tools import staging_list
        # Create empty job dir
        empty_id = "empty123"
        empty_out = Path(self.tmpdir) / empty_id / "output"
        empty_out.mkdir(parents=True)
        result = run_async(staging_list.ainvoke({"job_id": empty_id}))
        self.assertIn("empty", result)

    def test_staging_read(self):
        from core.tools.staging_tools import staging_read
        result = run_async(staging_read.ainvoke({"job_id": self.job_id, "path": "hello.py"}))
        self.assertIn("print('hello')", result)
        self.assertIn("hello.py", result)

    def test_staging_read_nonexistent_file(self):
        from core.tools.staging_tools import staging_read
        result = run_async(staging_read.ainvoke({"job_id": self.job_id, "path": "nope.py"}))
        self.assertIn("[ERROR]", result)

    def test_staging_read_path_traversal(self):
        from core.tools.staging_tools import staging_read
        result = run_async(staging_read.ainvoke({"job_id": self.job_id, "path": "../../etc/passwd"}))
        self.assertIn("[ERROR]", result)

    def test_staging_accept(self):
        from core.tools.staging_tools import staging_accept
        dest = Path(self.tmpdir) / "accepted" / "hello.py"
        result = run_async(staging_accept.ainvoke({
            "job_id": self.job_id,
            "path": "hello.py",
            "destination": str(dest),
        }))
        self.assertIn("[ACCEPTED]", result)
        self.assertTrue(dest.exists())
        self.assertFalse((self.output_dir / "hello.py").exists())

    def test_staging_reject(self):
        from core.tools.staging_tools import staging_reject
        result = run_async(staging_reject.ainvoke({
            "job_id": self.job_id,
            "path": "data.json",
            "reason": "Contains test data",
        }))
        self.assertIn("[REJECTED]", result)
        self.assertFalse((self.output_dir / "data.json").exists())
        # Check rejection was logged
        rejections = self.job_dir / "audit" / "rejections.jsonl"
        self.assertTrue(rejections.exists())
        with open(rejections) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["reason"], "Contains test data")

    def test_staging_list_path_traversal_job_id(self):
        from core.tools.staging_tools import staging_list
        result = run_async(staging_list.ainvoke({"job_id": "../etc"}))
        self.assertIn("[ERROR]", result)


# ═══════════════════════════════════════════════════════════════════════════
# Audit verdict parsing
# ═══════════════════════════════════════════════════════════════════════════


class TestVerdictParsing(unittest.TestCase):
    """Test _parse_verdict from audit_agent module."""

    def test_parse_fenced_json(self):
        from core.agents.audit_agent import _parse_verdict
        text = """I reviewed the files.

```json
{
    "passed": true,
    "issues": [],
    "suggestions": ["add docstring"],
    "security_flags": [],
    "summary": "Looks good"
}
```"""
        verdict = _parse_verdict(text)
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.summary, "Looks good")
        self.assertEqual(verdict.suggestions, ["add docstring"])

    def test_parse_raw_json(self):
        from core.agents.audit_agent import _parse_verdict
        text = 'Review complete. {"passed": false, "issues": ["missing file"], "suggestions": [], "security_flags": [], "summary": "Incomplete"}'
        verdict = _parse_verdict(text)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.issues, ["missing file"])

    def test_parse_no_json(self):
        from core.agents.audit_agent import _parse_verdict
        verdict = _parse_verdict("No JSON here at all")
        self.assertFalse(verdict.passed)
        self.assertIn("did not return a valid JSON verdict", verdict.issues[0])

    def test_parse_invalid_json(self):
        from core.agents.audit_agent import _parse_verdict
        verdict = _parse_verdict("Here: {not valid json at all}")
        self.assertFalse(verdict.passed)
        self.assertIn("invalid JSON", verdict.issues[0])


# ═══════════════════════════════════════════════════════════════════════════
# Audit gate decision
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditGateDecision(unittest.TestCase):
    """Test audit_gate_decision routing logic."""

    def test_ironclaw_with_artifacts_routes_to_audit(self):
        from core.nodes.audit_gate import audit_gate_decision
        state = {
            "delegation_type": "ironclaw",
            "staging_artifacts": [
                StagingArtifact("f.py", 100, "file", "ironclaw")
            ],
            "staging_job_id": "abc123",
        }
        self.assertEqual(audit_gate_decision(state), "audit")

    def test_ironclaw_without_artifacts_skips(self):
        from core.nodes.audit_gate import audit_gate_decision
        state = {
            "delegation_type": "ironclaw",
            "staging_artifacts": [],
        }
        self.assertEqual(audit_gate_decision(state), "skip")

    def test_non_ironclaw_skips(self):
        from core.nodes.audit_gate import audit_gate_decision
        state = {
            "delegation_type": "operate",
            "staging_artifacts": [],
        }
        self.assertEqual(audit_gate_decision(state), "skip")

    def test_memory_agent_skips(self):
        from core.nodes.audit_gate import audit_gate_decision
        state = {"delegation_type": "memory", "staging_artifacts": []}
        self.assertEqual(audit_gate_decision(state), "skip")

    def test_none_delegation_skips(self):
        from core.nodes.audit_gate import audit_gate_decision
        state = {"staging_artifacts": []}
        self.assertEqual(audit_gate_decision(state), "skip")


# ═══════════════════════════════════════════════════════════════════════════
# Audit decision (pass/fail/escalate)
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditDecision(unittest.TestCase):
    """Test audit_decision routing after audit agent returns."""

    def test_passed_verdict(self):
        from core.nodes.re_dispatch import audit_decision
        state = {
            "audit_verdict": AuditVerdict(passed=True, summary="OK"),
            "review_count": 0,
            "max_reviews": 3,
        }
        self.assertEqual(audit_decision(state), "pass")

    def test_failed_verdict_under_limit(self):
        from core.nodes.re_dispatch import audit_decision
        state = {
            "audit_verdict": AuditVerdict(passed=False, issues=["bad"]),
            "review_count": 1,
            "max_reviews": 3,
        }
        self.assertEqual(audit_decision(state), "fail")

    def test_failed_verdict_at_limit(self):
        from core.nodes.re_dispatch import audit_decision
        state = {
            "audit_verdict": AuditVerdict(passed=False, issues=["bad"]),
            "review_count": 3,
            "max_reviews": 3,
        }
        self.assertEqual(audit_decision(state), "escalate")

    def test_none_verdict_passes(self):
        from core.nodes.re_dispatch import audit_decision
        state = {"audit_verdict": None, "review_count": 0, "max_reviews": 3}
        self.assertEqual(audit_decision(state), "pass")

    def test_default_max_reviews(self):
        from core.nodes.re_dispatch import audit_decision
        state = {
            "audit_verdict": AuditVerdict(passed=False, issues=["x"]),
            "review_count": 2,
        }
        # Default max_reviews is 3, so count=2 < 3 → fail
        self.assertEqual(audit_decision(state), "fail")


# ═══════════════════════════════════════════════════════════════════════════
# Re-dispatch feedback
# ═══════════════════════════════════════════════════════════════════════════


class TestReDispatch(unittest.TestCase):
    """Test re_dispatch_node feedback generation."""

    def test_builds_feedback(self):
        from core.nodes.re_dispatch import re_dispatch_node
        state = {
            "audit_verdict": AuditVerdict(
                passed=False,
                issues=["hardcoded key", "incomplete"],
                suggestions=["add tests"],
                security_flags=["credential exposure"],
                summary="Failed",
            ),
            "review_count": 0,
            "staging_job_id": "abc123",
        }
        result = run_async(re_dispatch_node(state))
        self.assertIn("audit_feedback", result)
        self.assertIn("hardcoded key", result["audit_feedback"])
        self.assertIn("credential exposure", result["audit_feedback"])
        self.assertEqual(result["review_count"], 1)
        self.assertIsNone(result["audit_verdict"])

    def test_increments_count(self):
        from core.nodes.re_dispatch import re_dispatch_node
        state = {
            "audit_verdict": AuditVerdict(passed=False, issues=["x"]),
            "review_count": 2,
        }
        result = run_async(re_dispatch_node(state))
        self.assertEqual(result["review_count"], 3)


# ═══════════════════════════════════════════════════════════════════════════
# Dispatch node staging
# ═══════════════════════════════════════════════════════════════════════════


class TestDispatchStaging(unittest.TestCase):
    """Test dispatch node staging directory creation."""

    def test_creates_staging_for_ironclaw(self):
        from core.nodes.dispatch import _create_staging_job, _scan_staging_artifacts

        with tempfile.TemporaryDirectory() as tmpdir:
            import core.nodes.dispatch as d
            orig = d.STAGING_BASE
            d.STAGING_BASE = Path(tmpdir)
            try:
                job_id, output_dir = _create_staging_job("test task")
                self.assertTrue(output_dir.exists())
                self.assertTrue((output_dir.parent / "manifest.json").exists())

                # Read manifest
                manifest = json.loads((output_dir.parent / "manifest.json").read_text())
                self.assertEqual(manifest["job_id"], job_id)
                self.assertIn("test task", manifest["task"])

                # Create test files and scan
                (output_dir / "result.py").write_text("x = 1")
                (output_dir / "debug.log").write_text("log data")
                artifacts = _scan_staging_artifacts(job_id)
                self.assertEqual(len(artifacts), 2)
                types = {a.artifact_type for a in artifacts}
                self.assertIn("file", types)
                self.assertIn("log", types)
            finally:
                d.STAGING_BASE = orig

    def test_scan_empty_directory(self):
        from core.nodes.dispatch import _scan_staging_artifacts
        artifacts = _scan_staging_artifacts("nonexistent")
        self.assertEqual(artifacts, [])


# ═══════════════════════════════════════════════════════════════════════════
# Integrate node staging cleanup
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegrateStagingCleanup(unittest.TestCase):
    """Test integrate_node clears staging state."""

    def test_clears_staging_on_audit_pass(self):
        from langchain_core.messages import AIMessage
        from core.nodes.integrate import integrate_node

        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
            "audit_verdict": AuditVerdict(passed=True, summary="All clear"),
            "staging_job_id": "abc123",
            "staging_artifacts": [
                StagingArtifact("f.py", 100, "file", "ironclaw"),
            ],
            "review_count": 0,
        }
        result = run_async(integrate_node(state))
        self.assertIsNone(result.get("staging_job_id"))
        self.assertEqual(result.get("staging_artifacts"), [])
        self.assertIsNone(result.get("audit_verdict"))
        self.assertEqual(result.get("review_count"), 0)
        self.assertIn("Audit Passed", result["messages"][0].content)

    def test_clears_staging_on_escalation(self):
        from core.nodes.integrate import integrate_node

        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
            "audit_verdict": AuditVerdict(
                passed=False,
                issues=["unresolved"],
                summary="Still broken",
            ),
            "staging_job_id": "abc123",
            "staging_artifacts": [],
            "review_count": 3,
        }
        result = run_async(integrate_node(state))
        self.assertIsNone(result.get("staging_job_id"))
        self.assertIn("Audit Failed", result["messages"][0].content)

    def test_no_staging_state_untouched(self):
        from core.nodes.integrate import integrate_node

        state = {
            "agent_result": AgentResult(agent="memory", success=True, summary="Saved"),
        }
        result = run_async(integrate_node(state))
        # No staging fields should be set
        self.assertNotIn("staging_job_id", result)
        self.assertIn("Memory Agent", result["messages"][0].content)


# ═══════════════════════════════════════════════════════════════════════════
# Router audit keywords
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterAuditKeywords(unittest.TestCase):
    """Test that audit keywords are in the delegation map."""

    def test_audit_keywords_exist(self):
        from core.nodes.router import DELEGATION_KEYWORDS
        self.assertIn("audit", DELEGATION_KEYWORDS)
        self.assertTrue(len(DELEGATION_KEYWORDS["audit"]) > 0)

    def test_audit_keyword_matches(self):
        from core.nodes.router import DELEGATION_KEYWORDS
        message = "review staging output"
        matched = False
        for keyword in DELEGATION_KEYWORDS["audit"]:
            if keyword in message.lower():
                matched = True
                break
        self.assertTrue(matched)

    def test_skill_to_delegation_audit(self):
        from core.nodes.router import _skill_ctx_to_delegation
        from core.state import SkillContext

        ctx = SkillContext(name="ironclaw-review", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "audit")

    def test_skill_to_delegation_staging_organize(self):
        """v0.0.6: staging skills route to ironclaw (execution)."""
        from core.nodes.router import _skill_ctx_to_delegation
        from core.state import SkillContext

        ctx = SkillContext(name="staging-organize", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "ironclaw")

    def test_skill_to_delegation_staging_cleanup(self):
        """v0.0.6: staging skills route to ironclaw (execution)."""
        from core.nodes.router import _skill_ctx_to_delegation
        from core.state import SkillContext

        ctx = SkillContext(name="staging-cleanup", version="1.0", description="")
        self.assertEqual(_skill_ctx_to_delegation(ctx), "ironclaw")


# ═══════════════════════════════════════════════════════════════════════════
# Manifest update lifecycle
# ═══════════════════════════════════════════════════════════════════════════


class TestManifestUpdate(unittest.TestCase):
    """Test _update_manifest helper function for status transitions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import core.nodes.dispatch as d
        self._orig_base = d.STAGING_BASE
        d.STAGING_BASE = Path(self.tmpdir)

    def tearDown(self):
        import core.nodes.dispatch as d
        d.STAGING_BASE = self._orig_base
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_job(self, task="test task"):
        from core.nodes.dispatch import _create_staging_job
        return _create_staging_job(task)

    def test_update_manifest_basic(self):
        """_update_manifest updates fields in the manifest JSON."""
        from core.nodes.dispatch import _update_manifest
        job_id, _ = self._create_job()
        _update_manifest(job_id, {"status": "agent_done"})

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "agent_done")

    def test_update_manifest_preserves_existing_fields(self):
        """Update should merge, not replace, the manifest."""
        from core.nodes.dispatch import _update_manifest
        job_id, _ = self._create_job("my task")
        _update_manifest(job_id, {"status": "agent_done"})

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "agent_done")
        self.assertEqual(manifest["job_id"], job_id)
        self.assertIn("my task", manifest["task"])

    def test_update_manifest_completed(self):
        """Manifest transitions to completed with timestamp."""
        from core.nodes.dispatch import _update_manifest
        job_id, _ = self._create_job()
        _update_manifest(job_id, {
            "status": "completed",
            "completed_at": "2026-03-03T00:00:00Z",
            "audit_passed": True,
        })

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["completed_at"], "2026-03-03T00:00:00Z")
        self.assertTrue(manifest["audit_passed"])

    def test_update_manifest_failed(self):
        """Manifest transitions to failed with issues."""
        from core.nodes.dispatch import _update_manifest
        job_id, _ = self._create_job()
        _update_manifest(job_id, {
            "status": "failed",
            "completed_at": "2026-03-03T00:00:00Z",
            "audit_passed": False,
            "issues": ["hardcoded key", "missing tests"],
        })

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertFalse(manifest["audit_passed"])
        self.assertEqual(len(manifest["issues"]), 2)

    def test_update_manifest_nonexistent_job(self):
        """Updating a nonexistent manifest should not raise."""
        from core.nodes.dispatch import _update_manifest
        # Should just log a warning, not crash
        _update_manifest("nonexistent_job_id_12345", {"status": "done"})

    def test_update_manifest_multiple_updates(self):
        """Multiple updates accumulate correctly."""
        from core.nodes.dispatch import _update_manifest
        job_id, _ = self._create_job()

        _update_manifest(job_id, {"status": "agent_done"})
        _update_manifest(job_id, {"status": "completed", "completed_at": "T1"})

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["completed_at"], "T1")
        # Original fields preserved
        self.assertEqual(manifest["job_id"], job_id)

    def test_manifest_lifecycle_full(self):
        """Full lifecycle: in_progress -> agent_done -> completed."""
        from core.nodes.dispatch import _update_manifest
        job_id, _ = self._create_job()

        # Initial state: in_progress (set by _create_staging_job)
        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "in_progress")

        # Agent completes
        _update_manifest(job_id, {"status": "agent_done"})
        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "agent_done")

        # Audit passes, integration completes
        _update_manifest(job_id, {
            "status": "completed",
            "completed_at": "2026-03-03T12:00:00Z",
            "audit_passed": True,
        })
        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertTrue(manifest["audit_passed"])


class TestIntegrateManifestUpdate(unittest.TestCase):
    """Test that integrate_node updates manifest status on disk."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import core.nodes.dispatch as d
        self._orig_base = d.STAGING_BASE
        d.STAGING_BASE = Path(self.tmpdir)

    def tearDown(self):
        import core.nodes.dispatch as d
        d.STAGING_BASE = self._orig_base
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_integrate_sets_completed_on_audit_pass(self):
        """integrate_node should set manifest status to completed."""
        from core.nodes.dispatch import _create_staging_job
        from core.nodes.integrate import integrate_node

        job_id, _ = _create_staging_job("test")
        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
            "audit_verdict": AuditVerdict(passed=True, summary="OK"),
            "staging_job_id": job_id,
            "staging_artifacts": [StagingArtifact("f.py", 100, "file", "ironclaw")],
            "review_count": 0,
        }
        run_async(integrate_node(state))

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertIn("completed_at", manifest)
        self.assertTrue(manifest.get("audit_passed"))

    def test_integrate_sets_failed_on_audit_fail(self):
        """integrate_node should set manifest status to failed."""
        from core.nodes.dispatch import _create_staging_job
        from core.nodes.integrate import integrate_node

        job_id, _ = _create_staging_job("test")
        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
            "audit_verdict": AuditVerdict(
                passed=False, issues=["bad code"], summary="Fail"
            ),
            "staging_job_id": job_id,
            "staging_artifacts": [],
            "review_count": 3,
        }
        run_async(integrate_node(state))

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("completed_at", manifest)
        self.assertFalse(manifest.get("audit_passed"))
        self.assertIn("bad code", manifest.get("issues", []))

    def test_integrate_completed_no_verdict(self):
        """integrate_node sets completed when no audit verdict (non-audited)."""
        from core.nodes.dispatch import _create_staging_job
        from core.nodes.integrate import integrate_node

        job_id, _ = _create_staging_job("test")
        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
            "audit_verdict": None,
            "staging_job_id": job_id,
            "staging_artifacts": [],
            "review_count": 0,
        }
        run_async(integrate_node(state))

        manifest = json.loads((Path(self.tmpdir) / job_id / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertIn("completed_at", manifest)


if __name__ == "__main__":
    unittest.main()
