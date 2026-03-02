"""Tests for keyword-based delegation routing."""
import pytest
from core.nodes.keyword_router import (
    DELEGATION_KEYWORDS,
    match_keywords,
    match_action_intent,
    is_follow_up,
)


class TestMatchKeywords:
    """Test keyword-based delegation matching."""

    @pytest.mark.parametrize("msg,expected", [
        # Memory delegation
        ("remember this concept", "memory"),
        ("capture this idea", "memory"),
        ("save this to vault", "memory"),
        ("promote this fdo", "memory"),
        ("organize vault entries", "memory"),
        ("store this information", "memory"),
        ("add to vault please", "memory"),
        ("create an fdo for this", "memory"),
        # Task management (memory delegation — write operations)
        ("create a story for auth", "memory"),
        ("create a task for bug fix", "memory"),
        ("move to active column", "memory"),
        ("move to in progress", "memory"),
        ("sync calendar now", "memory"),
        ("add calendar event", "memory"),
        # NOTE: Planning keywords removed from delegation — planning is now a
        # graph-level branch handled by graph_router.py, not keyword_router.
        # Code operations → IronClaw (v0.0.6 — no more code agent)
        ("write code for the parser", "ironclaw"),
        ("implement the new feature", "ironclaw"),
        ("refactor this function", "ironclaw"),
        ("fix this code please", "ironclaw"),
        ("add a test for it", "ironclaw"),
        ("write a function to parse", "ironclaw"),
        ("debug this issue", "ironclaw"),
        # Shell/execution → IronClaw (v0.0.6 — moved from operate)
        ("run command ls -la", "ironclaw"),
        ("shell out to bash", "ironclaw"),
        ("deploy the application", "ironclaw"),
        ("what is my ip address", "ironclaw"),
        ("whoami on this machine", "ironclaw"),
        ("curl the api endpoint", "ironclaw"),
        ("git push to origin", "ironclaw"),
        ("commit these changes", "ironclaw"),
        # Research delegation
        ("analyze this paper", "research"),
        ("deep dive into topology", "research"),
        ("ingest this document", "research"),
        ("investigate the anomaly", "research"),
        ("summarize this paper", "research"),
        ("research this topic", "research"),
        # IronClaw delegation — direct references
        ("use ironclaw for this", "ironclaw"),
        ("ask iron claw to scan", "ironclaw"),
        ("dispatch agent workflow", "ironclaw"),
        ("run a security scan on this", "ironclaw"),
        ("do a security analysis", "ironclaw"),
        ("scan for vulnerabilities", "ironclaw"),
        ("security audit of the code", "ironclaw"),
        ("analyze the container security", "ironclaw"),
        # IronClaw delegation — sandboxed execution
        ("run sandboxed command", "ironclaw"),
        ("execute safely in sandbox", "ironclaw"),
        ("run in sandbox mode", "ironclaw"),
        ("isolated shell execution", "ironclaw"),
        # Operate delegation (narrowed to git reads + file reads)
        ("git status", "operate"),
        ("git log please", "operate"),
        ("git diff main", "operate"),
        ("list files in the directory", "operate"),
        ("read the file contents", "operate"),
        ("check the status of service", "operate"),
        # Audit delegation
        ("review staging output", "audit"),
        ("audit the files", "audit"),
        ("check staged changes", "audit"),
    ])
    def test_keyword_matches(self, msg, expected):
        assert match_keywords(msg.lower()) == expected

    def test_no_match_for_casual(self):
        assert match_keywords("how are you today") is None

    def test_no_match_for_question(self):
        assert match_keywords("what is pac framework") is None

    def test_no_match_for_greeting(self):
        assert match_keywords("hello grim") is None

    def test_all_categories_have_keywords(self):
        """Every delegation type should have at least one keyword."""
        for dtype, keywords in DELEGATION_KEYWORDS.items():
            assert len(keywords) > 0, f"No keywords for {dtype}"

    def test_all_delegation_types(self):
        """Should have exactly 6 delegation types (v0.0.6: planning graph-level, codebase added Phase 3)."""
        expected = {"memory", "research", "ironclaw", "operate", "audit", "codebase"}
        assert set(DELEGATION_KEYWORDS.keys()) == expected

    def test_no_code_delegation_type(self):
        """v0.0.6: 'code' delegation removed — all code ops route to ironclaw."""
        assert "code" not in DELEGATION_KEYWORDS

    def test_case_sensitivity(self):
        """Keywords use substring matching on lowercased input."""
        # Should match when lowered
        assert match_keywords("remember this") == "memory"
        # Won't match mixed case unless caller lowers first
        assert match_keywords("REMEMBER THIS") is None


