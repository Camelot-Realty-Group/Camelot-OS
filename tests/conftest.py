"""Shared pytest fixtures/path setup for Camelot OS tests.

Ensures both the repo root and orchestrator/ are importable regardless of
where pytest is invoked from.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
for p in (REPO_ROOT, REPO_ROOT / "orchestrator", REPO_ROOT / "concierge_bot"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
