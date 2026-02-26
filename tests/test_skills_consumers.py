"""Quick test script for consumer-aware skill system."""
from core.config import load_config
from core.skills.loader import load_skills
from core.skills.matcher import match_skills
from pathlib import Path

config = load_config(grim_root=Path('.'))
registry = load_skills(config.skills_path)

# Test consumer-aware queries
print('=== GRIM-facing skills (recognition) ===')
grim_skills = registry.for_grim()
for s in grim_skills:
    print(f'  {s.name} (delegation target: {s.delegation_target()})')

print(f'\n=== Memory agent skills ===')
for s in registry.for_agent('memory'):
    print(f'  {s.name}')

print(f'\n=== Coder agent skills ===')
for s in registry.for_agent('code'):
    print(f'  {s.name}')

print(f'\n=== Research agent skills ===')
for s in registry.for_agent('research'):
    print(f'  {s.name}')

print(f'\n=== Operator agent skills ===')
for s in registry.for_agent('operate'):
    print(f'  {s.name}')

# Test matching
tests = [
    'remember this: PAC is important',
    'write a Python script to test the golden ratio',
    'review vault health and triage inbox',
    'analyze this paper on information theory',
    'commit and push the current changes',
    'what is Dawn Field Theory?',
]

print(f'\n=== Skill matching tests ===')
for msg in tests:
    matched = match_skills(msg, registry)
    if matched:
        names = [f'{s.name}->{s.delegation_target() or "companion"}' for s in matched]
        print(f'  "{msg[:50]}" -> {names}')
    else:
        print(f'  "{msg[:50]}" -> companion (no skill match)')