class TestKeywordBoundaries:
    """Test v0.0.6 boundary enforcement and overlap resolution."""

    def test_no_code_key_in_delegation(self):
        """'code' delegation type must not exist."""
        assert "code" not in DELEGATION_KEYWORDS

    @pytest.mark.parametrize("msg,expected", [
        # Code ops → ironclaw (not code)
        ("write code for parser", "ironclaw"),
        ("implement the feature", "ironclaw"),
        ("refactor the module", "ironclaw"),
        ("debug this issue", "ironclaw"),
        ("add a test for auth", "ironclaw"),
        # Shell ops → ironclaw (not operate)
        ("run command ls", "ironclaw"),
        ("shell out to bash", "ironclaw"),
        ("deploy the app", "ironclaw"),
        ("curl the endpoint", "ironclaw"),
        # Git writes → ironclaw (not operate)
        ("git push to origin", "ironclaw"),
        ("commit these changes", "ironclaw"),
        # Git reads → operate (not ironclaw)
        ("git status", "operate"),
        ("git log please", "operate"),
        ("git diff main", "operate"),
        # NOTE: Planning keywords removed from delegation — handled at graph level
        # Task writes → memory
        ("create a story for auth", "memory"),
        ("create a task for bug", "memory"),
        ("move to active column", "memory"),
    ])
    def test_v006_boundary_routing(self, msg, expected):
        """Verify v0.0.6 agent boundaries are enforced correctly."""
        assert match_keywords(msg.lower()) == expected

    def test_planning_not_in_delegation_keywords(self):
        """Planning is now graph-level, not in delegation keywords."""
        assert "planning" not in DELEGATION_KEYWORDS

    def test_operate_does_not_contain_shell(self):
        """Operate keywords should NOT include shell/execution."""
        operate_kws = DELEGATION_KEYWORDS["operate"]
        for kw in operate_kws:
            assert "run command" not in kw
            assert "shell" not in kw
            assert "execute" not in kw
            assert "deploy" not in kw

    def test_operate_does_not_contain_git_writes(self):
        """Operate keywords should NOT include git write operations."""
        operate_kws = DELEGATION_KEYWORDS["operate"]
        for kw in operate_kws:
            assert "git push" not in kw
            assert "commit" not in kw

    def test_ironclaw_has_code_keywords(self):
        """IronClaw should have all the code operation keywords."""
        ic_kws = DELEGATION_KEYWORDS["ironclaw"]
        assert "write code" in ic_kws
        assert "implement" in ic_kws
        assert "refactor" in ic_kws
        assert "debug this" in ic_kws

    def test_ironclaw_has_shell_keywords(self):
        """IronClaw should have shell execution keywords."""
        ic_kws = DELEGATION_KEYWORDS["ironclaw"]
        assert "run command" in ic_kws
        assert "shell" in ic_kws
        assert "deploy" in ic_kws

    @pytest.mark.parametrize("msg", [
        "implement the plan",
        "plan this implementation",
    ])
    def test_ambiguous_plan_implement(self, msg):
        """'plan' is no longer a delegation keyword (graph-level now).
        'implement' matches ironclaw via keyword_router."""
        result = match_keywords(msg.lower())
        # "implement" is an ironclaw keyword — planning is graph-level
        assert result == "ironclaw"

    def test_keyword_order_is_deterministic(self):
        """Same input always produces same output (dict iteration order)."""
        msg = "plan this implementation"
        results = {match_keywords(msg) for _ in range(10)}
        assert len(results) == 1, "match_keywords must be deterministic"

    def test_each_category_has_unique_purpose(self):
        """No two categories should have identical keyword lists."""
        categories = list(DELEGATION_KEYWORDS.values())
        for i, kws_a in enumerate(categories):
            for j, kws_b in enumerate(categories):
                if i != j:
                    assert set(kws_a) != set(kws_b)


