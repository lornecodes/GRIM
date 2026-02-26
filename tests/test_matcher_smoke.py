"""Quick smoke test for the skill matcher."""
from core.config import load_config
from core.skills.loader import load_skills
from core.skills.matcher import match_skills

cfg = load_config()
registry = load_skills(cfg.skills_path)

tests = [
    "remember this concept about topology",
    "can you capture this idea about Mobius surfaces?",
    "what do I know about PAC framework?",
    "promote the inbox items to FDOs",
    "lets just talk about physics",
    "review vault health",
]

for msg in tests:
    matched = match_skills(msg, registry)
    names = [s.name for s in matched]
    print(f'  "{msg[:50]}" -> {names}')
