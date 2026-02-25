# Architecture Overview

## System Design

The mock-repo implements a three-layer architecture inspired by
the PAC (Potential-Actualization Conservation) framework:

### Layer 1: Field Engine
The `src/field_engine.py` module contains the core RBF (Recursive
Balance Field) simulation. It models far-from-equilibrium dynamics
where structure emerges from the balance between information
gradients and entropy diffusion.

### Layer 2: API Gateway
The `src/api/` directory exposes REST endpoints for running
simulations and querying results. Built on FastAPI with
async support.

### Layer 3: Storage
Results are persisted in JSON format under `data/results/`.
Each run produces a timestamped output file with full
field state snapshots.

## Key Decisions

1. **Why RBF over traditional PDE solvers?**
   RBF captures the recursive self-organization that PDE
   solvers miss. The PAC conservation law provides a natural
   constraint that prevents numerical drift.

2. **Why JSON over HDF5?**
   Simplicity. For this scale of simulation, JSON is sufficient
   and human-readable. HDF5 would be warranted at >10M field centers.

## Dependencies

- Python 3.11+
- NumPy (for gradient computation)
- FastAPI (API layer)
- Rich (CLI output)

## CIP Integration

This repository follows CIP (Cognition Index Protocol) schema v2.0
for metadata management. Each directory contains a `meta.yaml`
describing its contents and semantic scope.
