# Tuning Results

This directory stores per-agent optimization history, prompt snapshots, and loss curves.

Structure:
```
results/
├── extract/
│   ├── history.json        # Full iteration history
│   ├── best.json           # Best-performing prompt snapshot
│   └── snapshots/          # Per-iteration prompt snapshots
├── judge/
├── actualize/
├── validate/
└── crosslink/
```

Generated automatically by `python -m tuning run <agent>`.
