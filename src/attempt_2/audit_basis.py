"""
audit_basis.py — the one place the stage chain learns what the corruption audit
certified for THIS spending fact (row count + dollars of the identifiable-provider
base), so every downstream dollar-conservation assertion reconciles against the
file actually on disk, not against a constant baked in from one vintage.

audit_corruption.py writes processed/corruption_audit.json. Before 2026-10-08 the
features stage, the company tracker and the backtest each carried the 2024-vintage
national totals as module constants, which made the whole chain fail its own
assertions on any other spending file (a refresh, a state cut, the synthetic
fixture). The constants remain here as the fallback for a processed/ folder that
was audited before the sidecar existed.
"""
from __future__ import annotations

import json
from pathlib import Path

LEGACY_BASE_ROWS = 230_133_234
LEGACY_BASE_PAID = 1_100_631_960_143.0
SIDECAR = "corruption_audit.json"


def audit_base(processed_dir: str | Path) -> tuple[int, float, str]:
    """(rows, paid, basis) of the certified provider base for the fact under
    ``processed_dir``; basis names where the numbers came from."""
    p = Path(processed_dir) / SIDECAR
    if p.exists():
        a = json.loads(p.read_text(encoding="utf-8"))
        return int(a["n_base"]), float(a["paid_base"]), f"{SIDECAR} (this fact's audit)"
    return LEGACY_BASE_ROWS, LEGACY_BASE_PAID, "module constants (legacy 2024 national vintage; no sidecar found)"
