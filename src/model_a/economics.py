"""
economics.py — outcome economics: what each initiative cost and what it bought.

The CPO item nobody builds until it's too late to backfill: cost per
incremental unit, by initiative. Agent credits spent on the harvest, hours on
a rebuild, dollars on a data license — logged against what they gained
(identifiers recovered, labels added, extra catches). The chart that
eventually prices the company (recovery dollars per model version) starts
as this humble ledger.

  log     python -m src.model_a.economics --log --initiative manus_enrichment \
              --cost-credits 900 --gained identifiers=212 \
              --note "gap enrichment round 1"
  report  python -m src.model_a.economics --report

Append-only jsonl, same discipline as the registry: entries are never
edited, corrections are new entries with a note.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def parse_gained(spec: str) -> dict[str, float]:
    """'identifiers=212,labels=3121' -> {'identifiers': 212.0, ...}"""
    out: dict[str, float] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"gained spec '{part}' must be name=number")
        k, v = part.split("=", 1)
        out[k.strip()] = float(v)
    return out


def log_entry(path: str | Path, initiative: str, cost_credits: float = 0.0,
              cost_dollars: float = 0.0, cost_hours: float = 0.0,
              gained: dict[str, float] | None = None,
              note: str = "") -> dict:
    row = {"initiative": initiative.strip(),
           "cost_credits": float(cost_credits),
           "cost_dollars": float(cost_dollars),
           "cost_hours": float(cost_hours),
           "gained": gained or {}, "note": note,
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
    """Per-initiative totals + unit costs for every gained metric."""
    by: dict = {}
    for r in rows:
        v = by.setdefault(r.get("initiative", "?"),
                          {"cost_credits": 0.0, "cost_dollars": 0.0,
                           "cost_hours": 0.0, "gained": {}, "entries": 0})
        v["entries"] += 1
        for k in ("cost_credits", "cost_dollars", "cost_hours"):
            v[k] += float(r.get(k) or 0)
        for g, n in (r.get("gained") or {}).items():
            v["gained"][g] = v["gained"].get(g, 0.0) + float(n)
    for v in by.values():
        unit: dict = {}
        for g, n in v["gained"].items():
            if n > 0:
                if v["cost_credits"]:
                    unit[g] = {"credits_per": round(v["cost_credits"] / n, 2)}
                if v["cost_dollars"]:
                    unit.setdefault(g, {})["dollars_per"] = round(
                        v["cost_dollars"] / n, 2)
        v["unit_costs"] = unit
    return by


def to_markdown(by: dict) -> str:
    L = ["# ECONOMICS — cost per incremental unit, by initiative", ""]
    if not by:
        return "\n".join(L + ["_No entries yet. Log costs as they happen; "
                              "this ledger cannot be backfilled honestly._"])
    L.append("| initiative | entries | credits | dollars | hours | "
             "gained | unit cost |")
    L.append("|---|--:|--:|--:|--:|---|---|")
    for name, v in sorted(by.items()):
        gained = ", ".join(f"{k}: {v_:g}" for k, v_ in
                           sorted(v["gained"].items())) or "—"
        units = "; ".join(
            f"{g}: " + ", ".join(f"{uk.replace('_per', '')} {uv:g}"
                                 for uk, uv in u.items())
            for g, u in sorted(v["unit_costs"].items())) or "—"
        L.append(f"| {name} | {v['entries']} | {v['cost_credits']:g} "
                 f"| {v['cost_dollars']:g} | {v['cost_hours']:g} "
                 f"| {gained} | {units} |")
    L.append("")
    L.append("_Unit cost = total cost / total gained, per initiative. When "
             "lead dispositions accumulate, the next column is cost per "
             "pursued lead — the number that prices the pipeline._")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root",
                    default=os.environ.get("MEDICAID_DATA_ROOT",
                                           str(Path.home() / "Desktop/data")))
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--initiative", default=None)
    ap.add_argument("--cost-credits", type=float, default=0.0)
    ap.add_argument("--cost-dollars", type=float, default=0.0)
    ap.add_argument("--cost-hours", type=float, default=0.0)
    ap.add_argument("--gained", default="",
                    help="name=number[,name=number...] e.g. identifiers=212")
    ap.add_argument("--note", default="")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    path = Path(args.data_root) / "model_a" / "economics.jsonl"
    if args.log:
        if not args.initiative:
            raise SystemExit("--log needs --initiative")
        row = log_entry(path, args.initiative, args.cost_credits,
                        args.cost_dollars, args.cost_hours,
                        parse_gained(args.gained), args.note)
        print(f"[economics] logged {row['initiative']}")
    rows = load_log(path)
    report = path.with_name("ECONOMICS.md")
    report.write_text(to_markdown(summarize(rows)), encoding="utf-8")
    print(f"[economics] {len(rows)} entries -> {report}")


if __name__ == "__main__":
    main()