class TestMatchActionIntent:
    """Test action-intent fallback matching."""

    @pytest.mark.parametrize("msg", [
        "run the command on the server",
        "execute a shell command",
        "check the network connection",
        "test the dns resolution",
        "show me the file directory",
        "get me the system info",
        "ping the server port",
        "tell me my ip address",
        "run the cli tool",
    ])
    def test_action_intent_matches(self, msg):
        """v0.0.6: action-intent routes to ironclaw (execution layer)."""
        assert match_action_intent(msg.lower()) == "ironclaw"

    def test_no_match_verb_only(self):
        """Verb without a matching target should not match."""
        assert match_action_intent("run something fun") is None

    def test_no_match_target_only(self):
        """Target without a matching verb should not match."""
        assert match_action_intent("the server is down") is None

    def test_no_match_unrelated(self):
        assert match_action_intent("tell me about pac framework") is None

    def test_returns_ironclaw_only(self):
        """Action-intent always returns 'ironclaw' or None (v0.0.6)."""
        result = match_action_intent("execute the shell command")
        assert result in ("ironclaw", None)


class TestIsFollowUp:
    """Test follow-up signal detection."""

    @pytest.mark.parametrize("msg", [
        "now do the same for the other file",
        "also add error handling",
        "try again please",
        "can you also fix the tests",
        "run that command again",
        "why didn't that work",
        "why cant you do this",
        "another attempt please",
        "next step",
        "do it now",
        "just use the same approach",
        "you already have that info",
        "i just asked you to do this",
    ])
    def test_follow_up_detected(self, msg):
        assert is_follow_up(msg.lower()) is True

    def test_not_follow_up(self):
        assert is_follow_up("tell me about pac framework") is False

    def test_not_follow_up_greeting(self):
        assert is_follow_up("hello grim, how are you") is False

    def test_empty_string(self):
        assert is_follow_up("") is False


class TestContinuityForPlanning:
    """Test follow-up signals work for planning delegation continuity."""

    def test_follow_up_after_planning(self):
        """'also' and 'now' are follow-up signals that would re-delegate."""
        assert is_follow_up("also break down the auth module") is True
        assert is_follow_up("now prioritize those stories") is True

    def test_planning_then_follow_up_pattern(self):
        """Planning is graph-level now; keyword match hits ironclaw via 'implement'."""
        # First turn: "implement" is an ironclaw keyword
        assert match_keywords("plan this implementation") == "ironclaw"
        # Follow-up turn: follow-up signal detected
        assert is_follow_up("also scope the backend work") is True

    def test_ironclaw_then_follow_up(self):
        """Follow-up after ironclaw delegation."""
        assert match_keywords("write code for parser") == "ironclaw"
        assert is_follow_up("now add error handling") is True

    def test_non_follow_up_breaks_continuity(self):
        """A fresh request (no follow-up signal) should not re-delegate."""
        assert is_follow_up("search the vault for pac framework") is False


