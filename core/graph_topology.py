"""Static GRIM graph topology — node metadata, edges, and layout positions.

Pure data module with no imports from graph.py or node modules to avoid
circular dependencies.  The ``/api/graph/topology`` endpoint merges this
static skeleton with live agent-registry metadata (tools, enabled state).
"""

from __future__ import annotations

from typing import Literal

NodeType = Literal[
    "preprocessing", "routing", "companion", "agent", "postprocessing", "infra"
]
EdgeType = Literal["static", "conditional"]

# ---------------------------------------------------------------------------
# Infrastructure node metadata
# Companion/agent nodes get their metadata from GRAPH_NODE_METADATA + registry.
# ---------------------------------------------------------------------------

INFRA_NODE_METADATA: dict[str, dict] = {
    "identity": {
        "id": "identity",
        "name": "Identity",
        "role": "preprocessor",
        "description": "Injects system prompt and personality into session state",
        "detail": (
            "First node in every request. Loads and assembles the full system prompt from:\n\n"
            "- identity/system_prompt.md — core identity instructions (~67 lines)\n"
            "- identity/personality.yaml — field state (coherence: 0.8, valence: 0.3, uncertainty: 0.2)\n"
            "- grim-identity FDO — extended identity from Kronos vault\n"
            "- grim-personality FDO — compiled personality cache\n"
            "- Caller profile — loads the 'peter' FDO for personalization\n"
            "- Working memory — via kronos_memory_read\n"
            "- Active objectives — from local/objectives/ directory\n\n"
            "Output: state.system_prompt and state.field_state populated for downstream nodes."
        ),
        "tools": [],
        "color": "#8888a0",
        "tier": "grim",
        "toggleable": False,
        "node_type": "preprocessing",
    },
    "compress": {
        "id": "compress",
        "name": "Compress",
        "role": "preprocessor",
        "description": "Trims conversation history to fit context window",
        "detail": (
            "Checks if the conversation exceeds the context limit and trims if needed.\n\n"
            "- context_max_tokens: 160,000 (trigger threshold)\n"
            "- context_keep_recent: 12 messages (always preserved)\n\n"
            "When triggered, older messages are summarized and replaced with a compressed "
            "representation. Recent messages are kept verbatim to preserve conversation flow. "
            "Runs on every turn but only compresses when the token count exceeds the threshold."
        ),
        "tools": [],
        "color": "#8888a0",
        "tier": "grim",
        "toggleable": False,
        "node_type": "preprocessing",
    },
    "memory": {
        "id": "memory",
        "name": "Memory Recall",
        "role": "preprocessor",
        "description": "Retrieves relevant knowledge from Kronos vault for the current query",
        "detail": (
            "Searches the Kronos vault for FDOs relevant to the user's message. "
            "Uses hybrid search (exact tags + BM25 keyword + graph expansion with RRF).\n\n"
            "Retrieves up to 5 FDOs with summaries, plus recent rolling notes from the last 30 days. "
            "Results are injected into state.knowledge_context for downstream nodes to reference.\n\n"
            "This is a non-LLM preprocessing step — fast keyword/embedding search, no generation."
        ),
        "tools": [],
        "color": "#60a5fa",
        "tier": "grim",
        "toggleable": False,
        "node_type": "preprocessing",
    },
    "skill_match": {
        "id": "skill_match",
        "name": "Skill Match",
        "role": "preprocessor",
        "description": "Matches user intent to skill protocols and loads matching protocol texts",
        "detail": (
            "Scans the user message against skill trigger patterns (keywords + regex) "
            "from all 34 registered skills.\n\n"
            "When a match is found, loads the full skill protocol text from the skills/ directory "
            "and injects it into state.skill_protocols. Multiple skills can match simultaneously.\n\n"
            "Also sets state.matched_skills with skill metadata for routing decisions. "
            "If a skill has a delegation_hint (e.g. 'memory', 'ironclaw'), it flows to the router "
            "as skill_delegation_hint to bias delegation."
        ),
        "tools": [],
        "color": "#8888a0",
        "tier": "grim",
        "toggleable": False,
        "node_type": "preprocessing",
    },
    "graph_router": {
        "id": "graph_router",
        "name": "Graph Router",
        "role": "router",
        "description": "Routes to research, personal, or planning branch based on query intent",
        "detail": (
            "First routing decision — selects which graph branch handles the request. "
            "Uses signal-based pattern matching (no LLM call).\n\n"
            "Priority order:\n"
            "1. Skill delegation hint → research (if skill matched with delegation_hint)\n"
            "2. Planning signals → planning_companion\n"
            "3. Delegation keywords → research (router)\n"
            "4. Action-intent patterns → research (router)\n"
            "5. Personal signals → personal_companion (unless research override detected)\n"
            "6. Default → research (router)\n\n"
            "Personal signals have a research-override check: if the message contains both "
            "personal signals AND research content, it routes to research instead."
        ),
        "tools": [],
        "color": "#f59e0b",
        "tier": "grim",
        "toggleable": False,
        "node_type": "routing",
        "routing_rules": [
            {"condition": "research intent (default)", "target": "router"},
            {"condition": "personal / emotional query", "target": "personal_companion"},
            {"condition": "planning / task management", "target": "planning_companion"},
        ],
        "signals": {
            "personal": [
                "how are you", "how's it going", "what's up",
                "how's your day", "good morning", "good night",
                "i'm feeling", "i feel", "i'm stressed", "i'm tired",
                "i'm frustrated", "i'm excited", "i'm worried",
                "just chatting", "let's chat", "tell me about yourself",
                "who are you", "just checking in", "i appreciate you",
            ],
            "planning": [
                "let's plan", "plan this", "break this down",
                "scope this", "plan sprint", "groom backlog",
                "create stories for", "create tasks for",
                "prioritize the work", "what should i work on",
                "promote draft", "approve draft", "show drafts",
            ],
        },
    },
    "router": {
        "id": "router",
        "name": "Router",
        "role": "router",
        "description": "Decides whether companion handles the query or delegates to a specialist agent",
        "detail": (
            "Second routing decision — within the research branch, decides think vs delegate. "
            "Uses a 5-level priority cascade (no LLM call).\n\n"
            "Priority order:\n"
            "1. Skill delegation hint — if skill_match set a delegation_hint, use it\n"
            "2. Continuity — if previous turn was delegated, follow-up messages continue delegation\n"
            "3. Keyword matching — scans for delegation keywords per agent category\n"
            "4. Action-intent — verb + target patterns (e.g. 'run the command')\n"
            "5. Default → companion (think mode)\n\n"
            "Delegation targets: memory, research, ironclaw (code/shell/git operations)."
        ),
        "tools": [],
        "color": "#f59e0b",
        "tier": "grim",
        "toggleable": False,
        "node_type": "routing",
        "routing_rules": [
            {"condition": "reflection / synthesis needed", "target": "companion"},
            {"condition": "action / tool execution needed", "target": "dispatch"},
        ],
        "signals": {
            "memory": [
                "capture this", "remember this", "save this",
                "promote", "organize vault", "create an fdo",
                "create a story", "create a task", "move to active",
            ],
            "research": [
                "analyze this", "ingest", "deep dive",
                "research this", "investigate", "find papers on",
            ],
            "ironclaw": [
                "ironclaw", "write code", "implement", "create file",
                "fix this code", "refactor", "run command", "run pytest",
                "git commit", "git push", "docker",
            ],
        },
    },
    "dispatch": {
        "id": "dispatch",
        "name": "Dispatch",
        "role": "dispatcher",
        "description": "Delegates work to specialist agents (memory, coder, research, operator, etc.)",
        "detail": (
            "Instantiates and runs the selected specialist agent. The agent to dispatch is "
            "determined by state.delegation_type (set by Router).\n\n"
            "Available agents: memory, research, codebase, operator, ironclaw, audit, code (disabled).\n\n"
            "Each agent gets:\n"
            "- A skill protocol (from skill_match) or its default_protocol\n"
            "- The user's task (with conversation context for reference resolution)\n"
            "- Knowledge context from the memory node\n"
            "- Up to 10 tool-call round trips to complete the task\n\n"
            "Agent output flows to audit_gate for security review before integration."
        ),
        "tools": [],
        "color": "#f59e0b",
        "tier": "grim",
        "toggleable": False,
        "node_type": "routing",
    },
    "audit_gate": {
        "id": "audit_gate",
        "name": "Audit Gate",
        "role": "gate",
        "description": "Checks if IronClaw artifacts require zero-trust audit before integration",
        "detail": (
            "Security checkpoint after agent execution. Checks if the dispatched agent was "
            "IronClaw (tier: ironclaw) and produced artifacts that need review.\n\n"
            "- IronClaw artifacts present → route to audit agent for zero-trust review\n"
            "- GRIM-tier agent or no artifacts → skip directly to integrate\n"
            "- Audit disabled (agent toggled off) → skip to integrate\n\n"
            "Part of the IronClaw security pipeline: dispatch → audit_gate → audit → re_dispatch cycle."
        ),
        "tools": [],
        "color": "#ef4444",
        "tier": "grim",
        "toggleable": False,
        "node_type": "infra",
        "routing_rules": [
            {"condition": "IronClaw artifacts present", "target": "audit"},
            {"condition": "no artifacts / GRIM-only", "target": "integrate (skip)"},
        ],
    },
    "re_dispatch": {
        "id": "re_dispatch",
        "name": "Re-Dispatch",
        "role": "loop",
        "description": "Re-queues failed audit to dispatch for retry (max 3 cycles)",
        "detail": (
            "Handles audit failures by re-dispatching to the agent with feedback. "
            "The audit agent's rejection reason is passed back as context so the agent "
            "can correct its output.\n\n"
            "- Maximum 3 retry cycles before escalating to the user\n"
            "- Each retry increments state.re_dispatch_count\n"
            "- If max retries exceeded, integrates with a failure notice\n\n"
            "This creates the re_dispatch → dispatch loop visible in the graph."
        ),
        "tools": [],
        "color": "#ef4444",
        "tier": "grim",
        "toggleable": False,
        "node_type": "infra",
    },
    "integrate": {
        "id": "integrate",
        "name": "Integrate",
        "role": "postprocessor",
        "description": "Merges agent results and companion output into the response",
        "detail": (
            "Convergence point — all graph branches (companion, personal, planning, agents) "
            "flow here. Merges agent_result with the conversation messages.\n\n"
            "If an agent was dispatched, its AgentResult (success/failure + summary) is "
            "formatted and appended to the message stream. If the companion handled the "
            "query directly, messages pass through unchanged.\n\n"
            "Output: finalized messages ready for the evolve node."
        ),
        "tools": [],
        "color": "#34d399",
        "tier": "grim",
        "toggleable": False,
        "node_type": "postprocessing",
    },
    "evolve": {
        "id": "evolve",
        "name": "Evolve",
        "role": "postprocessor",
        "description": "Updates personality field state and long-term memory after each turn",
        "detail": (
            "Final node in every request. Updates GRIM's persistent state based on the "
            "interaction:\n\n"
            "- Field state modulation — adjusts coherence, valence, uncertainty based on "
            "conversation dynamics (defined in personality.yaml modulation rules)\n"
            "- Memory signals — flags important information for the memory agent to capture "
            "in a future turn\n\n"
            "This is what makes GRIM's personality evolve over time rather than remaining static."
        ),
        "tools": [],
        "color": "#34d399",
        "tier": "grim",
        "toggleable": False,
        "node_type": "postprocessing",
    },
}

