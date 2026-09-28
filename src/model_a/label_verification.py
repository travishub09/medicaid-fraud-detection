"""
label_verification.py — the human check on the strict FCA label, delegated to the research
agent as a FIRST pass (a person still reads the rows it flags).

Input: ``verification_sample.csv`` from ``fca_label_export`` (a random sample of label
rows: NPI, defendant, tier, source URL, dates). Each task gets a batch of rows and must,
for every row, open the source document, confirm the named defendant is the person or
business on the row, look the NPI up in the NPPES registry, and answer with a verdict:

  confirmed      the source names this defendant AND the NPI is that provider (name, city)
  wrong_person   the source names someone else, or the row's defendant is not a party
  wrong_npi      right defendant, but the NPI belongs to a different provider
  not_a_provider the defendant is real but has no NPI (owner, recruiter, marketer)
  cannot_verify  the source is unreachable or silent

Outputs (``--out-dir``): label_verification_prompts.jsonl + batch csv (review first),
registry / results via the pursuit-dossier Manus plumbing (one dispatcher at a time),
and after ``--poll``: verification_results.csv (one row per sampled label with the
verdict, evidence URL and note) + LABEL_VERIFICATION.md with the tally. A confirmed
rate under 90 percent, or more than one wrong_npi, means the match rules tighten
before any clean-room upload. Verdicts are the agent's reading of public documents;
they are evidence for the operator, never the final word.

  python -m src.model_a.label_verification --sample processed/fca_label/verification_sample.csv --out-dir processed/fca_label/verify
  python -m src.model_a.label_verification --out-dir processed/fca_label/verify --dispatch
  python -m src.model_a.label_verification --out-dir processed/fca_label/verify --poll
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

VERDICTS = ("confirmed", "wrong_person", "wrong_npi", "not_a_provider", "cannot_verify")

PROMPT = (
    "Public-records verification task. Below are {n} rows from a label table. Each row ties a healthcare "
    "provider's National Provider Identifier (NPI) to an already-public enforcement outcome (a Department of "
    "Justice or HHS-OIG announcement, a state action, or an OIG exclusion). For EVERY row: (1) open the "
    "source_url; (2) confirm the defendant named on the row is a party in that document; (3) look the NPI up "
    "in the NPPES NPI Registry (npiregistry.cms.hhs.gov) and confirm it is that provider (name, city, state, "
    "entity type); (4) give a verdict. Use only public sources. Do not contact anyone.\n\n"
    "Verdict values: confirmed (source names this defendant AND the NPI is that provider); wrong_person (the "
    "source names someone else or the defendant is not a party); wrong_npi (right defendant, NPI belongs to a "
    "different provider); not_a_provider (defendant is real but has no NPI: owner, recruiter, marketer); "
    "cannot_verify (source unreachable or silent).\n\n"
    "Return ONLY a JSON array, one object per row, with keys: row (the row number given), npi, verdict (one of "
    "the five values), evidence_url (the page that settles it), note (one sentence). No prose outside the JSON.\n\n"
    "Rows:\n{rows}")


def _oid(sample_path: str, batch_no: int) -> str:
    return f"verify:{hashlib.md5(f'{sample_path}|{batch_no}'.encode()).hexdigest()[:10]}"


def build_prompts(sample: pd.DataFrame, batch_size: int = 10, sample_name: str = "sample") -> list[dict]:
    rows = sample.reset_index(drop=True)
    out = []
    for b in range(0, len(rows), batch_size):
        chunk = rows.iloc[b: b + batch_size]
        lines = []
        for i, r in chunk.iterrows():
            extra = ""
            if str(r.get("role", "") or "").strip():
                extra += f"; role_in_release={r.get('role')}"
            if str(r.get("evidence_strength", "") or "").strip():
                extra += f"; match_strength={r.get('evidence_strength')}"
            lines.append(f"row {i + 1}: npi={r.get('npi', '')}; defendant={r.get('defendant', '') or '(exclusion-list entry, no name)'}; "
                         f"tier={r.get('label_tier', '')}; state={r.get('state', '')}; announced={r.get('announced_date', '')}; "
                         f"source_url={r.get('source_url', '')}; case_id={r.get('case_id', '')}{extra}")
        out.append({"org_id": _oid(sample_name, b // batch_size), "org_name": f"label verification batch {b // batch_size + 1}",
                    "batch": b // batch_size + 1, "rows": [int(i) + 1 for i in chunk.index],
                    "prompt": PROMPT.format(n=len(chunk), rows="\n".join(lines))})
    return out


def write_prompts(prompts: list[dict], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pj = out_dir / "label_verification_prompts.jsonl"
    with open(pj, "w", encoding="utf-8") as fh:
        for p in prompts:
            fh.write(json.dumps(p) + "\n")
    pc = out_dir / "label_verification_batch.csv"
    pd.DataFrame([{"batch": p["batch"], "org_id": p["org_id"], "rows": ",".join(map(str, p["rows"]))} for p in prompts]).to_csv(pc, index=False)
    return pj, pc


def parse_verdicts(output) -> list[dict]:
    """The JSON array from a task's output; tolerant of prose around it."""
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    m = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for o in arr if isinstance(arr, list) else []:
        if not isinstance(o, dict):
            continue
        v = str(o.get("verdict", "")).strip().lower().replace(" ", "_")
        out.append({"row": o.get("row"), "npi": re.sub(r"\D", "", str(o.get("npi") or "")),
                    "verdict": v if v in VERDICTS else "cannot_verify",
                    "evidence_url": str(o.get("evidence_url") or ""), "note": str(o.get("note") or "")[:300]})
    return out


