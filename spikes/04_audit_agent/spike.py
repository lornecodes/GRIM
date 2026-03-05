"""
Spike 04 — Audit Agent
=======================
Prove that an audit agent can:
  1. Review a simulated coding session transcript (tool uses, code changes)
  2. Analyze a git diff for quality and correctness
  3. Check code against project standards (via Kronos)
  4. Produce a structured audit verdict (approve/request-changes/reject)

This spike simulates what happens after a coding agent completes a job:
the auditor gets the full transcript + diff and decides whether to merge.

Run:
    cd GRIM/spikes/04_audit_agent
    python spike.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    tool,
    create_sdk_mcp_server,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

os.environ.pop("CLAUDECODE", None)

# ── Simulated session transcript ──────────────────────────────────

GOOD_SESSION_TRANSCRIPT = """
## Coding Agent Session Transcript
**Job:** Implement fizzbuzz function with tests
**Agent:** coding-agent-01
**Duration:** 45 seconds
**Turns:** 6

### Turn 1 — Agent reads task
Agent received task: "Write a fizzbuzz function with comprehensive tests"

### Turn 2 — Agent checks project standards
[ToolUse] get_project_context({"topic": "testing"})
[ToolResult] "Use pytest, descriptive names, >=2 test cases per function"

[ToolUse] get_project_context({"topic": "style"})
[ToolResult] "Type hints on all functions, docstrings on public functions, snake_case"

### Turn 3 — Agent writes code
[ToolUse] Write({"file_path": "/workspace/fizzbuzz.py", "content": "..."})
Created fizzbuzz.py with:
- fizzbuzz(n: int) -> str function
- Type hints on signature
- Docstring explaining behavior
- Handles: multiples of 15 (fizzbuzz), 3 (fizz), 5 (buzz), else str(n)

### Turn 4 — Agent writes tests
[ToolUse] Write({"file_path": "/workspace/test_fizzbuzz.py", "content": "..."})
Created test_fizzbuzz.py with 8 tests:
- test_fizzbuzz_15, test_fizzbuzz_30 (multiples of both)
- test_fizz_3, test_fizz_9 (multiples of 3 only)
- test_buzz_5, test_buzz_10 (multiples of 5 only)
- test_number_1, test_number_7 (neither)

### Turn 5 — Agent runs tests
[ToolUse] Bash({"command": "python -m pytest test_fizzbuzz.py -v"})
[ToolResult] "8 passed in 0.03s"

### Turn 6 — Agent reports completion
All tests pass. Code follows project standards (type hints, docstrings, snake_case).
"""

GOOD_SESSION_DIFF = """
diff --git a/fizzbuzz.py b/fizzbuzz.py
new file mode 100644
--- /dev/null
+++ b/fizzbuzz.py
@@ -0,0 +1,14 @@
+def fizzbuzz(n: int) -> str:
+    \"\"\"Return fizz, buzz, fizzbuzz, or the number as a string.
+
+    Args:
+        n: A positive integer.
+
+    Returns:
+        'fizzbuzz' if divisible by both 3 and 5,
+        'fizz' if divisible by 3, 'buzz' if divisible by 5,
+        otherwise str(n).
+    \"\"\"
+    if n % 15 == 0:
+        return "fizzbuzz"
+    if n % 3 == 0:
+        return "fizz"
+    if n % 5 == 0:
+        return "buzz"
+    return str(n)

diff --git a/test_fizzbuzz.py b/test_fizzbuzz.py
new file mode 100644
--- /dev/null
+++ b/test_fizzbuzz.py
@@ -0,0 +1,28 @@
+from fizzbuzz import fizzbuzz
+
+def test_fizzbuzz_15():
+    assert fizzbuzz(15) == "fizzbuzz"
+
+def test_fizzbuzz_30():
+    assert fizzbuzz(30) == "fizzbuzz"
+
+def test_fizz_3():
+    assert fizzbuzz(3) == "fizz"
+
+def test_fizz_9():
+    assert fizzbuzz(9) == "fizz"
+
+def test_buzz_5():
+    assert fizzbuzz(5) == "buzz"
+
+def test_buzz_10():
+    assert fizzbuzz(10) == "buzz"
+
+def test_number_1():
+    assert fizzbuzz(1) == "1"
+
+def test_number_7():
+    assert fizzbuzz(7) == "7"
"""

BAD_SESSION_TRANSCRIPT = """
## Coding Agent Session Transcript
**Job:** Add user authentication endpoint
**Agent:** coding-agent-02
**Duration:** 30 seconds
**Turns:** 3

