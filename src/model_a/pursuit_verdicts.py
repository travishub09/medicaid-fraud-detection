"""pursuit_verdicts.py — the operator's standing verdicts on organizations, honoured by
every run's dossier pick.

Why (2026-10-28, the first current-day run): its operator-book dossier named Ideal
Option (HOLD since 2026-10-26: a federal court enforced a DOJ civil investigative demand)
and HealthTrackRx (WATCH: a 2019 qui tam, dismissed 2025) in its top ten, both researched
for v26 the week before. A verdict is a fact about the ORGANIZATION, not about a run, so
it lives outside the run folders in ONE file under the data root,
``<data_root>/model_a/pursuit_verdicts.csv`` (org_id, org_name, verdict, date, reason,
run), and the dossier's target pick sets those rows aside and lists them in
``dossier_verdicts_applied.csv``. The graded books never read it (a verdict is an
operational decision; the grade stays a measurement).

Verdicts: pursue (already a case: no new research credits), hold (government already
there), watch (nothing to act on yet; re-check when data lands), set_aside (not a case:
national arm, pass-through, nonprofit, wrong shape). Every verdict is the operator's word
(`--add`), never a runner's.

    python -m src.model_a.pursuit_verdicts --init            # write the seed file (v26 read, 2026-10-26/27)
    python -m src.model_a.pursuit_verdicts --list
    python -m src.model_a.pursuit_verdicts --add "org:pac:6709013105" hold "DOJ CID enforced 2026-09-02" --name "IDEAL OPTION, PLLC"
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

import pandas as pd

COLS = ["org_id", "org_name", "verdict", "date", "reason", "run"]
VERDICTS = ("pursue", "hold", "watch", "set_aside")
FILE_NAME = "pursuit_verdicts.csv"

# The v26 operator-book read (docs: the pursuit packet of 2026-10-27). Organizations only.
SEED = [
    ("org:name:TRUETOX LABORATORIES", "TRUETOX LABORATORIES LLC", "pursue", "2026-10-27",
     "in research (NJ comptroller 2021 audit; public-disclosure question with counsel)", "frozen_2023-12_v26"),
    ("org:pac:6204722200", "HEALTHTRACKRX INDIANA, INC", "watch", "2026-10-27",
     "qui tam 4:19-cv-00613 E.D. Tex. filed 2019, dismissed 2025-03; re-check on 2025 data", "frozen_2023-12_v26"),
    ("org:pac:6709013105", "IDEAL OPTION, PLLC", "hold", "2026-10-26",
     "federal court enforced a DOJ civil investigative demand 2026-09-02: active FCA investigation", "frozen_2023-12_v26"),
    ("org:name:MINI PHARMACY ENTERPRISES", "MINI PHARMACY ENTERPRISES", "watch", "2026-10-27",
     "CMS DME revocation 2025-08 (standards, not fraud); changed hands twice", "frozen_2023-12_v26"),
    ("org:name:SIMPLE LABORATORIES", "SIMPLE LABORATORIES", "watch", "2026-10-27",
     "COVID-era lab; civil matters only, no government action found", "frozen_2023-12_v26"),
    ("org:name:STAVROS CENTER FOR INDEPENDENT LIVING", "STAVROS CENTER FOR INDEPENDENT LIVING", "set_aside", "2026-10-27",
     "MassHealth PCA fiscal intermediary: attendant wages pass through its numbers", "frozen_2023-12_v26"),
    ("org:name:UNILAB CORPORATION", "UNILAB CORPORATION", "set_aside", "2026-10-27",
     "Quest Diagnostics under an old name", "frozen_2023-12_v26"),
    ("org:name:DIAGNOSTIC LABORATORY OF OKLAHOMA", "DIAGNOSTIC LABORATORY OF OKLAHOMA LLC", "set_aside", "2026-10-27",
     "Quest Diagnostics joint venture", "frozen_2023-12_v26"),
    ("org:name:LABORATORY SERVICES COOPERATIVE", "LABORATORY SERVICES COOPERATIVE", "set_aside", "2026-10-27",
     "nonprofit lab cooperative; only public matter a data breach", "frozen_2023-12_v26"),
    ("org:name:KENNEDY DONOVAN CENTER", "KENNEDY DONOVAN CENTER", "set_aside", "2026-10-27",
     "nonprofit early-intervention provider; minor program findings", "frozen_2023-12_v26"),
    ("org:name:CURATIVE LABS", "CURATIVE LABS INC.", "set_aside", "2026-10-28",
     "national-scale COVID testing company, not an operator a relator comes from", "current_202610_cd1"),
    ("org:pac:5991710147", "AEGIS SCIENCES CORPORATION", "set_aside", "2026-10-28",
     "national toxicology laboratory", "current_202610_cd1"),
]


def default_path() -> Path:
    root = Path(os.environ.get("MEDICAID_DATA_ROOT", str(Path.home() / "Desktop/data")))
    return root / "model_a" / FILE_NAME


def load_verdicts(path: Path | None = None, run_dir: Path | None = None) -> pd.DataFrame:
    """The verdicts table: an explicit path, else <run_dir>/../pursuit_verdicts.csv (the
    model_a folder beside the run), else the data root's. Missing file = empty table."""
    cands = [Path(path)] if path else []
    if run_dir is not None:
        cands.append(Path(run_dir).resolve().parent / FILE_NAME)
    cands.append(default_path())
    for p in cands:
        if p.exists():
            t = pd.read_csv(p, dtype=str).fillna("")
            for c in COLS:
                if c not in t.columns:
                    t[c] = ""
            t["verdict"] = t["verdict"].str.strip().str.lower()
            return t[COLS]
    return pd.DataFrame(columns=COLS)


