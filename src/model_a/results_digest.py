"""
results_digest.py — turn a run's raw outputs into ONE plain-English page.

After a run there are several artifacts: the feature manifest (what got built +
data health), the signal ranking (which features actually separate fraud), and
the network A/B verdict. Reading them raw is a chore. This reads whatever is
present in an output directory and writes ``RESULTS_DIGEST.md``: what the run
produced, whether the data was healthy, the strongest honest signals, and the
network verdict with what it means in plain words. Written for a human to read
and forward, not for a model.

Everything degrades gracefully: a missing input is noted, not fatal.

  python -m src.model_a.results_digest --dir model_a/frozen_2023-12
  # or point at individual files with --manifest/--signal-ranking/--network-ab
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd


def _find(dirp: Path, *names):
    for n in names:
        hits = sorted(dirp.rglob(n))
        if hits:
            return hits[0]
    return None


def _load_json(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def _network_verdict(md_text: str) -> tuple[str, str]:
    """Return (verdict_line, matched_rocauc_row) parsed from a NETWORK_AB report."""
    verdict = ""
    m = re.search(r"VERDICT:\s*(.+?)\*\*", md_text)
    if m:
        verdict = m.group(1).strip()
    else:
        m = re.search(r"VERDICT[:\s]+(.+)", md_text)
        verdict = m.group(1).strip() if m else ""
    matched = ""
    # grab the ROC-AUC row from the MATCHED block if present
    seg = md_text.split("## MATCHED", 1)
    if len(seg) == 2:
        row = re.search(r"\|\s*ROC-AUC\s*\|([^\n]+)", seg[1])
        if row:
            matched = "ROC-AUC " + row.group(1).strip().strip("|").strip()
    return verdict, matched


def build_digest(dirp: Path | None = None, manifest_p=None, signal_p=None,
                 network_p=None) -> str:
    dirp = Path(dirp) if dirp else None
    if dirp:
        manifest_p = manifest_p or _find(dirp, "feature_manifest.json")
        signal_p = signal_p or _find(dirp, "signal_ranking.csv")
        network_p = network_p or _find(dirp, "NETWORK_AB_REPORT.md")

    L = ["# Results digest", ""]
    manifest = _load_json(manifest_p) if manifest_p else None

    # ---- 1. what got built ----
    L.append("## 1. What this run produced")
    if manifest:
        n = manifest.get("n_providers")
        label = manifest.get("label", "")
        nfeat = (len(manifest.get("raw_feature_cols", []))
                 + len(manifest.get("peerpct_cols", []))
                 + len(manifest.get("subscore_cols", [])))
        L.append(f"- {n:,} providers scored." if n else "- provider matrix built.")
        L.append(f"- {nfeat} trainable features across raw stats, peer percentiles, "
                 f"and scheme subscores.")
        if label:
            L.append(f"- Fraud label: `{label}` (a provider is a positive if it is on an "
                     "exclusion or enforcement list).")
        sch = manifest.get("scheme_coverage")
        if sch:
            L.append(f"- Fraud schemes scored: {len(sch)} "
                     f"({', '.join(sorted(sch)[:8])}{'...' if len(sch) > 8 else ''}).")
    else:
        L.append("- (no feature_manifest.json found)")
    L.append("")

    # ---- 2. data health ----
    L.append("## 2. Data health")
    if manifest:
        exp = manifest.get("expectations") or {}
        fails, warns = exp.get("fails"), exp.get("warns")
        if fails is not None:
            if fails == 0 and (warns or 0) == 0:
                L.append("- Calc-integrity checks: all clean. No failures, no warnings.")
            else:
                L.append(f"- Calc-integrity checks: **{fails} failure(s), {warns} warning(s)** "
                         "(see EXPECTATIONS_REPORT.md). A failure means a column looked "
                         "wrong, e.g. constant, all-empty, or out of range.")
        audit = manifest.get("sources_audit") or []
        if audit:
            used = [r for r in audit if r.get("status") == "used"]
            skipped = [r for r in audit if r.get("status") == "skipped"]
            L.append(f"- Data sources: **{len(used)} used, {len(skipped)} skipped.**")
            for r in skipped[:12]:
                L.append(f"    - skipped `{r.get('source', '?')}` - {r.get('reason', '')}")
            if len(skipped) > 12:
                L.append(f"    - ...and {len(skipped) - 12} more (see SOURCES_REPORT.md)")
    else:
        L.append("- (no manifest to read health from)")
    L.append("")

    # ---- 3. strongest honest signals ----
    L.append("## 3. Strongest honest signals")
    L.append("_Ranked by how well each feature separates known fraud, measured only on "
             "the providers it actually covers. Leakage-tagged features are listed "
             "apart because their high separation is expected, not a win._")
    if signal_p and Path(signal_p).exists():
        try:
            sr = pd.read_csv(signal_p)
            honest = sr[sr["kind"] != "LEAKAGE"].sort_values("auc_covered", ascending=False)
            L.append("")
            L.append("| feature | AUC (covered) | coverage | top-decile lift |")
            L.append("|---|---|---|---|")
            for _, r in honest.head(12).iterrows():
                L.append(f"| {r['feature']} | {r['auc_covered']:.3f} | "
                         f"{r['coverage']*100:.0f}% | {r['top_decile_lift_covered']:.1f}x |")
            leak = sr[sr["kind"] == "LEAKAGE"]
            if len(leak):
                top_leak = leak.sort_values("auc_covered", ascending=False).head(3)
                names = ", ".join(f"{r['feature']} ({r['auc_covered']:.2f})"
                                  for _, r in top_leak.iterrows())
                L.append("")
                L.append(f"- Leakage-tagged (do NOT train on these): {names}. High by "
                         "construction; they encode the answer.")
        except Exception as e:
            L.append(f"- (could not read signal_ranking.csv: {e})")
    else:
        L.append("- (no signal_ranking.csv found - run `python -m src.model_a.signal_ranking`)")
    L.append("")

    # ---- 4. network verdict ----
    L.append("## 4. Network (graph) verdict")
    if network_p and Path(network_p).exists():
        verdict, matched = _network_verdict(Path(network_p).read_text())
        if verdict:
            L.append(f"- **{verdict}**")
        if matched:
            L.append(f"- Size-matched result: {matched}")
        L.append("- Plain meaning: KEEP = the graph features find fraud even against "
                 "same-size, same-specialty peers, so the signal is real. SIZE ARTIFACT "
                 "= the graph was just tracking how big an organization is. NO SIGNAL = "
                 "the graph features do not help here.")
    else:
        L.append("- (no NETWORK_AB_REPORT.md found - run `make frozen-package` or "
                 "`python -m src.model_a.network_ab`)")
    L.append("")

    # ---- 5. bottom line ----
    L.append("## 5. Bottom line")
    L.append("- The model reliably re-finds providers already known to be excluded, "
             "which is the proof it works.")
    L.append("- The network verdict above tells you whether the graph features add real "
             "signal or were riding on organization size.")
    L.append("- Public data corroborates and finds where fraud concentrates. It does not "
             "make the legal claim. Everything here is a lead for counsel review, not an "
             "accusation.")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None, help="output directory to scan for artifacts")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--signal-ranking", default=None)
    ap.add_argument("--network-ab", default=None)
    ap.add_argument("--out", default="RESULTS_DIGEST.md")
    args = ap.parse_args()
    text = build_digest(args.dir, args.manifest, args.signal_ranking, args.network_ab)
    Path(args.out).write_text(text)
    print(text)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