class TestDelegationCompleteness:
    """Verify all 6 delegation types are well-covered by keywords."""

    def test_all_types_have_multiple_keywords(self):
        """Each delegation type should have at least 3 keywords."""
        for dtype, keywords in DELEGATION_KEYWORDS.items():
            assert len(keywords) >= 3, f"{dtype} has only {len(keywords)} keywords"

    def test_planning_not_in_delegation(self):
        """Planning is graph-level (graph_router.py), not a delegation type."""
        assert "planning" not in DELEGATION_KEYWORDS

    def test_ironclaw_covers_former_code_and_operate(self):
        """IronClaw should have keywords from both former code and operate agents."""
        kws = DELEGATION_KEYWORDS["ironclaw"]
        # Former code keywords
        assert any("code" in k for k in kws)
        assert any("implement" in k for k in kws)
        # Former operate shell keywords
        assert any("shell" in k for k in kws)
        assert any("deploy" in k for k in kws)

    def test_memory_still_has_vault_ops(self):
        """Memory should retain all vault write keywords."""
        kws = " ".join(DELEGATION_KEYWORDS["memory"])
        assert "capture" in kws
        assert "remember" in kws
        assert "vault" in kws
        assert "story" in kws  # task management writes

    def test_codebase_in_delegation_keywords(self):
        """Phase 3: codebase delegation type exists."""
        assert "codebase" in DELEGATION_KEYWORDS

    def test_codebase_has_sufficient_keywords(self):
        """Codebase should have robust keyword coverage."""
        kws = DELEGATION_KEYWORDS["codebase"]
        assert len(kws) >= 10, f"Codebase only has {len(kws)} keywords"


class TestCodebaseKeywordRouting:
    """Test codebase delegation keyword matching (Phase 3)."""

    @pytest.mark.parametrize("msg,expected", [
        ("look at the code for auth", "codebase"),
        ("check the code in fracton", "codebase"),
        ("show me the code for PAC", "codebase"),
        ("what's the repo structure of GRIM", "codebase"),
        ("how does this module work in reality-engine", "codebase"),
        ("explain the architecture of fracton", "codebase"),
        # "where is the code for" collides with ironclaw's "where " keyword
        # The router prioritizes ironclaw (dict order), which is correct for system queries
        ("find the source code for the router", "codebase"),
        ("find the source for base agent", "codebase"),
        ("navigate the repo structure", "codebase"),
        ("browse the code in dawn-field-theory", "codebase"),
        ("what's the code architecture of fracton", "codebase"),
        ("what changed in GRIM recently", "codebase"),
        ("recent changes to reality-engine", "codebase"),
        ("trace through the dispatch flow", "codebase"),
        ("walk me through the code for routing", "codebase"),
        ("what does the code do in graph.py", "codebase"),
        ("how does this work in code", "codebase"),
        ("check the meta.yaml for experiments", "codebase"),
        ("show the directory structure", "codebase"),
        ("index the repo for fracton", "codebase"),
        ("deep index dawn-field-theory", "codebase"),
    ])
    def test_codebase_keyword_matches(self, msg, expected):
        assert match_keywords(msg.lower()) == expected

    def test_codebase_no_overlap_with_ironclaw(self):
        """Codebase keywords should not overlap with IronClaw."""
        cb_kws = set(DELEGATION_KEYWORDS["codebase"])
        ic_kws = set(DELEGATION_KEYWORDS["ironclaw"])
        overlap = cb_kws & ic_kws
        assert not overlap, f"Overlap between codebase and ironclaw: {overlap}"

    def test_codebase_no_overlap_with_operate(self):
        """Codebase keywords should not overlap with operate."""
        cb_kws = set(DELEGATION_KEYWORDS["codebase"])
        op_kws = set(DELEGATION_KEYWORDS["operate"])
        overlap = cb_kws & op_kws
        assert not overlap, f"Overlap between codebase and operate: {overlap}"

    def test_boundary_read_file_goes_operate(self):
        """'read the file' should go to operate, not codebase."""
        assert match_keywords("read the file contents") == "operate"

    def test_boundary_write_code_goes_ironclaw(self):
        """'write code' should go to ironclaw, not codebase."""
        assert match_keywords("write code for the parser") == "ironclaw"

    def test_boundary_implement_goes_ironclaw(self):
        """'implement' should go to ironclaw, not codebase."""
        assert match_keywords("implement the new feature") == "ironclaw"

    def test_source_code_goes_codebase(self):
        """'source code' should go to codebase."""
        assert match_keywords("show me the source code") == "codebase"

    def test_codebase_string_routes_correctly(self):
        """The word 'codebase' itself should route to codebase."""
        assert match_keywords("tell me about the codebase") == "codebase"
