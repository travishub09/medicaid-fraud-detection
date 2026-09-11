"""
run_catalog.py — the on-disk inventory of every model run, discovered from
artifacts, not memory.

The v6 confusion (a remembered run that never existed) happened because
"what runs do I have" lived in recollection instead of on disk. This
module walks the data root, identifies run folders by their artifact
FINGERPRINT (matrix + manifest + reports — whatever the folder is named),
classifies each as frozen (leakage-correct, as-of cutoff) or current-day,
dates it from file mtimes, and writes:

    <data_root>/model_a/run_catalog.json    (machine-readable; tools use it)
    <data_root>/model_a/RUN_CATALOG.md      (the human view)

Consumers: ``consensus --runs auto`` merges every cataloged frozen run;
anything else that needs "all runs" reads the json instead of asking a
human to remember folder names. Read-only on everything except the two
catalog files.

  python -m src.run_catalog --data-root <root>
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

RUN_MARKERS = {
    "provider_features_for_model.parquet": 10,
    "feature_manifest.json": 5,
    "results_digest.md": 3,
    "pursuit_dossier.md": 3,
    "source_ablation_oof_full.md": 3,
    "network_ab_report.md": 3,
    "scheme_triage.md": 2,
    "scheme_eval.md": 2,
    "case_validation.md": 2,
    "provider_priority_list.csv": 2,
    "expectations_report.md": 2,
    "sources_report.md": 2,
}
_MIN_SCORE = 5          # matrix alone, or several reports together


def _classify(dirpath: Path, files_lower: dict[str, str]) -> str:
    """frozen (as-of cutoff) vs current-day, from the manifest if possible,
    else from the folder-name convention."""
    man = files_lower.get("feature_manifest.json")
    if man:
        try:
            mj = json.loads((dirpath / man).read_text(encoding="utf-8"))
            if mj.get("asof_cutoff") or mj.get("freeze_args", {}).get(
                    "asof_cutoff"):
                return "frozen"
            if "asof_cutoff" in mj:            # present but null
                return "current"
        except Exception:
            pass
    return "frozen" if "frozen" in dirpath.name.lower() else "current"


def discover_runs(data_root: str | Path) -> list[dict]:
    """Scan the data root for run folders; return catalog entries sorted
    newest-first. Read-only."""
    root = Path(data_root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in
                       ("_resume_cache", "__pycache__", ".git")]
        low = {f.lower(): f for f in filenames}
        score = sum(w for name, w in RUN_MARKERS.items() if name in low)
        if score < _MIN_SCORE:
            continue
        d = Path(dirpath)
        hits = [low[n] for n in RUN_MARKERS if n in low]
        times = []
        for f in hits:
            try:
                times.append((d / f).stat().st_mtime)
            except OSError:
                pass
        has_matrix = "provider_features_for_model.parquet" in low
        out.append({
            "run_dir": str(d),
            "name": d.name,
            "kind": _classify(d, low),
            "has_matrix": has_matrix,
            "n_artifacts": len(hits),
            "artifacts": sorted(hits),
            "written_from": datetime.fromtimestamp(min(times)).isoformat(
                timespec="minutes") if times else None,
            "written_to": datetime.fromtimestamp(max(times)).isoformat(
                timespec="minutes") if times else None,
        })
    return sorted(out, key=lambda r: r["written_to"] or "", reverse=True)


def write_catalog(data_root: str | Path) -> Path:
    root = Path(data_root)
    runs = discover_runs(root)
    outdir = root / "model_a"
    outdir.mkdir(parents=True, exist_ok=True)
    jp = outdir / "run_catalog.json"
    jp.write_text(json.dumps(
        {"data_root": str(root),
         "generated_at": datetime.now().isoformat(timespec="minutes"),
         "runs": runs}, indent=1), encoding="utf-8")
    md = ["# RUN CATALOG — every model run on this machine, from artifacts",
          "", f"_data root: {root}; regenerate with "
          f"`python -m src.run_catalog --data-root \"{root}\"`. Tools read "
          "run_catalog.json; `consensus --runs auto` merges the frozen "
          "runs listed here._", "",
          "| run | kind | matrix | artifacts | files written |",
          "|---|---|---|--:|---|"]
    for r in runs:
        md.append(f"| {r['name']} | {r['kind']} | "
                  f"{'yes' if r['has_matrix'] else 'NO'} | "
                  f"{r['n_artifacts']} | {r['written_from']} -> "
                  f"{r['written_to']} |")
    (outdir / "RUN_CATALOG.md").write_text("\n".join(md) + "\n",
                                           encoding="utf-8")
    print(f"[run_catalog] {len(runs)} runs -> {jp}")
    print(f"[run_catalog] human view -> {outdir / 'RUN_CATALOG.md'}")
    return jp


def cataloged_run_dirs(data_root: str | Path, kind: str = "frozen",
                       require_matrix: bool = True,
                       refresh: bool = True) -> list[str]:
    """The list tools consume: run folders of the given kind ('frozen',
    'current', or 'all'), newest first. Refreshes the catalog from disk by
    default so it can never serve a stale memory."""
    root = Path(data_root)
    jp = root / "model_a" / "run_catalog.json"
    if refresh or not jp.exists():
        write_catalog(root)
    runs = json.loads(jp.read_text(encoding="utf-8"))["runs"]
    return [r["run_dir"] for r in runs
            if (kind == "all" or r["kind"] == kind)
            and (r["has_matrix"] or not require_matrix)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root",
                    default=os.environ.get(
                        "MEDICAID_DATA_ROOT",
                        str(Path.home() / "Desktop/data")))
    args = ap.parse_args()
    write_catalog(args.data_root)


if __name__ == "__main__":
    main()
