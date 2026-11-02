"""policy/features.py — policy-aware FEATURES per billing NPI (batch 5b-F, 2026-11-02).

THE RULE IS THE RULER, as a feature. Every scheme metric on the matrix is a within-
taxonomy percentile: "more definitive panels than peer labs" is the comparison. The
state policy library (src/policy/registry.py) holds a better ruler where it has a row:
the PAYER'S OWN NUMBER. A lab billing 25 definitive panels per patient-year in a state
that covers 12 is over the rule by 13 whatever its peers do; an adult day centre at a
census of 1.4 licences in a state that bars a second shift is over the building. This
module writes that excess as a column:

    pol_<topic>_<param>_excess   = max(0, observed / the state's parameter - 1)
                                   NULL  when the state has no numeric rule for the
                                         parameter in the library (or the provider has
                                         no observation on the family: absent, never 0)
                                   0     when the provider is within the rule
    pol_<topic>_rule_present     = 1 when the state has ANY numeric parameter for the
                                   topic in the library, else 0

The parameter keys come from ``registry.TOPICS`` at call time: a parameter the lane
learns tomorrow flows in without a code change as long as it has an observed quantity
(``OBSERVED_RULES`` below, or a column named ``obs_<param>`` on the per-NPI frame, read
as a plain cap). A parameter with no observed quantity is listed in the report, never
scored. Yes/no parameters are read against the family's share or ratio (a rule that
REQUIRES a presumptive screen first is read against definitive-over-presumptive lines;
a rule that bars 60-minute sessions without justification is read against the
60-minute share), and the per-diem second-shift rule halves the census it allows
(occupancy / 2 when a second shift is declared lawful, the New Jersey read).

Observed quantities come from ONE DuckDB pass over the spending fact
(``observed_from_parquet``: the fact never enters pandas; the same patient-month
convention as ingest_cms.sector_schemes: SUM(total_patients) over months) or from any
per-NPI frame that already carries the columns (``pc_lines_per_patient_month``,
``hh_lines_per_patient_month``, ``psy_60min_share``, ``psy_interactive_share``,
``occupancy_ratio`` from the capacity table, ...). The frame must carry the provider's
state (``practice_state`` / ``addr_state`` / ``state``).

INTEGRATION (written here because this module edits no existing file):
  provider_features_export._run_npi_adapters, right after the sector_schemes block:
      from src.policy import registry as _preg
      from src.policy.features import observed_from_parquet, policy_features
      obs = observed_from_parquet(spend_path, asof_cutoff=asof_cutoff)
      obs = join_m1(obs, provider_dim[["npi", "addr_state"]].rename(columns={"addr_state": "practice_state"}),
                    source="policy/state")
      pol = policy_features(_preg.load_policy(data_root), obs, asof=asof_cutoff)
      frames["policy"] = pol            # join_m1(matrix, pol, source="policy", on="npi")
  feature_partition: every ``pol_*`` column -> partition group "policy", numeric, trainable,
  never a label and never leakage-adjacent (a state's billing rule is public and dated;
  ``asof`` keeps a rule effective after the freeze out of a frozen run).

    python -m src.policy.features --data-root D --in <per-NPI parquet> --out <parquet> [--asof 2023-12]
    python -m src.policy.features --data-root D --fact processed/spending_fact.parquet \
        --provider-dim processed/provider_dim.parquet --out <parquet>
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.policy import registry as preg

DAYS_PER_MONTH = 30.4
WEEKS_PER_MONTH = 4.345
DEFAULT_PC_UNIT_MINUTES = 15.0          # T1019 is a 15-minute unit nationally unless the state says otherwise

# the HCPCS families behind the observed quantities (theory_screens' per-diem and
# transport numerators, sector_schemes' lab / personal-care / home-health / psychotherapy
# families; copied by value so this module edits nothing)
_LAB_DEF = ("G0480", "G0481", "G0482", "G0483", "G0659")
_LAB_PRES = ("80305", "80306", "80307")
_PERDIEM = ("H0017", "H0018", "H0019", "H2013", "H2020", "H2034", "H2036", "H0035", "S9480",
            "S5102", "T2016", "T2031", "T2033", "T1020")
_NEMT = ("T2001", "T2002", "T2003", "T2004", "T2005", "T2007", "T2049",
         "A0080", "A0090", "A0100", "A0110", "A0120", "A0130", "A0140")

KINDS = ("cap", "ratio_if_required", "ratio_if_forbidden", "share_if_required", "share_if_forbidden",
         "second_shift", "pc_hours_per_day")


@dataclass(frozen=True)
class ObservedRule:
    column: str          # the per-NPI observed column
    kind: str            # one of KINDS
    scale: float = 1.0   # observed x scale is in the parameter's unit (per year, per week, per day)
    note: str = ""


# param key -> how the provider's own numbers are read against it. The TOPIC a parameter
# belongs to comes from registry.TOPICS, never from here.
OBSERVED_RULES: dict[str, ObservedRule] = {
    # drug_testing
    "max_definitive_per_year": ObservedRule("lab_def_lines_per_patient_month", "cap", 12.0,
                                            "definitive panels per patient-month x 12 against the covered number a year"),
    "max_presumptive_per_year": ObservedRule("lab_pres_lines_per_patient_month", "cap", 12.0,
                                             "presumptive screens per patient-month x 12"),
    "requires_presumptive_first": ObservedRule("lab_def_to_pres_ratio", "ratio_if_required", 1.0,
                                               "definitive lines over presumptive lines where the state requires a screen first"),
    "lab_reflex_allowed": ObservedRule("lab_def_to_pres_ratio", "ratio_if_forbidden", 1.0,
                                       "the same ratio where the state bars laboratory reflex"),
    # perdiem_day_program
    "max_days_per_week": ObservedRule("perdiem_lines_per_patient_month", "cap", 1.0 / WEEKS_PER_MONTH,
                                      "per-diem lines per patient-month / 4.345 against the days a week allowed"),
    "census_may_exceed_license": ObservedRule("occupancy_ratio", "ratio_if_forbidden", 1.0,
                                              "peak census over licensed places where the state bars exceeding the licence"),
    "second_shift_allowed": ObservedRule("occupancy_ratio", "second_shift", 1.0,
                                         "occupancy / 2 when a second shift is lawful, else occupancy"),
    # personal_care
    "max_hours_per_day_per_beneficiary": ObservedRule("pc_lines_per_patient_month", "pc_hours_per_day", 1.0,
                                                      "units x the state's unit minutes / 60 / 30.4 against the daily cap"),
    # home_health
    "max_visits_per_week": ObservedRule("hh_lines_per_patient_month", "cap", 1.0 / WEEKS_PER_MONTH,
                                        "home-health visits per patient-month / 4.345"),
    # lab_panels
    "max_lab_lines_per_day": ObservedRule("lab_lines_per_patient_month", "cap", 1.0 / DAYS_PER_MONTH,
                                          "lab lines per patient-month / 30.4 (a monthly average: conservative)"),
    # behavioral_health
    "max_units_per_day": ObservedRule("bh_lines_per_patient_month", "cap", 1.0 / DAYS_PER_MONTH,
                                      "H-code units per patient-month / 30.4"),
    # psychotherapy
    "max_sessions_per_week": ObservedRule("psy_lines_per_patient_month", "cap", 1.0 / WEEKS_PER_MONTH,
                                          "psychotherapy sessions per patient-month / 4.345"),
    "psy_60_requires_justification": ObservedRule("psy_60min_share", "share_if_required", 1.0,
                                                  "the 60-minute share where the state demands justification"),
    "interactive_complexity_payable": ObservedRule("psy_interactive_share", "share_if_forbidden", 1.0,
                                                   "the 90785 share where the state does not pay it"),
    # nemt
    "max_legs_per_day": ObservedRule("nemt_lines_per_patient_month", "cap", 1.0 / DAYS_PER_MONTH,
                                     "transport legs per patient-month / 30.4"),
}

_STATE_COLS = ("practice_state", "addr_state", "state")


def _in(codes) -> str:
    return "(hcpcs IN (" + ",".join(f"'{c}'" for c in sorted(codes)) + "))"


def observed_from_parquet(spending_path: str | Path, asof_cutoff: str | None = None) -> pd.DataFrame:
    """Per-NPI observed quantities straight from the spending parquet (one DuckDB pass;
    the fact never enters pandas). Intensities are lines per patient-month (SUM of the
    monthly patient counts over the family, the sector_schemes convention); NULL where
    the provider has no lines on the family. ``asof_cutoff`` (YYYY-MM) keeps service
    months on/before it."""
    from src.attempt_2.duck import connect as _duck
    from src.ingest_cms.sector_schemes import (HH_CODES, PSY_60_CODES, PSY_ALL_CODES, PSY_INTERACTIVE_BASE_CODES,
                                               PSY_INTERACTIVE_CODES, _BH_PRED, _LAB_PRED, _PC_PRED)
    con = _duck()
    p = str(spending_path).replace("'", "''")
    where = f"WHERE CAST(service_month AS VARCHAR) <= '{str(asof_cutoff)[:7]}-99'" if asof_cutoff else ""
    fam = {
        "lab_def": _in(_LAB_DEF), "lab_pres": _in(_LAB_PRES), "lab": _LAB_PRED, "pc": _PC_PRED,
        "hh": _in(HH_CODES), "bh": _BH_PRED, "psy": _in(PSY_ALL_CODES), "psy60": _in(PSY_60_CODES),
        "psy_int": _in(PSY_INTERACTIVE_CODES), "psy_int_base": _in(PSY_INTERACTIVE_BASE_CODES),
        "perdiem": _in(_PERDIEM), "nemt": _in(_NEMT),
    }
    sums = ",\n".join(
        f"SUM(CASE WHEN {pred} THEN lines ELSE 0 END) AS {k}_lines, "
        f"SUM(CASE WHEN {pred} THEN patients ELSE 0 END) AS {k}_pm"
        for k, pred in fam.items())
    df = con.execute(f"""
        WITH b AS (
            SELECT CAST(billing_npi AS VARCHAR) AS npi,
                   UPPER(TRIM(CAST(hcpcs_code AS VARCHAR))) AS hcpcs,
                   SUM(CAST(total_claim_lines AS DOUBLE)) AS lines,
                   SUM(CAST(total_patients AS DOUBLE)) AS patients
            FROM read_parquet('{p}') {where}
            GROUP BY 1, 2)
        SELECT npi, {sums} FROM b GROUP BY npi
    """).df()
    con.close()
    out = pd.DataFrame({"npi": df["npi"].astype(str)})

    def _per_pm(k: str) -> pd.Series:
        return (df[f"{k}_lines"] / df[f"{k}_pm"]).where((df[f"{k}_lines"] > 0) & (df[f"{k}_pm"] > 0))

    out["lab_def_lines_per_patient_month"] = _per_pm("lab_def")
    out["lab_pres_lines_per_patient_month"] = _per_pm("lab_pres")
    # the reflex ratio: NULL without definitive lines; a lab with definitive lines and no
    # screens at all reads the definitive count (every panel unscreened)
    has_def = df["lab_def_lines"] > 0
    out["lab_def_to_pres_ratio"] = (df["lab_def_lines"] / df["lab_pres_lines"].where(df["lab_pres_lines"] > 0, 1.0)).where(has_def)
    out["lab_lines_per_patient_month"] = _per_pm("lab")
    out["pc_lines_per_patient_month"] = _per_pm("pc")
    out["hh_lines_per_patient_month"] = _per_pm("hh")
    out["bh_lines_per_patient_month"] = _per_pm("bh")
    out["psy_lines_per_patient_month"] = _per_pm("psy")
    out["psy_60min_share"] = (df["psy60_lines"] / df["psy_lines"]).where(df["psy_lines"] > 0).clip(0, 1)
    out["psy_interactive_share"] = (df["psy_int_lines"] / df["psy_int_base_lines"]).where(df["psy_int_base_lines"] > 0).clip(0, 1)
    out["perdiem_lines_per_patient_month"] = _per_pm("perdiem")
    out["nemt_lines_per_patient_month"] = _per_pm("nemt")
    metric_cols = [c for c in out.columns if c != "npi"]
    return out[out[metric_cols].notna().any(axis=1)].reset_index(drop=True)


def _state_column(frame: pd.DataFrame) -> str:
    for c in _STATE_COLS:
        if c in frame.columns:
            return c
    raise ValueError(f"the per-NPI frame carries no state column (one of {_STATE_COLS}): "
                     "join provider_dim.addr_state as practice_state first")


def _excess(kind: str, obs: pd.Series, param: float, scale: float, unit_minutes: float) -> pd.Series:
    """The excess for one (kind, state parameter) over a vector of observations; NaN
    observations stay NaN (absent, never 0)."""
    o = pd.to_numeric(obs, errors="coerce").astype(float)
    nan = pd.Series(np.nan, index=o.index, dtype=float)
    if kind == "cap":
        if not (param > 0):
            return nan                      # a cap of 0 or less is not a usable ruler
        return (o * scale / param - 1.0).clip(lower=0.0)
    if kind == "pc_hours_per_day":
        if not (param > 0):
            return nan
        hours = o * unit_minutes / 60.0 / DAYS_PER_MONTH
        return (hours / param - 1.0).clip(lower=0.0)
    if kind == "ratio_if_required":          # rule 1 = the screen (the base) must come first
        return (o - 1.0).clip(lower=0.0) if param >= 0.5 else o.where(o.isna(), 0.0)
    if kind == "ratio_if_forbidden":         # rule 0 = the practice is barred
        return (o - 1.0).clip(lower=0.0) if param < 0.5 else o.where(o.isna(), 0.0)
    if kind == "share_if_required":          # rule 1 = justification required: the share sits under the rule
        return o.clip(0.0, 1.0) if param >= 0.5 else o.where(o.isna(), 0.0)
    if kind == "share_if_forbidden":         # rule 0 = not payable: every unit of the share is excess
        return o.clip(0.0, 1.0) if param < 0.5 else o.where(o.isna(), 0.0)
    if kind == "second_shift":               # rule 1 = a second shift is lawful: the building holds twice the licence
        return ((o / 2.0) - 1.0).clip(lower=0.0) if param >= 0.5 else (o - 1.0).clip(lower=0.0)
    raise ValueError(f"unknown observed-rule kind {kind!r}")


def excess_column(topic: str, param: str) -> str:
    return f"pol_{topic}_{param}_excess"


def present_column(topic: str) -> str:
    return f"pol_{topic}_rule_present"


def rules_for(frame: pd.DataFrame | None = None) -> list[tuple[str, str, ObservedRule]]:
    """(topic, param, rule) for every parameter in registry.TOPICS with an observed
    quantity: the OBSERVED_RULES entry, else a frame column named obs_<param> read as a
    plain cap (so a new parameter flows in without code)."""
    out = []
    for tk, t in preg.TOPICS.items():
        for pk in t.params:
            r = OBSERVED_RULES.get(pk)
            if r is None and frame is not None and f"obs_{pk}" in frame.columns:
                r = ObservedRule(f"obs_{pk}", "cap", 1.0, "a frame column named after the parameter, read as a cap")
            if r is not None:
                out.append((tk, pk, r))
    return out


def feature_columns(frame: pd.DataFrame | None = None) -> list[str]:
    """Every column policy_features can write (for a manifest / the partition map)."""
    cols = []
    for tk in preg.TOPICS:
        cols.append(present_column(tk))
    for tk, pk, _ in rules_for(frame):
        cols.append(excess_column(tk, pk))
    return cols


def _topic_has_numeric(policy: pd.DataFrame, state: str, topic: str, asof: str | None) -> bool:
    if policy is None or not len(policy):
        return False
    m = (policy["state"].astype(str).str.upper() == state) & (policy["topic"] == topic)
    sub = policy[m]
    if asof and len(sub):
        ef = pd.to_datetime(sub["effective_from"], errors="coerce")
        sub = sub[ef.isna() | (ef <= pd.Timestamp(asof))]
    return bool(pd.to_numeric(sub["value_num"], errors="coerce").notna().any()) if len(sub) else False


def policy_features(policy_df: pd.DataFrame, per_npi: pd.DataFrame, asof: str | None = None,
                    program: str = "FFS") -> pd.DataFrame:
    """npi + pol_* columns for every NPI in ``per_npi`` (one row per NPI, never a fan-out).

    ``policy_df`` is the library (registry.load_policy); ``per_npi`` carries ``npi``, a
    state column and the observed columns it has (missing observed columns are skipped,
    their excess columns absent). ``asof`` (YYYY-MM or a date) keeps a rule effective
    after the freeze out of a frozen run. Every output column is numeric."""
    if per_npi["npi"].duplicated().any():
        raise AssertionError("policy_features: the per-NPI frame carries duplicate NPIs (join_m1 it first)")
    st_col = _state_column(per_npi)
    out = pd.DataFrame({"npi": per_npi["npi"].astype(str).to_numpy()}, index=per_npi.index)
    states = per_npi[st_col].fillna("").astype(str).str.strip().str.upper()
    policy = policy_df if policy_df is not None else pd.DataFrame(columns=preg.POLICY_COLS)
    asof_s = (str(asof)[:7] + "-01") if asof and len(str(asof)) == 7 else (str(asof) if asof else None)
    rules = [(tk, pk, r) for tk, pk, r in rules_for(per_npi) if r.column in per_npi.columns]
    # the state's parameters, looked up once per state (policy_param is a per-call scan)
    cache: dict[tuple[str, str, str], float] = {}

    def _param(state: str, topic: str, param: str) -> float:
        k = (state, topic, param)
        if k not in cache:
            cache[k] = preg.policy_param(policy, state, topic, param, program=program, asof=asof_s) if state else float("nan")
        return cache[k]

    for tk in preg.TOPICS:
        pres = pd.Series(0, index=per_npi.index, dtype=int)
        for st in states.unique():
            if st and _topic_has_numeric(policy, st, tk, asof_s):
                pres[states == st] = 1
        out[present_column(tk)] = pres.to_numpy()
    for tk, pk, r in rules:
        col = pd.Series(np.nan, index=per_npi.index, dtype=float)
        for st in states.unique():
            if not st:
                continue
            param = _param(st, tk, pk)
            if pd.isna(param):
                continue                    # NULL: the state has no rule for the parameter
            unit = _param(st, "personal_care", "unit_minutes") if r.kind == "pc_hours_per_day" else float("nan")
            unit = DEFAULT_PC_UNIT_MINUTES if pd.isna(unit) or unit <= 0 else float(unit)
            mask = (states == st).to_numpy()
            col[mask] = _excess(r.kind, per_npi.loc[mask, r.column], float(param), r.scale, unit).to_numpy()
        out[excess_column(tk, pk)] = col.astype(float).to_numpy()
    return out.reset_index(drop=True)


def coverage(policy_df: pd.DataFrame, per_npi: pd.DataFrame, feats: pd.DataFrame) -> list[dict]:
    """Per (topic, param): states with a rule, NPIs observed, NPIs scored, NPIs over the rule."""
    rows = []
    rules = rules_for(per_npi)
    pol = policy_df if policy_df is not None else pd.DataFrame(columns=preg.POLICY_COLS)
    for tk, t in preg.TOPICS.items():
        have = {pk: r for tk2, pk, r in rules if tk2 == tk}
        for pk in t.params:
            r = have.get(pk)
            n_states = 0
            if len(pol):
                m = (pol["topic"] == tk) & (pol["param"] == pk) & pd.to_numeric(pol["value_num"], errors="coerce").notna()
                n_states = int(pol.loc[m, "state"].astype(str).str.upper().nunique())
            c = excess_column(tk, pk)
            if r is None:
                rows.append({"topic": tk, "param": pk, "observed": "", "states_with_rule": n_states,
                             "npis_observed": 0, "npis_scored": 0, "npis_over_rule": 0, "status": "no observed quantity"})
                continue
            obs_n = int(pd.to_numeric(per_npi[r.column], errors="coerce").notna().sum()) if r.column in per_npi.columns else 0
            scored = int(feats[c].notna().sum()) if c in feats.columns else 0
            over = int((feats[c] > 0).sum()) if c in feats.columns else 0
            status = "scored" if scored else ("observed column missing" if r.column not in per_npi.columns else "no state rule reached a provider")
            rows.append({"topic": tk, "param": pk, "observed": r.column, "states_with_rule": n_states,
                         "npis_observed": obs_n, "npis_scored": scored, "npis_over_rule": over, "status": status})
    return rows


def report(rows: list[dict], n_npi: int, asof: str | None) -> str:
    L = ["# POLICY FEATURES", "",
         f"_{n_npi:,} NPIs; `pol_<topic>_<param>_excess` = max(0, observed / the state's parameter - 1), NULL where the "
         f"state has no rule or the provider has no observation, 0 within the rule; `pol_<topic>_rule_present` = the state "
         f"has a numeric parameter for the topic{'; rules effective on/before ' + str(asof) if asof else ''}. A rule is the "
         "payer's own number, read as evidence for a human; the column is a feature, never a label._", "",
         "| topic | param | observed as | states with a rule | NPIs observed | NPIs scored | NPIs over the rule | status |",
         "|---|---|---|--:|--:|--:|--:|---|"]
    for r in rows:
        L.append(f"| {r['topic']} | {r['param']} | {r['observed'] or '-'} | {r['states_with_rule']} | {r['npis_observed']:,} | "
                 f"{r['npis_scored']:,} | {r['npis_over_rule']:,} | {r['status']} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True, help="holds processed/policy/state_policy.parquet (registry.policy_path)")
    ap.add_argument("--in", dest="inp", default=None, help="a per-NPI parquet with npi, a state column and observed columns")
    ap.add_argument("--fact", default=None, help="the spending parquet: observed quantities are computed from it instead")
    ap.add_argument("--provider-dim", default=None,
                    help="processed/provider_dim.parquet for the state when --fact is given (default <data_root>/processed/provider_dim.parquet)")
    ap.add_argument("--policy", default=None, help="override the library parquet path")
    ap.add_argument("--asof", default=None, help="YYYY-MM: only rules effective on/before (a frozen run) and service months <= it")
    ap.add_argument("--program", default="FFS")
    ap.add_argument("--out", required=True, help="parquet; POLICY_FEATURES.md + .json written beside it")
    a = ap.parse_args()
    root = Path(a.data_root)
    policy = preg.load_policy(root, path=Path(a.policy) if a.policy else None)
    if a.fact:
        per = observed_from_parquet(a.fact, asof_cutoff=a.asof)
        pdim_p = Path(a.provider_dim) if a.provider_dim else root / "processed" / "provider_dim.parquet"
        if not pdim_p.exists():
            ap.error(f"--fact needs the provider state: {pdim_p} not found (pass --provider-dim)")
        pdim = pd.read_parquet(pdim_p, columns=["npi", "addr_state"]).drop_duplicates("npi")
        pdim["npi"] = pdim["npi"].astype(str)
        per = per.merge(pdim.rename(columns={"addr_state": "practice_state"}), on="npi", how="left", validate="m:1")
    elif a.inp:
        per = pd.read_parquet(a.inp)
        per["npi"] = per["npi"].astype(str)
    else:
        ap.error("give --in <per-NPI parquet> or --fact <spending parquet>")
    feats = policy_features(policy, per, asof=a.asof, program=a.program)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(out, index=False)
    rows = coverage(policy, per, feats)
    out.with_name("POLICY_FEATURES.md").write_text(report(rows, len(feats), a.asof), encoding="utf-8")
    out.with_name("policy_features.json").write_text(json.dumps({"n_npi": int(len(feats)), "asof": a.asof,
                                                                 "columns": [c for c in feats.columns if c != "npi"],
                                                                 "coverage": rows}, indent=1), encoding="utf-8")
    scored = sum(r["npis_scored"] for r in rows)
    print(f"[policy_features] {len(feats):,} NPIs, {len(feats.columns) - 1} columns, {len(policy):,} library rows, "
          f"{scored:,} parameter scores, {sum(r['npis_over_rule'] for r in rows):,} over a rule -> {out}")


if __name__ == "__main__":
    main()