def fold_results(sample: pd.DataFrame, results: dict) -> pd.DataFrame:
    rows = sample.reset_index(drop=True).copy()
    rows["row"] = rows.index + 1
    rows["verdict"], rows["evidence_url"], rows["note"] = "pending", "", ""
    for oid, env in results.items():
        if env.get("status") != "completed":
            continue
        for v in parse_verdicts(env.get("output") or ""):
            try:
                i = int(v["row"]) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(rows):
                rows.loc[i, ["verdict", "evidence_url", "note"]] = [v["verdict"], v["evidence_url"], v["note"]]
    return rows


def report(rows: pd.DataFrame) -> str:
    done = rows[rows["verdict"] != "pending"]
    tally = done["verdict"].value_counts().to_dict()
    n = len(done)
    conf = tally.get("confirmed", 0)
    L = ["# LABEL VERIFICATION (agent first pass; the operator reads every flagged row)", "",
         f"- rows sampled: {len(rows)}; answered: {n}; confirmed: {conf} ({(100 * conf / n):.0f}% of answered)" if n else f"- rows sampled: {len(rows)}; answered: 0",
         "- verdicts: " + ", ".join(f"{k} {v}" for k, v in tally.items()), ""]
    if n:
        bad = done[done["verdict"].isin(["wrong_person", "wrong_npi"])]
        L += [f"- match-rule verdict: {'TIGHTEN before any upload' if (conf / n < 0.9 or (done['verdict'] == 'wrong_npi').sum() > 1) else 'acceptable for the POC upload'}", ""]
        if len(bad):
            L += ["## Flagged rows (read these)", ""]
            for r in bad.itertuples():
                L.append(f"- row {r.row}: {r.verdict}; NPI {r.npi}; {r.defendant or '(exclusion entry)'}; tier {r.label_tier}; {r.note} [{r.evidence_url}]")
        by_tier = done.groupby("label_tier")["verdict"].apply(lambda s: f"{(s == 'confirmed').sum()}/{len(s)} confirmed")
        L += ["", "## By tier", ""] + [f"- {t}: {v}" for t, v in by_tier.items()]
        if "evidence_strength" in done.columns:
            by_s = done.groupby("evidence_strength")["verdict"].apply(lambda s: f"{(s == 'confirmed').sum()}/{len(s)} confirmed")
            L += ["", "## By evidence strength (the ruler for --min-strength on the next export)", ""]
            L += [f"- {s}: {v}" for s, v in by_s.items()]
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", default=None, help="verification_sample.csv from fca_label_export")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--dispatch", action="store_true")
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts = out_dir / "label_verification_prompts.jsonl"
    registry = out_dir / "label_verification_tasks.json"
    results_p = out_dir / "label_verification_results.json"
    sample_copy = out_dir / "verification_sample.csv"
    if a.status:
        reg = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {}
        print(f"registry: {len(reg)} tasks, {sum(1 for v in reg.values() if v.get('task_id'))} with ids")
        return
    if a.dispatch:
        from src.model_a.pursuit_dossier import dispatch_research
        print(f"label verification dispatch: {dispatch_research(prompts, registry)}")
        return
    if a.poll:
        from src.model_a.pursuit_dossier import poll_research
        results = poll_research(registry, results_p)
        sample = pd.read_csv(sample_copy, dtype=str)
        rows = fold_results(sample, results)
        rows.to_csv(out_dir / "verification_results.csv", index=False)
        rep = out_dir / "LABEL_VERIFICATION.md"
        rep.write_text(report(rows), encoding="utf-8")
        done = int((rows["verdict"] != "pending").sum())
        print(f"label verification poll: {done}/{len(rows)} rows answered -> {out_dir / 'verification_results.csv'}\n  report: {rep}")
        return
    if not a.sample:
        raise SystemExit("--sample is required to build the batch")
    sample = pd.read_csv(a.sample, dtype=str)
    sample.to_csv(sample_copy, index=False)            # the rows the verdicts refer to, frozen
    ps = build_prompts(sample, a.batch_size, Path(a.sample).name)
    pj, pc = write_prompts(ps, out_dir)
    print(f"label verification: {len(sample)} rows in {len(ps)} tasks -> {pj}\n  review: {pc}\n  then --dispatch, later --poll")


if __name__ == "__main__":
    main()
