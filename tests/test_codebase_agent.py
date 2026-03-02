"""Tests for the Codebase Agent — Phase 3 source navigation and spatial awareness."""
import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import GrimConfig, load_config


# ---------------------------------------------------------------------------
# Source tool tests
# ---------------------------------------------------------------------------


class TestSourceToolWrappers:
    """Test LangChain wrappers for MCP source tools."""

    @pytest.fixture(autouse=True)
    def mock_mcp(self):
        """Mock the MCP session for all source tool tests."""
        mock_session = AsyncMock()
        with patch("core.tools.kronos_read.tool_context") as ctx:
            ctx.mcp_session = mock_session
            self.mock_session = mock_session
            yield

    def _make_mcp_result(self, data: dict):
        """Create a mock MCP result with TextContent."""
        result = MagicMock()
        result.content = [MagicMock(text=json.dumps(data))]
        self.mock_session.call_tool.return_value = result
        return result

    @pytest.mark.asyncio
    async def test_kronos_navigate_calls_mcp(self):
        from core.tools.kronos_source import kronos_navigate
        self._make_mcp_result({"description": "Root dir", "files": ["README.md"]})
        result = await kronos_navigate.ainvoke({"path": "GRIM"})
        data = json.loads(result)
        assert data["description"] == "Root dir"
        self.mock_session.call_tool.assert_called_once()
        # _call_mcp passes (method_name, kwargs_dict) as positional args
        call_args = self.mock_session.call_tool.call_args[0]
        assert call_args[0] == "kronos_navigate"

    @pytest.mark.asyncio
    async def test_kronos_read_source_calls_mcp(self):
        from core.tools.kronos_source import kronos_read_source
        self._make_mcp_result({"content": "line1\nline2", "line_count": 2})
        result = await kronos_read_source.ainvoke({
            "repo": "GRIM", "path": "core/state.py"
        })
        data = json.loads(result)
        assert data["content"] == "line1\nline2"
        call_args = self.mock_session.call_tool.call_args[0]
        assert call_args[0] == "kronos_read_source"
        assert call_args[1]["repo"] == "GRIM"

    @pytest.mark.asyncio
    async def test_kronos_search_source_calls_mcp(self):
        from core.tools.kronos_source import kronos_search_source
        self._make_mcp_result({"files_searched": 5, "total_hits": 3})
        result = await kronos_search_source.ainvoke({
            "query": "grim-architecture", "pattern": "BaseAgent"
        })
        data = json.loads(result)
        assert data["total_hits"] == 3

    @pytest.mark.asyncio
    async def test_kronos_deep_dive_calls_mcp(self):
        from core.tools.kronos_source import kronos_deep_dive
        self._make_mcp_result({"sources_by_repo": {"GRIM": []}})
        result = await kronos_deep_dive.ainvoke({"query": "grim-architecture"})
        data = json.loads(result)
        assert "sources_by_repo" in data

    @pytest.mark.asyncio
    async def test_kronos_deep_dive_with_type_filter(self):
        from core.tools.kronos_source import kronos_deep_dive
        self._make_mcp_result({"sources_by_repo": {}})
        await kronos_deep_dive.ainvoke({
            "query": "grim-architecture", "type_filter": "module"
        })
        call_kwargs = self.mock_session.call_tool.call_args[0][1]
        assert call_kwargs["type_filter"] == "module"

    @pytest.mark.asyncio
    async def test_kronos_deep_dive_empty_filter_excluded(self):
        from core.tools.kronos_source import kronos_deep_dive
        self._make_mcp_result({"sources_by_repo": {}})
        await kronos_deep_dive.ainvoke({
            "query": "grim-architecture", "type_filter": ""
        })
        call_kwargs = self.mock_session.call_tool.call_args[0][1]
        assert "type_filter" not in call_kwargs


