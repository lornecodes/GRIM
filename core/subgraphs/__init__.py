"""Subgraph wrappers — package existing node logic into SubgraphOutput.

Each subgraph wraps existing companion/agent node functions and produces
a SubgraphOutput for the Response Generator loop. This is the evolutionary
step toward full compiled LangGraph subgraphs (v0.0.13+).

Current wrappers:
  - ConversationSubgraph: companion + personal_companion
  - ResearchSubgraph: research + codebase agents via dispatch
  - PlanningSubgraph: planning_companion
  - CodeSubgraph: ironclaw + coder agents via dispatch
"""