### Turn 1 — Agent writes code immediately (NO standard check)
[ToolUse] Write({"file_path": "/workspace/auth.py", "content": "..."})
Created auth.py with:
- login(username, password) function (NO type hints)
- No docstring
- Hardcoded secret key: SECRET_KEY = "my-super-secret-key-123"
- No password hashing — stores plaintext passwords
- SQL query uses string formatting (SQL injection vulnerable):
  query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"

### Turn 2 — Agent runs (no tests written)
[ToolUse] Bash({"command": "python -c 'from auth import login; print(login(\"admin\", \"pass\"))'")
[ToolResult] "Login successful"

### Turn 3 — Agent reports done
"Authentication endpoint works. User can log in."
"""

BAD_SESSION_DIFF = """
diff --git a/auth.py b/auth.py
new file mode 100644
--- /dev/null
+++ b/auth.py
@@ -0,0 +1,15 @@
+import sqlite3
+
+SECRET_KEY = "my-super-secret-key-123"
+
+def login(username, password):
+    conn = sqlite3.connect("users.db")
+    cursor = conn.cursor()
+    query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
+    cursor.execute(query)
+    user = cursor.fetchone()
+    conn.close()
+    if user:
+        return "Login successful"
+    return "Login failed"
"""


# ── Audit submission tool ─────────────────────────────────────────

audit_verdicts = []

@tool(
    name="submit_audit_verdict",
    description="Submit the audit verdict for a coding session. Verdict must include: decision (approve/request-changes/reject), summary, issues list, and recommendations.",
    input_schema={
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["approve", "request-changes", "reject"],
                "description": "The audit decision"
            },
            "summary": {
                "type": "string",
                "description": "Brief summary of findings"
            },
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                        "description": {"type": "string"},
                        "location": {"type": "string"}
                    },
                    "required": ["severity", "description"]
                },
                "description": "List of issues found"
            },
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Recommendations for improvement"
            }
        },
        "required": ["decision", "summary", "issues"]
    },
)
async def submit_audit_verdict(args):
    """Capture the audit verdict."""
    audit_verdicts.append(args)
    return {"content": [{"type": "text", "text": f"Verdict recorded: {args['decision']}"}]}


audit_server = create_sdk_mcp_server(
    name="audit",
    version="0.1.0",
    tools=[submit_audit_verdict],
)


# ── Test Cases ────────────────────────────────────────────────────

AUDIT_SYSTEM_PROMPT = (
    "You are an audit agent for the Dawn Field Institute. "
    "You review coding agent session transcripts and diffs to ensure quality.\n\n"
    "Your review process:\n"
    "1. Read the session transcript to understand what the agent did\n"
    "2. Analyze the diff for code quality, security, and correctness\n"
    "3. Check against project standards (type hints, docstrings, tests)\n"
    "4. Submit a structured verdict using submit_audit_verdict\n\n"
    "Standards to check:\n"
    "- Type hints on all function signatures\n"
    "- Docstrings on public functions\n"
    "- Tests for all public functions (>=2 test cases each)\n"
    "- No hardcoded secrets or credentials\n"
    "- No SQL injection or security vulnerabilities\n"
    "- Agent should have checked project standards before coding\n\n"
    "Verdicts:\n"
    "- approve: Code meets all standards, tests pass, no issues\n"
    "- request-changes: Minor issues that can be fixed\n"
    "- reject: Critical security issues, missing tests, or fundamentally broken"
)


async def test_approve_good_code():
    """Test 1: Auditor should approve well-written code."""
    print("\n" + "=" * 60)
    print("TEST 1: Audit good code (should approve)")
    print("=" * 60)

    audit_verdicts.clear()

    options = ClaudeAgentOptions(
        system_prompt=AUDIT_SYSTEM_PROMPT,
        mcp_servers={"audit": audit_server},
        allowed_tools=["mcp__audit__submit_audit_verdict"],
        permission_mode="bypassPermissions",
        max_turns=3,
    )

    messages = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Review this coding session and submit your verdict.\n\n"
            f"## Session Transcript\n{GOOD_SESSION_TRANSCRIPT}\n\n"
            f"## Git Diff\n```\n{GOOD_SESSION_DIFF}\n```"
        )
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and len(block.text) > 10:
                        print(f"  [Auditor] {block.text[:200]}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"  [ToolUse] {block.name}({json.dumps(block.input)[:100]})")
            elif isinstance(msg, ResultMessage):
                print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.6f}")

    result = next((m for m in messages if isinstance(m, ResultMessage)), None)
    assert result is not None, "No ResultMessage"

    assert len(audit_verdicts) >= 1, "No verdict submitted"
    verdict = audit_verdicts[-1]
    print(f"\n  Verdict: {verdict['decision']}")
    print(f"  Summary: {verdict['summary'][:100]}")
    print(f"  Issues: {len(verdict.get('issues', []))}")

    assert verdict["decision"] == "approve", f"Expected approve, got {verdict['decision']}"
    print("  [PASS] Auditor correctly approved good code!")
    return True


async def test_reject_bad_code():
    """Test 2: Auditor should reject insecure code."""
    print("\n" + "=" * 60)
    print("TEST 2: Audit bad code (should reject)")
    print("=" * 60)

    audit_verdicts.clear()

    options = ClaudeAgentOptions(
        system_prompt=AUDIT_SYSTEM_PROMPT,
        mcp_servers={"audit": audit_server},
        allowed_tools=["mcp__audit__submit_audit_verdict"],
        permission_mode="bypassPermissions",
        max_turns=3,
    )

    messages = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Review this coding session and submit your verdict.\n\n"
            f"## Session Transcript\n{BAD_SESSION_TRANSCRIPT}\n\n"
            f"## Git Diff\n```\n{BAD_SESSION_DIFF}\n```"
        )
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and len(block.text) > 10:
                        print(f"  [Auditor] {block.text[:200]}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"  [ToolUse] {block.name}({json.dumps(block.input)[:100]})")
            elif isinstance(msg, ResultMessage):
                print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.6f}")

    result = next((m for m in messages if isinstance(m, ResultMessage)), None)
    assert result is not None, "No ResultMessage"

    assert len(audit_verdicts) >= 1, "No verdict submitted"
    verdict = audit_verdicts[-1]
    print(f"\n  Verdict: {verdict['decision']}")
    print(f"  Summary: {verdict['summary'][:100]}")
    issues = verdict.get("issues", [])
    print(f"  Issues found: {len(issues)}")
    for issue in issues:
        print(f"    [{issue['severity']}] {issue['description'][:80]}")

    assert verdict["decision"] in ("reject", "request-changes"), \
        f"Expected reject or request-changes, got {verdict['decision']}"

    # Should find at least: SQL injection, hardcoded secret, no type hints, no tests
    critical_issues = [i for i in issues if i["severity"] == "critical"]
    assert len(critical_issues) >= 1, f"Expected at least 1 critical issue, found {len(critical_issues)}"

    print("  [PASS] Auditor correctly rejected insecure code!")
    return True


# ── Main ──────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("SPIKE 04 -- Audit Agent")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    results = {}
    tests = [
        ("approve_good_code", test_approve_good_code),
        ("reject_bad_code", test_reject_bad_code),
    ]

    for name, test_fn in tests:
        try:
            results[name] = await test_fn()
        except Exception as e:
            print(f"\n  [FAIL] {name}: {e}")
            results[name] = False
            import traceback
            traceback.print_exc()

    # Summary
    print("\n" + "=" * 60)
    print("SPIKE 04 -- RESULTS")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} tests passed")

    if passed == total:
        print("\n  SPIKE 04 PROVEN -- Audit agent can review sessions and enforce quality!")
    else:
        print("\n  SPIKE 04 INCOMPLETE -- some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
