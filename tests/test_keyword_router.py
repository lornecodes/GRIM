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
        # Task management (memory delegation)
        ("create a story for auth", "memory"),
        ("create a task for bug fix", "memory"),
        ("move to active column", "memory"),
        ("move to in progress", "memory"),
        ("sync calendar now", "memory"),
        ("add calendar event", "memory"),
        ("plan sprint work", "memory"),
        # Code delegation
        ("write code for the parser", "code"),
        ("implement the new feature", "code"),
        ("refactor this function", "code"),
        ("fix this code please", "code"),
        ("add a test for it", "code"),
        ("write a function to parse", "code"),
        ("debug this issue", "code"),
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
        # Operate delegation
        ("run command ls -la", "operate"),
        ("git status", "operate"),
        ("ping the server", "operate"),
        ("curl the api endpoint", "operate"),
        ("shell out to bash", "operate"),
        ("check the status of service", "operate"),
        ("deploy the application", "operate"),
        ("what is my ip address", "operate"),
        ("whoami on this machine", "operate"),
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

    def test_all_six_delegation_types(self):
        """Should have exactly 6 delegation types."""
        expected = {"memory", "code", "research", "ironclaw", "operate", "audit"}
        assert set(DELEGATION_KEYWORDS.keys()) == expected

    def test_case_sensitivity(self):
        """Keywords use substring matching on lowercased input."""
        # Should match when lowered
        assert match_keywords("remember this") == "memory"
        # Won't match mixed case unless caller lowers first
        assert match_keywords("REMEMBER THIS") is None


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
        assert match_action_intent(msg.lower()) == "operate"

    def test_no_match_verb_only(self):
        """Verb without a matching target should not match."""
        assert match_action_intent("run something fun") is None

    def test_no_match_target_only(self):
        """Target without a matching verb should not match."""
        assert match_action_intent("the server is down") is None

    def test_no_match_unrelated(self):
        assert match_action_intent("tell me about pac framework") is None

    def test_returns_operate_only(self):
        """Action-intent always returns 'operate' or None."""
        result = match_action_intent("execute the shell command")
        assert result in ("operate", None)


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
