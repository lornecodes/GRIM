"""
Spike 02 — Coding Agent
========================
Prove that a Claude Code agent can:
  1. Write a Python file using native tools (Write/Edit)
  2. Run tests using Bash
  3. Read test output and iterate on failures
  4. Use a custom MCP tool for project context (simulated Kronos)
  5. Work in an isolated directory (simulated container workspace)

This spike creates a temp workspace, gives the agent a coding task,
and verifies it produces working code with passing tests.

Run:
    cd GRIM/spikes/02_coding_agent
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
    UserMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

os.environ.pop("CLAUDECODE", None)

# ── Simulated Kronos MCP Tool ────────────────────────────────────

@tool(
    name="get_project_context",
    description="Get project context and coding standards for the current task. Returns style guide, patterns, and relevant existing code.",
    input_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "What context to retrieve (e.g. 'testing', 'style', 'patterns')"}
        },
        "required": ["topic"]
    },
)
async def get_project_context(args):
    """Simulated Kronos lookup — provides coding standards."""
    topic = args["topic"].lower()
    contexts = {
        "testing": (
            "Testing standards:\n"
            "- Use pytest with descriptive test names\n"
            "- Each function should have at least 2 test cases (happy path + edge case)\n"
            "- Use assert statements, not unittest-style\n"
            "- Test file should be named test_<module>.py"
        ),
        "style": (
            "Code style:\n"
            "- Type hints on all function signatures\n"
            "- Docstrings on public functions\n"
            "- snake_case for functions and variables\n"
            "- No global mutable state"
        ),
        "patterns": (
            "Common patterns:\n"
            "- Use dataclasses for structured data\n"
            "- Raise ValueError for invalid inputs\n"
            "- Return None instead of raising for not-found cases\n"
            "- Use pathlib.Path instead of os.path"
        ),
    }
    result = contexts.get(topic, f"No context found for topic '{topic}'. Available: {list(contexts.keys())}")
    return {"content": [{"type": "text", "text": result}]}


context_server = create_sdk_mcp_server(
    name="project-context",
    version="0.1.0",
    tools=[get_project_context],
)


# ── Test Cases ────────────────────────────────────────────────────

async def test_write_and_test_code():
    """Test 1: Agent writes a module + tests, runs them, iterates until green."""
    print("\n" + "=" * 60)
    print("TEST 1: Write code + tests, run until green")
    print("=" * 60)

    # Create isolated workspace
    workspace = Path(tempfile.mkdtemp(prefix="spike02_"))
    print(f"  Workspace: {workspace}")

    try:
        options = ClaudeAgentOptions(
            system_prompt=(
                "You are a coding agent for the Dawn Field Institute. "
                "You write Python code, tests, and iterate until all tests pass.\n\n"
                "TASK: Create a module called `calculator.py` with these functions:\n"
                "  - add(a: float, b: float) -> float\n"
                "  - subtract(a: float, b: float) -> float\n"
                "  - multiply(a: float, b: float) -> float\n"
                "  - divide(a: float, b: float) -> float (raise ValueError on division by zero)\n\n"
                "Then create `test_calculator.py` with pytest tests covering all functions "
                "including edge cases (division by zero, negative numbers, zero).\n\n"
                "Finally, run the tests with `python -m pytest test_calculator.py -v` and "
                "fix any failures. When all tests pass, say DONE.\n\n"
                "First, use the get_project_context tool to check testing and style standards.\n\n"
                f"IMPORTANT: Create all files in this directory: {workspace}\n"
                "Use absolute paths for all file operations."
            ),
            cwd=str(workspace),
            mcp_servers={"context": context_server},
            allowed_tools=[
                "Read", "Write", "Edit", "Bash", "Grep", "Glob",
                "mcp__context__get_project_context",
            ],
            permission_mode="bypassPermissions",
            max_turns=15,
        )

        messages = []
        tool_uses = []
        text_output = []

        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                "Write the calculator module, tests, and run them. "
                "Check project context first for standards."
            )
            async for msg in client.receive_response():
                messages.append(msg)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_output.append(block.text)
                            if len(block.text) > 10:
                                print(f"  [Agent] {block.text[:150]}")
                        elif isinstance(block, ToolUseBlock):
                            tool_uses.append(block.name)
                            input_str = json.dumps(block.input)[:80]
                            print(f"  [ToolUse] {block.name}({input_str})")
                elif isinstance(msg, ResultMessage):
                    print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.6f}")

        result = next((m for m in messages if isinstance(m, ResultMessage)), None)
        assert result is not None, "No ResultMessage received"

        # Verify outputs
        calc_file = workspace / "calculator.py"
        test_file = workspace / "test_calculator.py"

        print(f"\n  Files created:")
        print(f"    calculator.py: {'EXISTS' if calc_file.exists() else 'MISSING'}")
        print(f"    test_calculator.py: {'EXISTS' if test_file.exists() else 'MISSING'}")
        print(f"  Tools used: {tool_uses}")
        print(f"  Total cost: ${result.total_cost_usd or 0:.6f}")
        print(f"  Total turns: {result.num_turns}")

        assert calc_file.exists(), "calculator.py was not created"
        assert test_file.exists(), "test_calculator.py was not created"

        # Verify the code actually works by running tests ourselves
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"\n  Independent test run:")
        print(f"    Exit code: {proc.returncode}")
        if proc.stdout:
            # Show last few lines (test summary)
            lines = proc.stdout.strip().split("\n")
            for line in lines[-5:]:
                print(f"    {line}")

        assert proc.returncode == 0, f"Tests failed independently:\n{proc.stdout}\n{proc.stderr}"

        # Check that context tool was used
        context_uses = [t for t in tool_uses if "context" in t]
        print(f"\n  Context tool used: {len(context_uses)} times")

        # Check that Bash was used (for running tests)
        bash_uses = [t for t in tool_uses if t == "Bash"]
        print(f"  Bash tool used: {len(bash_uses)} times")

        print("  [PASS] Coding agent wrote working code with passing tests!")
        return True

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


async def test_iterate_on_failure():
    """Test 2: Agent gets a broken test and must fix the code."""
    print("\n" + "=" * 60)
    print("TEST 2: Fix failing code (iterate on test failure)")
    print("=" * 60)

    workspace = Path(tempfile.mkdtemp(prefix="spike02_fix_"))
    print(f"  Workspace: {workspace}")

    try:
        # Pre-create a buggy module and a test that exposes the bug
        (workspace / "fizzbuzz.py").write_text(
            'def fizzbuzz(n: int) -> str:\n'
            '    """Return fizz, buzz, fizzbuzz, or the number as string."""\n'
            '    if n % 3 == 0 and n % 5 == 0:\n'
            '        return "fizzbuzz"\n'
            '    elif n % 3 == 0:\n'
            '        return "fizz"\n'
            '    elif n % 5 == 0:\n'
            '        return "buzz"\n'
            '    else:\n'
            '        return n  # BUG: should be str(n)\n',
            encoding="utf-8",
        )
        (workspace / "test_fizzbuzz.py").write_text(
            'from fizzbuzz import fizzbuzz\n\n'
            'def test_fizzbuzz_15():\n'
            '    assert fizzbuzz(15) == "fizzbuzz"\n\n'
            'def test_fizz_3():\n'
            '    assert fizzbuzz(3) == "fizz"\n\n'
            'def test_buzz_5():\n'
            '    assert fizzbuzz(5) == "buzz"\n\n'
            'def test_number_7():\n'
            '    assert fizzbuzz(7) == "7"  # This will fail on the bug\n\n'
            'def test_number_1():\n'
            '    assert fizzbuzz(1) == "1"  # This will also fail\n',
            encoding="utf-8",
        )

        options = ClaudeAgentOptions(
            system_prompt=(
                "You are a coding agent. There is a bug in fizzbuzz.py that causes "
                "test failures. Your task:\n"
                "1. Read fizzbuzz.py and test_fizzbuzz.py\n"
                "2. Run the tests to see what fails\n"
                "3. Fix the bug in fizzbuzz.py\n"
                "4. Run tests again to verify they pass\n"
                "5. Say DONE when all tests pass\n\n"
                f"Working directory: {workspace}\n"
                "Use absolute paths for all file operations."
            ),
            cwd=str(workspace),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
            permission_mode="bypassPermissions",
            max_turns=10,
        )

        messages = []
        tool_uses = []

        async with ClaudeSDKClient(options=options) as client:
            await client.query("Run the tests, find the bug, fix it, and verify.")
            async for msg in client.receive_response():
                messages.append(msg)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and len(block.text) > 10:
                            print(f"  [Agent] {block.text[:150]}")
                        elif isinstance(block, ToolUseBlock):
                            tool_uses.append(block.name)
                            print(f"  [ToolUse] {block.name}")
                elif isinstance(msg, ResultMessage):
                    print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.6f}")

        result = next((m for m in messages if isinstance(m, ResultMessage)), None)
        assert result is not None, "No ResultMessage received"

        # Verify the fix
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(workspace / "test_fizzbuzz.py"), "-v"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"\n  Independent test run:")
        print(f"    Exit code: {proc.returncode}")
        lines = proc.stdout.strip().split("\n")
        for line in lines[-5:]:
            print(f"    {line}")

        assert proc.returncode == 0, f"Tests still failing after agent fix:\n{proc.stdout}"

        # Verify the actual fix
        fixed_code = (workspace / "fizzbuzz.py").read_text(encoding="utf-8")
        assert "str(n)" in fixed_code, f"Expected str(n) in fixed code, got:\n{fixed_code}"

        print(f"\n  Tools used: {tool_uses}")
        print(f"  Total cost: ${result.total_cost_usd or 0:.6f}")

        # Check Edit was used (not a full rewrite)
        edit_uses = [t for t in tool_uses if t == "Edit"]
        print(f"  Edit tool used: {len(edit_uses)} times")

        print("  [PASS] Agent found and fixed the bug!")
        return True

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("SPIKE 02 -- Coding Agent")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    results = {}
    tests = [
        ("write_and_test", test_write_and_test_code),
        ("iterate_on_failure", test_iterate_on_failure),
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
    print("SPIKE 02 -- RESULTS")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} tests passed")

    if passed == total:
        print("\n  SPIKE 02 PROVEN -- Coding agent can write, test, and iterate!")
    else:
        print("\n  SPIKE 02 INCOMPLETE -- some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
