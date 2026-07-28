"""
run_history.py — the reconstructed release timeline: every major run BEFORE
the registry existed, with what we were trying to change, why, and how it did.

The run_registry only knows about runs registered after it was built (July
2026). But the project has a real history: the LEIE backtest that started it,
the first frozen build, the network A/B verdicts, the three ablation runs,
and the label expansion. This module bakes that history in as CURATED,
RETROSPECTIVE entries so the audit log and dashboard show the whole arc,
not just the last week.

HONESTY RULE: these entries are reconstructed after the fact from the
working chat transcript, the git log, and the repo docs. Every entry carries
`retrospective: true` and an `evidence` field saying where its numbers come
from. They are rendered visually apart from properly-registered runs and are
NEVER used by recommend() — a reconstructed row can inform, not promote.

How far back the evidence reaches:
  git log        2026-05-29 onward (~300 commits): dates + what changed.
  chat transcript 2026-07-10 onward verbatim, plus a summary of early July.
  repo docs      the pre-chat era (LEIE backtest, attempt_1/attempt_2).

  write    python -m src.model_a.run_history --write
           writes run_history.jsonl beside run_registry.jsonl. Idempotent
           full rewrite — this file is code-owned curation, not an
           append-only log (those stay separate and untouched).
  report   python -m src.model_a.run_history --report
           prints the timeline as markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# The curated timeline. Dates are the day the result landed (approximate
# where marked). Numbers are verbatim from the reports quoted at the time.
# ---------------------------------------------------------------------------
HISTORY: list[dict] = [
    {
        "tag": "origin_backtest",
        "date": "2026-06",
        "date_approx": True,
        "title": "LEIE temporal backtest — the original proof",
        "goal": ("Show the unsupervised composite score predicts real "
                 "enforcement before building anything bigger."),
        "why": ("Without out-of-time evidence the whole detection stack is "
                "an opinion. This was the first honest test."),
        "changes": ["src/backtest built: score providers on data before a "
                    "cutoff, check who lands on the LEIE after it"],
        "results": {"top_decile_lift": 2.0},
        "results_text": ("Providers in the top decile of the score were "
                         "about 2.0x as likely to be excluded later as the "
                         "average provider."),
        "lesson": "The signal is real; everything since builds on this.",
        "evidence": "src/backtest + docs/WHAT_WAS_BUILT.md",
        "retrospective": True,
    },
    {
        "tag": "run3_frozen",
        "date": "2026-07-10",
        "title": "First frozen 2023-12 build (run3)",
        "goal": ("Produce a leakage-correct training matrix frozen at "
                 "2023-12 so future-ban prediction is graded out-of-time."),
        "why": ("Travis needed a training set he could trust, and any "
                "feature computed after the freeze would let the model "
                "peek at the future."),
        "changes": [
            "pre-run bulletproof sweep: 4 silent-wiring bugs fixed "
            "(docgraph, POS, referral_rings, reassignment adapters existed "
            "but were never invoked)",
            "16GB memory engineering: DuckDB streaming for the 210M-row "
            "referral scan, sparse-CSR graph BFS instead of NetworkX",
            "statistics fixes: PU lift correction, FDR null from unlabeled, "
            "circular weak-supervision LFs pinned",
        ],
        "results": {"n_providers": 617062, "n_features": 135,
                    "sources_used": 28, "sources_skipped": 3,
                    "expectation_fails": 13},
        "results_text": ("617,062 providers x 135 trainable features; 28 "
                         "sources used, 3 skipped; 13 calc-integrity FAILs "
                         "surfaced and drove the next round of fixes."),
        "lesson": ("A run that reports its own failures loudly is worth "
                   "more than a quiet one."),
        "evidence": "RESULTS_DIGEST run3/run4 pasted in chat 2026-07-11",
        "retrospective": True,
    },
    {
        "tag": "network_ab_keep",
        "date": "2026-07-15",
        "date_approx": True,
        "title": "Network A/B: do the graph features earn their keep?",
        "goal": ("Answer Travis's challenge that the graph features were "
                 "noise or label leakage."),
        "why": ("If he was right, half the feature matrix was dead weight "
                "and the handoff would embarrass us."),
        "changes": [
            "network_ab honesty gates: ceiling guard, label-adjacent vs "
            "structural feature split, forward-label matching, 5-split "
            "cluster bootstrap",
            "two robustness attacks Travis designed: drop in-flight bans, "
            "residualize fraud proximity",
        ],
        "results": {"verdict": "KEEP", "replications": 5},
        "results_text": ("Verdict KEEP, replicated 5 times by 2026-07-28 "
                         "including both attack scenarios. The structural "
                         "graph features carry real signal on a "
                         "size-matched cohort."),
        "lesson": ("Skepticism institutionalized: his attacks became our "
                   "standing tests."),
        "evidence": "NETWORK_AB_REPORT iterations in chat, 07-11 to 07-28",
        "retrospective": True,
    },
    {
        "tag": "ablation_run1_holdout",
        "date": "2026-07-24",
        "title": "Ablation run 1 (quick holdout) — UNDECIDED",
        "goal": ("Settle core-5-files vs full matrix with a fast "
                 "single-split test."),
        "why": ("Travis predicted ~70% of the extra data was overlap and "
                "noise; we wanted a number."),
        "changes": ["fast_ablation script: one 30% holdout, ~306 of the "
                    "1,021 forward positives in the metric"],
        "results": {"roc_delta": 0.0033, "roc_lo": -0.0282,
                    "roc_hi": 0.0363, "positives_scored": 306},
        "results_text": ("ROC delta +0.0033 with a CI from -0.028 to "
                         "+0.036: the test could not tell the difference. "
                         "Underpowered, not a tie."),
        "lesson": ("306 positives cannot resolve a small effect. Pool "
                   "out-of-fold so all 1,021 count."),
        "evidence": "fast_ablation output pasted in chat 2026-07-24/25",
        "retrospective": True,
    },
    {
        "tag": "ablation_run2_oof",
        "date": "2026-07-26",
        "title": "Ablation run 2 (out-of-fold, all 1,021) — lift wins, "
                 "ROC a wash",
        "goal": ("Re-run the same question with enough power to answer "
                 "it."),
        "why": ("Run 1 proved only that run 1 was too small."),
        "changes": [
            "OOF pooling: 5 grouped folds so every provider is scored by "
            "a model that never saw it; all 1,021 positives feed the "
            "metric",
            "400 cluster-bootstrap draws for CIs on lift and PR-AUC, not "
            "just ROC",
        ],
        "results": {"lift_clear": True, "pr_clear": True,
                    "roc_clear": False, "positives_scored": 1021},
        "results_text": ("Top-decile lift and PR-AUC clear of zero for the "
                         "full matrix; ROC still a wash. Sent Travis the "
                         "ROC take-back. Per-source leaderboard (DME "
                         "+0.70, 340B +0.40, opioid +0.38, HCRIS +0.27) "
                         "later proved unstable across runs."),
        "lesson": ("Quote the metric leads actually experience (lift), "
                   "and never quote a per-source MVP as a standalone "
                   "fact."),
        "evidence": "SOURCE_ABLATION_OOF output in chat 2026-07-25/26",
        "retrospective": True,
    },
    {
        "tag": "label_expansion",
        "date": "2026-07-27",
        "title": "Label expansion: court cases + 40 state exclusion lists",
        "goal": ("Widen the thin exclusion label with scheme-typed court "
                 "cases and state Medicaid exclusions."),
        "why": ("An exclusion-only label is blind by construction to "
                "kickback, opioid, facility-quality, cost-report, and "
                "saturation schemes; those sources could never be judged."),
        "changes": [
            "agent-harvested DOJ/court case DB v2: 3,121 cases, $59.4B "
            "alleged, 43 states, 8/8 spot-checks verified, "
            "resolved-vs-pending tiering (pending never hard-labels)",
            "40 state Medicaid exclusion lists (~90k rows) via "
            "OpenSanctions converts",
            "join audit: 31% of case rows joinable to our entities now, "
            "gap list ranked by findability",
        ],
        "results": {"cases": 3121, "case_dollars_b": 59.4,
                    "case_states": 43, "state_lists": 40,
                    "joinable_pct": 31},
        "results_text": ("3,121 cases, ~17,000 providers carrying "
                         "scheme-typed case labels after the join; forward "
                         "ban label grew 4x to 4,401."),
        "lesson": ("Labels are the scarcest asset; buying them with agent "
                   "credits beat waiting for perfect data."),
        "evidence": "REVIEW_REPORT_v2 + JOIN_AUDIT in chat 2026-07-26/27",
        "retrospective": True,
    },
    {
        "tag": "v2_withcases_result",
        "date": "2026-07-28",
        "title": "Run 3 of the ablation (v2 rebuild) — all three metrics "
                 "clear",
        "goal": ("Same frozen forward test, upgraded ingredients: widened "
                 "label, sharper features, cleaner freeze."),
        "why": ("Two prior runs leaned the same way; a third with more "
                "power either banks the claim or kills it."),
        "changes": [
            "full overnight rebuild on the widened exclusion set "
            "(40 states + revocations + OpenSanctions)",
            "case DB folded in as scheme-typed labels and features",
        ],
        "results": {"lift_delta": 0.745, "lift_lo": 0.413, "lift_hi": 1.107,
                    "pr_delta": 0.0037, "roc_delta": 0.0337,
                    "roc_lo": 0.0061, "roc_hi": 0.0571,
                    "catches_full": 393, "catches_core": 319,
                    "full_only": 205, "core_only": 131,
                    "overlap_jaccard": 0.19, "forward_positives": 1021},
        "results_text": ("Lift +0.745 [+0.413, +1.107], PR +0.0037, ROC "
                         "+0.0337 [+0.0061, +0.0571] — all clear of zero, "
                         "including the ROC that was conceded earlier. "
                         "Full model catches 393 of 1,021 future bans in "
                         "the top slice vs core's 319; 205 caught only by "
                         "full vs 131 only by core; 19% overlap."),
        "lesson": ("The toolbox's total win replicates run after run; "
                   "credit assignment among overlapping sources does not. "
                   "Read the union, not the ranking."),
        "evidence": "SOURCE_ABLATION_OOF_FULL + digest in chat 2026-07-28",
        "retrospective": True,
    },
]


def git_milestones(repo: str | Path | None = None,
                   max_rows: int = 400) -> list[dict]:
    """Commit dates + subjects from git — the deep history (2026-05-29 on).
    Failure-tolerant: returns [] when git or the repo is unavailable."""
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--date=short",
             "--format=%ad\t%h\t%s"],
            capture_output=True, text=True, timeout=15,
            cwd=str(repo) if repo else None)
        rows = []
        for ln in out.stdout.splitlines()[:max_rows]:
            parts = ln.split("\t", 2)
            if len(parts) == 3:
                rows.append({"date": parts[0], "commit": parts[1],
                             "subject": parts[2]})
        return rows
    except Exception:
        return []


def to_markdown(entries: list[dict]) -> str:
    L = ["# RELEASE HISTORY — reconstructed timeline", ""]
    L.append("_Curated after the fact from the working chat, the git log, "
             "and the repo docs. Numbers are verbatim from the reports "
             "quoted at the time. These entries inform; they never "
             "promote — the registry's decisions only ever come from "
             "properly registered runs._")
    for e in entries:
        L.append("")
        d = e["date"] + ("~" if e.get("date_approx") else "")
        L.append(f"## {d} — {e['title']}")
        L.append("")
        L.append(f"**What we were trying to change:** {e['goal']}")
        L.append(f"**Why:** {e['why']}")
        if e.get("changes"):
            L.append("**Changes:**")
            for c in e["changes"]:
                L.append(f"- {c}")
        L.append(f"**Result:** {e.get('results_text', '—')}")
        if e.get("lesson"):
            L.append(f"**Lesson:** {e['lesson']}")
        L.append(f"_Evidence: {e.get('evidence', '—')}_")
    return "\n".join(L)


def write_history(data_root: str | Path) -> Path:
    p = Path(data_root) / "model_a" / "run_history.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for e in HISTORY:
            fh.write(json.dumps(e) + "\n")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root",
                    default=os.environ.get("MEDICAID_DATA_ROOT",
                                           str(Path.home() / "Desktop/data")))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.write:
        p = write_history(args.data_root)
        print(f"[run_history] {len(HISTORY)} curated entries -> {p}")
    if args.report or not args.write:
        print(to_markdown(HISTORY))


if __name__ == "__main__":
    main()
