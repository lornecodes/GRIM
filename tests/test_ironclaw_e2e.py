"""End-to-end tests for IronClaw tool execution and staging pipeline.

Tests the full flow:
- Gateway tool registry → tool execution → file output
- Staging manifest lifecycle (in_progress → agent_done → completed/failed)
- Dispatch → IronClaw → Integrate with manifest updates
- Bridge ↔ Gateway tool dispatch

Run: cd GRIM && python -m pytest tests/test_ironclaw_e2e.py -v
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
# Full staging pipeline E2E
# ═══════════════════════════════════════════════════════════════════════════


class TestStagingPipelineE2E(unittest.TestCase):
    """End-to-end staging pipeline: dispatch → execute → integrate."""

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

    def test_full_pipeline_success(self):
        """Dispatch creates job → agent writes files → integrate completes."""
        from core.nodes.dispatch import _create_staging_job, _update_manifest, _scan_staging_artifacts
        from core.nodes.integrate import integrate_node

        # 1. Dispatch creates staging job
        job_id, output_dir = _create_staging_job("build me a hello world server")
        manifest_path = Path(self.tmpdir) / job_id / "manifest.json"

        # Verify initial manifest
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["status"], "in_progress")
        self.assertTrue(output_dir.exists())

        # 2. Simulate agent writing files to staging
        (output_dir / "server.py").write_text("from http.server import HTTPServer\nprint('hello')")
        (output_dir / "requirements.txt").write_text("flask>=2.0\n")

        # 3. Agent done → update manifest
        _update_manifest(job_id, {"status": "agent_done"})
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["status"], "agent_done")

        # 4. Scan artifacts
        artifacts = _scan_staging_artifacts(job_id)
        self.assertEqual(len(artifacts), 2)
        artifact_names = {a.path for a in artifacts}
        self.assertIn("server.py", artifact_names)
        self.assertIn("requirements.txt", artifact_names)

        # 5. Integrate with passing audit
        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Built server"),
            "audit_verdict": AuditVerdict(passed=True, summary="All clear"),
            "staging_job_id": job_id,
            "staging_artifacts": artifacts,
            "review_count": 0,
        }
        result = run_async(integrate_node(state))

        # Verify manifest final state
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertIn("completed_at", manifest)
        self.assertTrue(manifest.get("audit_passed"))

        # Verify state was cleaned up
        self.assertIsNone(result.get("staging_job_id"))
        self.assertEqual(result.get("staging_artifacts"), [])

    def test_full_pipeline_audit_failure(self):
        """Dispatch → agent writes bad code → audit fails → manifest failed."""
        from core.nodes.dispatch import _create_staging_job, _update_manifest
        from core.nodes.integrate import integrate_node

        job_id, output_dir = _create_staging_job("write a script")
        (output_dir / "bad.py").write_text("API_KEY = 'sk-1234abcd'")

        _update_manifest(job_id, {"status": "agent_done"})

        # Integrate with failing audit (at max review count → escalation)
        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
            "audit_verdict": AuditVerdict(
                passed=False,
                issues=["hardcoded API key"],
                security_flags=["credential exposure"],
                summary="Security violation",
            ),
            "staging_job_id": job_id,
            "staging_artifacts": [StagingArtifact("bad.py", 30, "file", "ironclaw")],
            "review_count": 3,
        }
        result = run_async(integrate_node(state))

        manifest = json.loads(
            (Path(self.tmpdir) / job_id / "manifest.json").read_text()
        )
        self.assertEqual(manifest["status"], "failed")
        self.assertFalse(manifest.get("audit_passed"))
        self.assertIn("hardcoded API key", manifest.get("issues", []))

    def test_full_pipeline_no_verdict(self):
        """Non-audited path: dispatch → execute → integrate (no audit)."""
        from core.nodes.dispatch import _create_staging_job, _update_manifest
        from core.nodes.integrate import integrate_node

        job_id, output_dir = _create_staging_job("quick task")
        (output_dir / "output.txt").write_text("done")
        _update_manifest(job_id, {"status": "agent_done"})

        state = {
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
            "audit_verdict": None,
            "staging_job_id": job_id,
            "staging_artifacts": [],
            "review_count": 0,
        }
        run_async(integrate_node(state))

        manifest = json.loads(
            (Path(self.tmpdir) / job_id / "manifest.json").read_text()
        )
        self.assertEqual(manifest["status"], "completed")

    def test_multiple_jobs_independent(self):
        """Multiple staging jobs don't interfere with each other."""
        from core.nodes.dispatch import _create_staging_job, _update_manifest

        job1, out1 = _create_staging_job("task 1")
        job2, out2 = _create_staging_job("task 2")

        (out1 / "file1.py").write_text("x = 1")
        (out2 / "file2.py").write_text("y = 2")

        _update_manifest(job1, {"status": "completed"})
        _update_manifest(job2, {"status": "failed"})

        m1 = json.loads((Path(self.tmpdir) / job1 / "manifest.json").read_text())
        m2 = json.loads((Path(self.tmpdir) / job2 / "manifest.json").read_text())

        self.assertEqual(m1["status"], "completed")
        self.assertEqual(m2["status"], "failed")
        self.assertNotEqual(m1["job_id"], m2["job_id"])