class TestSourceGitTools:
    """Test repo-aware git tools."""

    @pytest.fixture(autouse=True)
    def mock_workspace(self, tmp_path):
        """Create a fake workspace with git repos."""
        self.ws = tmp_path
        # Create a fake repo dir
        repo = tmp_path / "test-repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        with patch("core.tools.workspace.tool_context") as ctx:
            ctx.workspace_root = tmp_path
            yield

    @pytest.mark.asyncio
    async def test_git_log_repo_resolves_path(self):
        from core.tools.kronos_source import git_log_repo
        with patch("core.tools.kronos_source._git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = {
                "exit_code": 0,
                "stdout": "abc12345|Peter|2026-03-02|feat: something",
                "stderr": "",
            }
            result = await git_log_repo.ainvoke({"repo": "test-repo"})
            data = json.loads(result)
            assert len(data) == 1
            assert data[0]["hash"] == "abc12345"
            assert data[0]["message"] == "feat: something"

    @pytest.mark.asyncio
    async def test_git_log_repo_with_since(self):
        from core.tools.kronos_source import git_log_repo
        with patch("core.tools.kronos_source._git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
            await git_log_repo.ainvoke({"repo": "test-repo", "since": "2026-02-28"})
            args = mock_git.call_args[0][0]
            assert any("--since=2026-02-28" in a for a in args)

    @pytest.mark.asyncio
    async def test_git_log_repo_bad_repo(self):
        from core.tools.kronos_source import git_log_repo
        result = await git_log_repo.ainvoke({"repo": "nonexistent-repo"})
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_git_diff_repo_returns_stat(self):
        from core.tools.kronos_source import git_diff_repo
        with patch("core.tools.kronos_source._git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = {
                "exit_code": 0,
                "stdout": " 3 files changed, 10 insertions(+)",
                "stderr": "",
            }
            result = await git_diff_repo.ainvoke({"repo": "test-repo"})
            data = json.loads(result)
            assert "diff_stat" in data

    @pytest.mark.asyncio
    async def test_git_diff_repo_bad_repo(self):
        from core.tools.kronos_source import git_diff_repo
        result = await git_diff_repo.ainvoke({"repo": "nonexistent-repo"})
        data = json.loads(result)
        assert "error" in data


class TestDeepIndexTool:
    """Test the deep_index_repo tool."""

    @pytest.fixture(autouse=True)
    def mock_workspace(self, tmp_path):
        self.ws = tmp_path
        # Create a fake repo
        repo = tmp_path / "test-repo"
        repo.mkdir()
        (repo / "README.md").write_text("# Test Repo\nA test repo.")
        (repo / "pyproject.toml").write_text("[project]\nname = 'test-repo'")
        (repo / "src").mkdir()
        with patch("core.tools.workspace.tool_context") as ctx:
            ctx.workspace_root = tmp_path
            yield

    @pytest.mark.asyncio
    async def test_deep_index_basic(self):
        from core.tools.kronos_source import deep_index_repo
        with patch("core.tools.kronos_source._call_mcp", new_callable=AsyncMock) as mock_mcp, \
             patch("core.tools.kronos_source._git", new_callable=AsyncMock) as mock_git:
            mock_mcp.return_value = {
                "description": "Root",
                "files": ["README.md", "pyproject.toml"],
                "child_directories": ["src"],
            }
            mock_git.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
            result = await deep_index_repo.ainvoke({"repo": "test-repo"})
            data = json.loads(result)
            assert data["repo"] == "test-repo"
            assert "python" in data["technologies"]
            assert "README.md" in data["key_files"]

    @pytest.mark.asyncio
    async def test_deep_index_bad_repo(self):
        from core.tools.kronos_source import deep_index_repo
        result = await deep_index_repo.ainvoke({"repo": "nonexistent"})
        data = json.loads(result)
        assert "error" in data


class TestRepoChangesSinceTool:
    """Test the repo_changes_since tool."""

    @pytest.fixture(autouse=True)
    def mock_workspace(self, tmp_path):
        repo = tmp_path / "test-repo"
        repo.mkdir()
        with patch("core.tools.workspace.tool_context") as ctx:
            ctx.workspace_root = tmp_path
            yield

    @pytest.mark.asyncio
    async def test_repo_changes_basic(self):
        from core.tools.kronos_source import repo_changes_since
        with patch("core.tools.kronos_source._git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = {
                "exit_code": 0,
                "stdout": "abc12345|Peter|2026-03-01|fix: something",
                "stderr": "",
            }
            result = await repo_changes_since.ainvoke({
                "repo": "test-repo", "since": "2026-02-28"
            })
            data = json.loads(result)
            assert data["repo"] == "test-repo"
            assert data["commit_count"] == 1

    @pytest.mark.asyncio
    async def test_repo_changes_bad_repo(self):
        from core.tools.kronos_source import repo_changes_since
        result = await repo_changes_since.ainvoke({
            "repo": "nonexistent", "since": "2026-02-28"
        })
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# Tool registration tests
# ---------------------------------------------------------------------------


class TestSourceToolRegistration:
    """Test that source tools are registered correctly."""

    def test_source_nav_tools_count(self):
        from core.tools.kronos_source import SOURCE_NAV_TOOLS
        assert len(SOURCE_NAV_TOOLS) == 4

    def test_source_git_tools_count(self):
        from core.tools.kronos_source import SOURCE_GIT_TOOLS
        assert len(SOURCE_GIT_TOOLS) == 2

    def test_source_index_tools_count(self):
        from core.tools.kronos_source import SOURCE_INDEX_TOOLS
        assert len(SOURCE_INDEX_TOOLS) == 2

    def test_source_all_tools_count(self):
        from core.tools.kronos_source import SOURCE_ALL_TOOLS
        assert len(SOURCE_ALL_TOOLS) == 8

    def test_tool_names(self):
        from core.tools.kronos_source import SOURCE_ALL_TOOLS
        names = {t.name for t in SOURCE_ALL_TOOLS}
        expected = {
            "kronos_navigate", "kronos_read_source",
            "kronos_search_source", "kronos_deep_dive",
            "git_log_repo", "git_diff_repo",
            "deep_index_repo", "repo_changes_since",
        }
        assert names == expected

    def test_registry_groups_registered(self):
        from core.tools.registry import tool_registry
        assert "source_nav" in tool_registry.groups()
        assert "source_git" in tool_registry.groups()
        assert "source_index" in tool_registry.groups()
        assert "source" in tool_registry.groups()


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------


class TestCodebaseAgentDiscovery:
    """Test CodebaseAgent follows BaseAgent discovery pattern."""

    def test_agent_name_attribute(self):
        import core.agents.codebase_agent as mod
        assert mod.__agent_name__ == "codebase"

    def test_make_agent_attribute(self):
        import core.agents.codebase_agent as mod
        assert callable(mod.__make_agent__)

    def test_agent_discovered_by_registry(self):
        from core.agents.registry import AgentRegistry
        config = GrimConfig()
        reg = AgentRegistry.discover(config, disabled=[])
        assert "codebase" in reg

    def test_agent_excluded_when_disabled(self):
        from core.agents.registry import AgentRegistry
        config = GrimConfig()
        reg = AgentRegistry.discover(config, disabled=["codebase"])
        assert "codebase" not in reg


class TestCodebaseAgentProperties:
    """Test CodebaseAgent class properties."""

    def test_agent_name(self):
        from core.agents.codebase_agent import CodebaseAgent
        assert CodebaseAgent.agent_name == "codebase"

    def test_protocol_priority(self):
        from core.agents.codebase_agent import CodebaseAgent
        assert "repo-navigate" in CodebaseAgent.protocol_priority

    def test_default_protocol_mentions_readonly(self):
        from core.agents.codebase_agent import CodebaseAgent
        assert "read-only" in CodebaseAgent.default_protocol.lower() or \
               "CANNOT write" in CodebaseAgent.default_protocol

    def test_tools_are_read_only(self):
        """Codebase agent must NOT have any write tools."""
        from core.agents.codebase_agent import CodebaseAgent
        config = GrimConfig()
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        # Should NOT have write tools
        write_tools = {"write_file", "edit_file", "git_add_commit", "run_shell"}
        assert tool_names.isdisjoint(write_tools), \
            f"Codebase agent has write tools: {tool_names & write_tools}"

    def test_tools_include_source_nav(self):
        """Agent should have all source navigation tools."""
        from core.agents.codebase_agent import CodebaseAgent
        config = GrimConfig()
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "kronos_navigate" in tool_names
        assert "kronos_read_source" in tool_names
        assert "kronos_search_source" in tool_names
        assert "kronos_deep_dive" in tool_names

    def test_tools_include_git(self):
        """Agent should have git read tools."""
        from core.agents.codebase_agent import CodebaseAgent
        config = GrimConfig()
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "git_log_repo" in tool_names
        assert "git_diff_repo" in tool_names
        assert "git_status" in tool_names
        assert "git_log" in tool_names

    def test_tools_include_vault_read(self):
        """Agent should have vault read tools."""
        from core.agents.codebase_agent import CodebaseAgent
        config = GrimConfig()
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "kronos_search" in tool_names
        assert "kronos_get" in tool_names

    def test_tools_include_file_read(self):
        """Agent should have file read tools."""
        from core.agents.codebase_agent import CodebaseAgent
        config = GrimConfig()
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "list_directory" in tool_names
        assert "search_files" in tool_names


class TestCodebaseAgentContext:
    """Test build_context with repo manifest."""

    def test_build_context_with_repos_yaml(self, tmp_path):
        """build_context should include workspace repo info."""
        from core.agents.codebase_agent import CodebaseAgent
        # Create a fake repos.yaml
        manifest = tmp_path / "repos.yaml"
        manifest.write_text(
            "repos:\n"
            "  - name: test-repo\n"
            "    tier: core\n"
            "    description: A test\n"
            "    path: test-repo\n"
        )
        config = GrimConfig()
        config.workspace_root = tmp_path
        config.repos_manifest = "repos.yaml"
        agent = CodebaseAgent(config)
        context = agent.build_context({})
        assert "test-repo" in context.get("workspace_repos", "")

    def test_build_context_missing_manifest(self, tmp_path):
        """build_context should handle missing repos.yaml gracefully."""
        from core.agents.codebase_agent import CodebaseAgent
        config = GrimConfig()
        config.workspace_root = tmp_path
        config.repos_manifest = "repos.yaml"
        agent = CodebaseAgent(config)
        context = agent.build_context({})
        # Should not crash, just empty string
        assert context.get("workspace_repos", "") == ""

    def test_build_context_with_knowledge(self):
        """build_context should include FDO knowledge context."""
        from core.agents.codebase_agent import CodebaseAgent
        config = GrimConfig()
        agent = CodebaseAgent(config)
        # Mock FDO summary
        fdo = MagicMock()
        fdo.id = "grim-architecture"
        fdo.domain = "ai-systems"
        fdo.status = "stable"
        fdo.summary = "GRIM architecture overview"
        fdo.related = ["grim-langgraph"]
        context = agent.build_context({"knowledge_context": [fdo]})
        assert "grim-architecture" in context.get("relevant_knowledge", "")


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestCodebaseConfig:
    """Test GrimConfig codebase fields."""

    def test_default_workspace_root(self):
        config = GrimConfig()
        assert config.workspace_root == Path("..")

    def test_default_repos_manifest(self):
        config = GrimConfig()
        assert config.repos_manifest == "repos.yaml"

    def test_workspace_root_resolved(self, tmp_path):
        """workspace_root should be resolved to absolute path by load_config."""
        grim_root = tmp_path / "GRIM"
        grim_root.mkdir()
        config_dir = grim_root / "config"
        config_dir.mkdir()
        (config_dir / "grim.yaml").write_text("env: debug\n")
        config = load_config(grim_root=grim_root)
        assert config.workspace_root.is_absolute()
