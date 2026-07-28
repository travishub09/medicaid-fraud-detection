"""
lead_dispositions.py — what actually happened to each lead: the real KPI.

Catching future-banned providers is a PROXY metric. The product's output is
leads, and the KPI that prices the business is what humans decide about them:
pursued, killed (and why), referred to counsel, signed. This module is the
append-only log that makes "precision as judged by humans, by model version"
computable — the feedback loop that no offline test can substitute for.

  log      python -m src.model_a.lead_dispositions --log --lead <npi-or-org> \
               --model-version v2_withcases --disposition killed \
               --reason "innocent explanation: FQHC grant program"
  report   python -m src.model_a.lead_dispositions --report

Dispositions: surfaced (default state, implicit) -> reviewing -> one of
  pursued    worth investigator time, actively worked
  killed     reviewed and rejected — the REASON is the gold: kill reasons are
             tomorrow's false-positive screens
  referred   handed to counsel
  signed     retained relator / engagement signed
  parked     not now, revisit

The log stores the model version that surfaced the lead, so precision-in-
practice can be compared release over release. Append-only: a lead's history
is every row it ever got; the latest row is its current state.

Leads context for review — a disposition describes OUR decision about a lead,
never a fact about a person.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DISPOSITIONS = ("reviewing", "pursued", "killed", "referred", "signed",
                "parked")


def log_disposition(path: str | Path, lead: str, model_version: str,
                    disposition: str, reason: str = "", by: str = "") -> dict:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}")
    row = {"lead": str(lead).strip(), "model_version": model_version,
           "disposition": disposition, "reason": reason, "by": by,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def load_log(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in
            p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def summarize(rows: list[dict]) -> dict:
    """Per-model-version scoreboard from each lead's LATEST disposition."""
    latest: dict = {}
    for r in rows:                                   # append-only: last wins
        latest[(r.get("model_version"), r.get("lead"))] = r
    by_ver: dict = {}
    for (ver, _lead), r in latest.items():
        v = by_ver.setdefault(ver, {"total": 0, "by_disposition": {},
                                    "kill_reasons": {}})
        v["total"] += 1
        d = r.get("disposition")
        v["by_disposition"][d] = v["by_disposition"].get(d, 0) + 1
        if d == "killed" and r.get("reason"):
            key = r["reason"].strip().lower()[:60]
            v["kill_reasons"][key] = v["kill_reasons"].get(key, 0) + 1
    for v in by_ver.values():
        good = sum(v["by_disposition"].get(k, 0)
                   for k in ("pursued", "referred", "signed"))
        judged = good + v["by_disposition"].get("killed", 0)
        v["precision_in_practice"] = (good / judged) if judged else None
        v["judged"] = judged
    return by_ver


def to_markdown(by_ver: dict) -> str:
    L = ["# LEAD DISPOSITIONS — precision as judged by humans", ""]
    if not by_ver:
        return "\n".join(L + ["_No dispositions logged yet. Start with "
                              "--log; the loop only exists if it's fed._"])
    L.append("| model version | leads logged | judged | pursued | killed | "
             "referred | signed | parked | precision-in-practice |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for ver, v in sorted(by_ver.items()):
        bd = v["by_disposition"]
        pip = (f"{v['precision_in_practice']:.0%}"
               if v["precision_in_practice"] is not None else "—")
        L.append(f"| {ver} | {v['total']} | {v['judged']} "
                 f"| {bd.get('pursued', 0)} | {bd.get('killed', 0)} "
                 f"| {bd.get('referred', 0)} | {bd.get('signed', 0)} "
                 f"| {bd.get('parked', 0)} | {pip} |")
    L.append("")
    L.append("_Precision-in-practice = (pursued + referred + signed) / "
             "judged. Compare across versions: the offline lift delta "
             "predicts this number; this number is the truth._")
    kills = {}
    for ver, v in by_ver.items():
        for reason, n in v["kill_reasons"].items():
            kills[reason] = kills.get(reason, 0) + n
    if kills:
        L.append("")
        L.append("## Top kill reasons (tomorrow's false-positive screens)")
        L.append("")
        for reason, n in sorted(kills.items(), key=lambda kv: -kv[1])[:10]:
            L.append(f"- ({n}) {reason}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root",
                    default=os.environ.get("MEDICAID_DATA_ROOT",
                                           str(Path.home() / "Desktop/data")))
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--lead", default=None, help="NPI or org id/name key")
    ap.add_argument("--model-version", default=None)
    ap.add_argument("--disposition", default=None, choices=DISPOSITIONS)
    ap.add_argument("--reason", default="")
    ap.add_argument("--by", default="")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    path = Path(args.data_root) / "model_a" / "lead_dispositions.jsonl"
    if args.log:
        if not (args.lead and args.model_version and args.disposition):
            raise SystemExit("--log needs --lead, --model-version, "
                             "--disposition")
        row = log_disposition(path, args.lead, args.model_version,
                              args.disposition, args.reason, args.by)
        print(f"[dispositions] logged {row['lead']} -> {row['disposition']}")
    rows = load_log(path)
    report = path.with_name("LEAD_DISPOSITIONS.md")
    report.write_text(to_markdown(summarize(rows)), encoding="utf-8")
    print(f"[dispositions] {len(rows)} log rows -> {report}")


if __name__ == "__main__":
    main()