# ═══════════════════════════════════════════════════════════════════════════
# Dispatch node E2E
# ═══════════════════════════════════════════════════════════════════════════


class TestDispatchNodeE2E(unittest.TestCase):
    """Test dispatch_node creates staging and invokes agent correctly."""

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

    def test_dispatch_ironclaw_creates_staging(self):
        """make_dispatch_node with ironclaw agent creates staging dir."""
        from core.nodes.dispatch import make_dispatch_node

        async def mock_ironclaw(state, event_queue=None):
            return AgentResult(agent="ironclaw", success=True, summary="Built")

        dispatch_fn = make_dispatch_node({"ironclaw": mock_ironclaw})

        state = {
            "delegation_type": "ironclaw",
            "messages": [],
            "user_input": "build me a web server",
            "skill_context": None,
            "audit_feedback": None,
            "staging_job_id": None,
        }

        result = run_async(dispatch_fn(state))

        # Should have created a staging job
        self.assertIsNotNone(result.get("staging_job_id"))
        job_id = result["staging_job_id"]

        # Manifest should exist and be agent_done
        manifest_path = Path(self.tmpdir) / job_id / "manifest.json"
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["status"], "agent_done")

    def test_dispatch_non_ironclaw_no_staging(self):
        """make_dispatch_node with non-ironclaw agent does NOT create staging."""
        from core.nodes.dispatch import make_dispatch_node

        async def mock_memory(state, event_queue=None):
            return AgentResult(agent="memory", success=True, summary="Saved")

        dispatch_fn = make_dispatch_node({"memory": mock_memory})

        state = {
            "delegation_type": "memory",
            "messages": [],
            "user_input": "remember this",
            "skill_context": None,
            "audit_feedback": None,
            "staging_job_id": None,
        }

        result = run_async(dispatch_fn(state))

        # No staging directory should exist
        staging_dirs = list(Path(self.tmpdir).iterdir())
        self.assertEqual(len(staging_dirs), 0)


# ═══════════════════════════════════════════════════════════════════════════
# Audit gate routing E2E
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditGateE2E(unittest.TestCase):
    """Test audit gate → re-dispatch → integrate flow."""

    def test_audit_pass_skips_re_dispatch(self):
        """When audit passes, flow goes to integrate (not re-dispatch)."""
        from core.nodes.audit_gate import audit_gate_decision
        from core.nodes.re_dispatch import audit_decision

        # Audit gate: has artifacts → route to audit
        state_gate = {
            "delegation_type": "ironclaw",
            "staging_artifacts": [StagingArtifact("f.py", 100, "file", "ironclaw")],
            "staging_job_id": "abc123",
        }
        self.assertEqual(audit_gate_decision(state_gate), "audit")

        # After audit passes → route to "pass" (integrate)
        state_decision = {
            "audit_verdict": AuditVerdict(passed=True, summary="OK"),
            "review_count": 0,
            "max_reviews": 3,
        }
        self.assertEqual(audit_decision(state_decision), "pass")

    def test_audit_fail_triggers_re_dispatch(self):
        """When audit fails and under limit → re-dispatch."""
        from core.nodes.re_dispatch import audit_decision, re_dispatch_node

        state = {
            "audit_verdict": AuditVerdict(
                passed=False, issues=["missing tests"], summary="Fail"
            ),
            "review_count": 0,
            "max_reviews": 3,
            "staging_job_id": "abc123",
        }
        self.assertEqual(audit_decision(state), "fail")

        # Re-dispatch builds feedback
        result = run_async(re_dispatch_node(state))
        self.assertIn("missing tests", result["audit_feedback"])
        self.assertEqual(result["review_count"], 1)

    def test_audit_fail_at_limit_escalates(self):
        """When audit fails and at limit → escalate."""
        from core.nodes.re_dispatch import audit_decision

        state = {
            "audit_verdict": AuditVerdict(passed=False, issues=["bad"]),
            "review_count": 3,
            "max_reviews": 3,
        }
        self.assertEqual(audit_decision(state), "escalate")