# ---------------------------------------------------------------------------
# Edges — source → target with type and optional label
# ---------------------------------------------------------------------------

STATIC_EDGES: list[dict] = [
    # Preprocessing pipeline
    {"source": "identity",    "target": "compress",            "type": "static"},
    {"source": "compress",    "target": "memory",              "type": "static"},
    {"source": "memory",      "target": "skill_match",         "type": "static"},
    {"source": "skill_match", "target": "graph_router",        "type": "static"},
    # Graph routing branches
    {"source": "graph_router", "target": "router",             "type": "conditional", "label": "research"},
    {"source": "graph_router", "target": "personal_companion", "type": "conditional", "label": "personal"},
    {"source": "graph_router", "target": "planning_companion", "type": "conditional", "label": "planning"},
    # Research branch: router → companion or dispatch
    {"source": "router",      "target": "companion",           "type": "conditional", "label": "think"},
    {"source": "router",      "target": "dispatch",            "type": "conditional", "label": "delegate"},
    # Agent delegation path
    {"source": "dispatch",    "target": "audit_gate",          "type": "static"},
    {"source": "audit_gate",  "target": "audit",               "type": "conditional", "label": "artifacts"},
    {"source": "audit_gate",  "target": "integrate",           "type": "conditional", "label": "skip"},
    {"source": "audit",       "target": "integrate",           "type": "conditional", "label": "pass"},
    {"source": "audit",       "target": "re_dispatch",         "type": "conditional", "label": "fail"},
    {"source": "re_dispatch", "target": "dispatch",            "type": "static",      "label": "retry"},
    # Companion paths to postprocessing
    {"source": "companion",          "target": "integrate",    "type": "static"},
    {"source": "personal_companion", "target": "integrate",    "type": "static"},
    {"source": "planning_companion", "target": "integrate",    "type": "static"},
    # Postprocessing
    {"source": "integrate",   "target": "evolve",              "type": "static"},
]

# ---------------------------------------------------------------------------
# Fixed layout positions — (column, row) for the left-to-right DAG
# Column 0 is leftmost; row 0 is the main pipeline.
# ---------------------------------------------------------------------------

NODE_POSITIONS: dict[str, tuple[int, int]] = {
    # Preprocessing (col 0-3, row 0)
    "identity":             (0, 0),
    "compress":             (1, 0),
    "memory":               (2, 0),
    "skill_match":          (3, 0),
    # Graph router (col 4)
    "graph_router":         (4, 0),
    # Branches from graph_router (col 5)
    "personal_companion":   (5, -1),
    "router":               (5, 0),
    "planning_companion":   (5, 1),
    # Research branch inner nodes (col 6-8)
    "companion":            (6, -1),
    "dispatch":             (6, 1),
    "audit_gate":           (7, 1),
    "audit":                (8, 1),
    "re_dispatch":          (8, 2),
    # Postprocessing (col 9-10)
    "integrate":            (9, 0),
    "evolve":               (10, 0),
}

# All known graph node IDs (for validation)
ALL_NODE_IDS: frozenset[str] = frozenset(
    list(INFRA_NODE_METADATA.keys())
    + ["companion", "personal_companion", "planning_companion"]
)
