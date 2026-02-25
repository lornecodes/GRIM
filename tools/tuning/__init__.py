"""
Tuning framework for actualization agent prompts.

Isolates each agent (extract, judge, actualize, validate, crosslink),
runs it against a diverse mock dataset, scores outputs against
ground truth, then uses Claude to rewrite prompts based on failures.

Usage:
    python -m tuning run extract       # Tune one agent
    python -m tuning run all           # Tune all agents
    python -m tuning eval extract      # Evaluate without optimizing
    python -m tuning status            # Show tuning history
"""