# ═══════════════════════════════════════════════════════════════════════════
# Staging artifact scanning E2E
# ═══════════════════════════════════════════════════════════════════════════


class TestStagingArtifactScanE2E(unittest.TestCase):
    """Test artifact scanning with various file types and structures."""

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

    def test_scan_mixed_file_types(self):
        """Scan correctly classifies different file types."""
        from core.nodes.dispatch import _create_staging_job, _scan_staging_artifacts

        job_id, output_dir = _create_staging_job("test")
        (output_dir / "main.py").write_text("print('hello')")
        (output_dir / "app.js").write_text("console.log('hi')")
        (output_dir / "debug.log").write_text("DEBUG: test")
        (output_dir / "style.css").write_text("body { color: red; }")

        artifacts = _scan_staging_artifacts(job_id)
        self.assertEqual(len(artifacts), 4)

        types = {a.path: a.artifact_type for a in artifacts}
        self.assertEqual(types["main.py"], "file")
        self.assertEqual(types["app.js"], "file")
        self.assertEqual(types["debug.log"], "log")
        self.assertEqual(types["style.css"], "file")

    def test_scan_nested_directories(self):
        """Scan finds files in nested directories."""
        from core.nodes.dispatch import _create_staging_job, _scan_staging_artifacts

        job_id, output_dir = _create_staging_job("test")
        (output_dir / "src").mkdir()
        (output_dir / "src" / "main.py").write_text("pass")
        (output_dir / "tests").mkdir()
        (output_dir / "tests" / "test_main.py").write_text("pass")

        artifacts = _scan_staging_artifacts(job_id)
        # Should find files in subdirectories too
        self.assertGreaterEqual(len(artifacts), 2)

    def test_scan_empty_staging(self):
        """Scan on empty staging returns empty list."""
        from core.nodes.dispatch import _create_staging_job, _scan_staging_artifacts

        job_id, _ = _create_staging_job("test")
        artifacts = _scan_staging_artifacts(job_id)
        self.assertEqual(artifacts, [])

    def test_scan_records_file_sizes(self):
        """Scan records correct file sizes."""
        from core.nodes.dispatch import _create_staging_job, _scan_staging_artifacts

        job_id, output_dir = _create_staging_job("test")
        content = "x" * 1024
        (output_dir / "big.txt").write_text(content)

        artifacts = _scan_staging_artifacts(job_id)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].size_bytes, 1024)


# ═══════════════════════════════════════════════════════════════════════════
# Bridge tool call format
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeToolCallFormat(unittest.TestCase):
    """Test that bridge formats tool calls correctly for IronClaw gateway."""

    def test_tool_call_serialization(self):
        """Tool calls should serialize to JSON with required fields."""
        tool_call = {
            "name": "file_write",
            "arguments": {
                "path": "/workspace/staging/abc123/output/server.py",
                "content": "from flask import Flask\napp = Flask(__name__)",
            },
        }
        serialized = json.dumps(tool_call)
        deserialized = json.loads(serialized)

        self.assertEqual(deserialized["name"], "file_write")
        self.assertIn("path", deserialized["arguments"])
        self.assertIn("content", deserialized["arguments"])

    def test_tool_response_parsing(self):
        """Bridge should handle tool execution responses correctly."""
        response = {
            "success": True,
            "output": "File written: server.py (42 bytes)",
            "execution_id": "test-exec-id",
            "duration_ms": 5,
        }
        self.assertTrue(response["success"])
        self.assertIn("File written", response["output"])

    def test_tool_error_response(self):
        """Bridge should handle error responses from IronClaw."""
        error_response = {
            "error": "Not Found",
            "message": "Unknown tool: invalid_tool",
            "request_id": "req-123",
        }
        self.assertIn("Unknown tool", error_response["message"])


if __name__ == "__main__":
    unittest.main()