def apply_verdicts(orgs: pd.DataFrame, verdicts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split an org table into (kept, set aside) by org_id, then by exact upper-cased
    name for rows whose org_id is name-keyed. Set-aside rows carry verdict + reason."""
    if not len(verdicts) or not len(orgs):
        return orgs, orgs.iloc[0:0].assign(verdict="", verdict_reason="")
    v = verdicts[verdicts["verdict"].isin(VERDICTS)]
    by_id = dict(zip(v["org_id"].astype(str), v.index))
    by_name = dict(zip(v["org_name"].astype(str).str.upper().str.strip(), v.index))
    ids = orgs["org_id"].astype(str)
    names = orgs["org_name"].astype(str).str.upper().str.strip()
    hit = ids.map(by_id)
    hit = hit.where(hit.notna(), names.map(by_name))
    mask = hit.notna()
    aside = orgs[mask].copy()
    aside["verdict"] = [v.loc[i, "verdict"] for i in hit[mask]]
    aside["verdict_reason"] = [v.loc[i, "reason"] for i in hit[mask]]
    return orgs[~mask], aside


def write_seed(path: Path, force: bool = False) -> Path:
    path = Path(path)
    if path.exists() and not force:
        raise SystemExit(f"{path} exists; use --force to overwrite (or --add to append)")
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(SEED, columns=COLS).to_csv(path, index=False)
    return path


def add_verdict(path: Path, org_id: str, verdict: str, reason: str, org_name: str = "", run: str = "") -> pd.DataFrame:
    if verdict not in VERDICTS:
        raise SystemExit(f"verdict must be one of {VERDICTS}")
    t = load_verdicts(path) if Path(path).exists() else pd.DataFrame(columns=COLS)
    t = t[t["org_id"].astype(str) != str(org_id)]          # a new verdict replaces the old one
    row = pd.DataFrame([[org_id, org_name, verdict, date.today().isoformat(), reason, run]], columns=COLS)
    t = pd.concat([t, row], ignore_index=True)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    t.to_csv(path, index=False)
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=None, help=f"verdicts file (default <data_root>/model_a/{FILE_NAME})")
    ap.add_argument("--init", action="store_true", help="write the seed file")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add", nargs=3, metavar=("ORG_ID", "VERDICT", "REASON"))
    ap.add_argument("--name", default="", help="org name for --add")
    ap.add_argument("--run", default="", help="run tag for --add")
    a = ap.parse_args()
    p = Path(a.path) if a.path else default_path()
    if a.init:
        print(f"[pursuit_verdicts] seed written: {write_seed(p, a.force)} ({len(SEED)} rows)")
    if a.add:
        t = add_verdict(p, a.add[0], a.add[1], a.add[2], a.name, a.run)
        print(f"[pursuit_verdicts] {a.add[0]} -> {a.add[1]}; {len(t)} rows in {p}")
    if a.list or not (a.init or a.add):
        t = load_verdicts(p)
        print(f"[pursuit_verdicts] {len(t)} verdict(s) in {p if p.exists() else '(no file)'}")
        if len(t):
            print(t[["org_name", "verdict", "date", "reason"]].to_string(index=False))


if __name__ == "__main__":
    main()
