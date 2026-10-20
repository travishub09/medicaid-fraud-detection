"""State Medicaid terminated / excluded / sanctioned provider lists → the exclusions schema.

Why: the LEIE lags a provider's last bill by a median of 38 to 42 months and carries an NPI
on about ten percent of rows. A state Medicaid termination for cause is usually the FIRST
public record of the same event chain, is keyed on NPI in most states, and is the
Medicaid-side label that matches Medicaid claims data. Under the ACA every other state
must terminate a provider one state terminated for cause, and OIG may exclude.

What: every ``<ST>_terminations_<yyyymm>.xlsx|csv`` under ``preclean/state_terminations/``
(filed by the operator's sorter) is read with a per-state column map, normalized to the
shared exclusions schema (``EXCLUSION_COLS``), and written as
``processed/exclusions_state_terminations.parquet``. The entity graph merges every
``processed/exclusions_*.parquet`` into exclusion nodes, so the rows widen
``within_2_hops_of_exclusion`` and the PU positive set without further wiring.

``excl_type`` is ``state_term:<ST>:<reason text>``; ``model_a.fca_relevance`` tiers it from
the reason (fraud / false claims / conviction / kickback / credible allegation / program
integrity = hard; license, abuse-of-patient, paperwork, "derivative of a federal exclusion"
= weak; a bare "terminated" with no reason = weak). A state termination NEVER becomes a
hard-tier positive on the word "terminated" alone.

Name-only lists (Alabama) are matched to NPPES by normalized name within the state; only a
UNIQUE, distinctive match is accepted, and the match basis is recorded in ``excl_type``
(``...|name_match``) so a downstream audit can discount it.

Run:
  python -m src.enforcement.state_terminations --in-dir preclean/state_terminations \
      --provider-dim processed/provider_dim.parquet \
      --out processed/exclusions_state_terminations.parquet
Writes the parquet plus ``STATE_TERMINATIONS_REPORT.md`` next to it (rows, NPI share,
name matches, tier counts per state; the reasons the tier table does not know).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.attempt_2.clean_data import (_normalize_name, canonicalize_series,
                                      distinctive_name_key, is_valid_npi)
from src.entity_graph.resolve_entities import norm_org_name

EXCLUSION_COLS = ["npi", "entity_name", "name_key", "excl_type", "excl_date",
                  "reinstate_date", "currently_active"]

# One alias list per logical field; matched case-insensitively against a header with
# whitespace and newlines collapsed. A state's file needs SOME name column; everything
# else is optional and reported.
FIELD_ALIASES: dict[str, list[str]] = {
    "npi": [r"^npi$", r"^npi/atypical id", r"^npi ", r"npi number", r"national provider"],
    "last_name": [r"^last name", r"provider last name", r"^last name or practice name"],
    "first_name": [r"^first name", r"provider first name"],
    "middle_name": [r"^middle name", r"provider mi name"],
    "org_name": [r"^business name", r"provider organization name", r"^excluded entity",
                 r"^individual//entity", r"^individual/entity", r"^name of provider",
                 r"^provider name", r"^name$", r"^entity"],
    "excl_date": [r"^sancdate", r"sanction date", r"^effective date", r"termination effective date",
                  r"^action date", r"^exclusion date", r"^suspension date", r"^date of action",
                  r"^suspension$", r"^termination date"],
    "reinstate_date": [r"termination end date", r"^end date", r"reinstat"],
    # NOTE: the LEIE-style "GENERAL" column is the provider CATEGORY (MEDICAL PRACTICE,
    # MD / PRIVATE CITIZEN ...), never the reason; the statute lives in EXCLTYPE.
    "reason": [r"^reason", r"reason for", r"termination reason", r"^sanction reason", r"^excltype",
               r"^excl\.? ?type", r"^exclusion type", r"^cause", r"^grounds", r"^violation", r"^offense",
               r"^category", r"^sanction type", r"^excluded/terminated", r"^action$", r"^type of action",
               r"^basis"],
    # a SECOND cause column (MS: 'Termination Reason' + 'Sanction Type' = 'F-Individual or
    # Entity Convicted of Fraud'); folded into the reason text so the tier sees both
    "reason2": [r"^sanction type", r"^sanction source", r"^type of (sanction|action)", r"^action type",
                r"^sanction category", r"^basis"],
    "provider_type": [r"provider type", r"^general$", r"provider category", r"profession", r"^role$",
                      r"specialty"],
    "state": [r"^state$", r"^st$"],
    "timeframe": [r"timeframe", r"exclusion period", r"^period"],
}
TERM_WORDS = re.compile(r"exclu|terminat|sanction|suspend|adverse|debar", re.I)


def _clean_header(h) -> str:
    return re.sub(r"\s+", " ", str(h)).strip().lower()


def _find_header_row(raw: pd.DataFrame) -> int:
    best, best_score = 0, -1
    for i in range(min(60, len(raw))):
        vals = [str(v).strip() for v in raw.iloc[i].tolist() if str(v) != "nan" and str(v).strip()]
        joined = " ".join(vals).lower()
        score = (len(vals) >= 2) * (2 * bool(re.search(r"name|entity|provider", joined))
                                   + bool(re.search(r"npi", joined))
                                   + bool(re.search(r"date|eff|suspension", joined))
                                   + bool(re.search(r"reason|type|action|sanction", joined)))
        if score > best_score:
            best, best_score = i, score
        if score >= 4:
            break
    return best


def resolve_fields(header: list) -> dict[str, str]:
    """logical field -> actual column name, first alias that matches wins."""
    cleaned = {c: _clean_header(c) for c in header}
    out: dict[str, str] = {}
    for field, pats in FIELD_ALIASES.items():
        for pat in pats:
            hit = next((c for c, cl in cleaned.items() if re.search(pat, cl) and c not in out.values()), None)
            if hit is not None:
                out[field] = hit
                break
    return out


def read_state_file(path: Path) -> tuple[pd.DataFrame, dict, str]:
    """Read the sheet that looks most like a provider list; return (frame, field map, sheet)."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        xl = pd.ExcelFile(path)
        best = None
        for sh in xl.sheet_names:
            raw = xl.parse(sh, header=None, nrows=80, dtype=str)
            if raw.empty:
                continue
            hdr = _find_header_row(raw)
            df = xl.parse(sh, header=hdr, dtype=str).dropna(how="all")
            fields = resolve_fields(list(df.columns))
            quality = int("org_name" in fields or "last_name" in fields) * 2 + int("npi" in fields) + int("excl_date" in fields)
            key = (quality, len(df))
            if best is None or key > best[0]:
                best = (key, df, fields, sh)
        _, df, fields, sh = best
        return df, fields, sh
    raw = pd.read_csv(path, header=None, nrows=80, dtype=str, encoding_errors="replace", sep=None, engine="python")
    hdr = _find_header_row(raw)
    df = pd.read_csv(path, header=hdr, dtype=str, encoding_errors="replace", sep=None, engine="python").dropna(how="all")
    return df, resolve_fields(list(df.columns)), ""


def state_from_filename(path: Path) -> str:
    m = re.match(r"^([A-Za-z]{2})_", path.name)
    return m.group(1).upper() if m else ""


DATE_FMTS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M:%S", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y")
DATE_LO, DATE_HI = pd.Timestamp("1900-01-01"), pd.Timestamp("2200-01-01")


def _dates(s: pd.Series) -> pd.Series:
    """Text/serial dates → datetime64[ns]. Sentinel 'open' dates (2999-12-31, 9999-..)
    and pre-1900 values become NaT; a far date otherwise arrives as a coarser unit
    and overflows when the per-state frames are concatenated."""
    def _ns(x: pd.Series) -> pd.Series:
        # clamp in whatever unit pandas chose, THEN force nanoseconds
        x = x.where((x >= DATE_LO) & (x < DATE_HI))
        return x.astype("datetime64[ns]")

    txt = s.fillna("").astype(str).str.strip()
    d = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in DATE_FMTS:
        miss = d.isna() & (txt != "")
        if not miss.any():
            break
        try:
            d.loc[miss] = _ns(pd.to_datetime(txt[miss], format=fmt, errors="coerce"))
        except (ValueError, OverflowError, TypeError):
            continue
    miss = d.isna() & (txt != "")
    if miss.any():
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                d.loc[miss] = _ns(pd.to_datetime(txt[miss], errors="coerce"))
            except (ValueError, OverflowError, TypeError):
                pass
    # Excel serials that arrived as text
    ser = pd.to_numeric(txt.where(txt != "", None), errors="coerce")
    fill = _ns(pd.to_datetime(ser, unit="D", origin="1899-12-30", errors="coerce"))
    return d.fillna(fill).astype("datetime64[ns]")


def normalize_state_list(df: pd.DataFrame, fields: dict, state: str,
                         source_file: str = "") -> pd.DataFrame:
    """One state's frame → exclusions schema rows (NPI may be blank; name always present)."""
    # A cell can hold several NPIs ("1104213446\n1174644850", "1053318444; 1881846780"):
    # one row per valid NPI, so neither identifier is lost. Rows with no valid NPI stay
    # one row with a blank NPI (name match may still fill it).
    if "npi" in fields:
        found = df[fields["npi"]].fillna("").astype(str).str.findall(r"(?<!\d)\d{10}(?!\d)")
        found = found.map(lambda L: [x for x in L if is_valid_npi(x)] or [""])
        df = df.assign(_npi=found).explode("_npi").reset_index(drop=True)
        fields = {**fields, "npi": "_npi"}
    g = lambda f: df[fields[f]].fillna("").astype(str).str.strip() if f in fields else pd.Series("", index=df.index)
    org, last, first, mid = g("org_name"), g("last_name"), g("first_name"), g("middle_name")
    person = (last + ", " + (first + " " + mid).str.strip()).str.strip(", ").str.strip()
    person = person.where(person != ",", "")
    name = org.where(org != "", person)
    # a list with ONE combined name column (SC, NC, AL) lands entirely in org; fine
    name = name.str.replace(r"\s+", " ", regex=True).str.strip()
    npi_raw = g("npi").str.replace(r"\D", "", regex=True)
    npi = npi_raw.where(npi_raw.str.len() == 10, "")
    npi = npi.where(npi.map(lambda s: is_valid_npi(s) if s else False), "")
    reason = g("reason").str.replace(r"\s+", " ", regex=True).str.strip()
    reason2 = g("reason2").str.replace(r"\s+", " ", regex=True).str.strip()
    both = (reason != "") & (reason2 != "") & (reason != reason2)
    reason = reason.where(~both, reason + " / " + reason2).where(reason != "", reason2)
    ptype = g("provider_type")
    excl_date = _dates(g("excl_date")) if "excl_date" in fields else pd.Series(pd.NaT, index=df.index)
    rein = _dates(g("reinstate_date")) if "reinstate_date" in fields else pd.Series(pd.NaT, index=df.index)
    row_state = g("state").str.upper().str[:2] if "state" in fields else pd.Series("", index=df.index)
    st = row_state.where(row_state.str.fullmatch(r"[A-Z]{2}").fillna(False), state)
    excl_type = ("state_term:" + st + ":" + reason.where(reason != "", "unspecified")
                 + ptype.where(ptype == "", " [" + ptype + "]"))
    now = pd.Timestamp.today().normalize()
    # the row's own words about the person's occupation (type / category / title /
    # description columns, whatever the state called them): an aide, driver or clerk
    # never holds an NPI, so a blank-NPI row for one must never name-match a clinician
    # (2026-10-20 verification: a Mississippi nurse's aide matched the one FNP of that
    # name in the state)
    from src.enforcement.label_gates import non_npi_role
    occ_cols = [c for c in df.columns if re.search(r"type|categor|title|occupation|profession|descr|role|general|"
                                                   r"specialt|license|classif|position", str(c), re.I)]
    occ_text = pd.Series("", index=df.index, dtype=object)
    for c in occ_cols:
        occ_text = occ_text + " | " + df[c].fillna("").astype(str)
    occ_text = occ_text + " | " + ptype.fillna("").astype(str)
    out = pd.DataFrame({
        "npi": npi,
        "entity_name": name,
        "name_key": name.map(norm_org_name),
        "excl_type": excl_type,
        "excl_date": excl_date,
        "reinstate_date": rein,
        "currently_active": (rein.isna() | (rein >= now)).astype(int),
        "state": st,
        "source_file": source_file,
        "non_npi_role": occ_text.map(non_npi_role).astype(int),
    })
    out = out[(out["name_key"] != "") | (out["npi"] != "")]
    return out.drop_duplicates(["npi", "name_key", "excl_type", "excl_date"]).reset_index(drop=True)


def match_names_to_nppes(rows: pd.DataFrame, provider_dim: pd.DataFrame) -> pd.DataFrame:
    """Fill blank NPIs by a UNIQUE, distinctive normalized-name match within the same state.
    Uses the platform's shared name normalizer (rule 6) on both sides; records the basis."""
    if provider_dim is None or not len(rows):
        return rows
    pd_ = provider_dim.copy()
    pd_["npi"] = pd_["npi"].astype(str)
    st_col = "addr_state" if "addr_state" in pd_.columns else ("practice_state" if "practice_state" in pd_.columns else None)
    name_col = "provider_name" if "provider_name" in pd_.columns else None
    if st_col is None or name_col is None:
        return rows
    # Only the states that actually have blank-NPI rows need a name index; normalizing
    # all ~8M NPPES names for one state's list is the slow path.
    blank_states = set(rows.loc[rows["npi"] == "", "state"].fillna("").astype(str).str.upper())
    blank_states.discard("")
    if not blank_states:
        rows = rows.copy()
        rows["match_basis"] = "npi_on_list"
        return rows
    pd_["st"] = pd_[st_col].fillna("").astype(str).str.upper()
    pd_ = pd_[pd_["st"].isin(blank_states)].copy()
    pd_["k"] = _normalize_name(pd_[name_col])
    pd_ = pd_[pd_["k"] != ""]
    counts = pd_.groupby(["st", "k"])["npi"].nunique()
    uniq = pd_.drop_duplicates(["st", "k"]).set_index(["st", "k"])["npi"]
    rows = rows.copy()
    rows["match_basis"] = "npi_on_list"
    blank = rows["npi"] == ""
    keys = _normalize_name(rows.loc[blank, "entity_name"])
    non_npi = rows["non_npi_role"].fillna(0).astype(int) if "non_npi_role" in rows.columns else pd.Series(0, index=rows.index)
    for idx, k, st in zip(rows.index[blank], keys, rows.loc[blank, "state"]):
        if int(non_npi.at[idx]):
            rows.at[idx, "match_basis"] = "non_npi_role"      # an aide / driver / clerk never resolves on a name
            continue
        if not k or not distinctive_name_key(k):
            rows.at[idx, "match_basis"] = "unmatched"
            continue
        n = counts.get((st, k), 0)
        if n == 1:
            rows.at[idx, "npi"] = uniq[(st, k)]
            rows.at[idx, "match_basis"] = "name_match"
            rows.at[idx, "excl_type"] = rows.at[idx, "excl_type"] + "|name_match"
        else:
            rows.at[idx, "match_basis"] = "ambiguous" if n > 1 else "unmatched"
    return rows


def load_all(in_dir: Path, provider_dim: pd.DataFrame | None = None) -> tuple[pd.DataFrame, list[dict]]:
    frames, log = [], []
    for p in sorted(Path(in_dir).glob("*")):
        if p.suffix.lower() not in (".xlsx", ".xls", ".csv") or p.name.startswith("~"):
            continue
        st = state_from_filename(p)
        if not st or "template" in p.name.lower() or "smmc" in p.name.lower():
            log.append({"file": p.name, "state": st, "rows": 0, "note": "skipped (no state prefix or a template)"})
            continue
        try:
            df, fields, sheet = read_state_file(p)
        except Exception as ex:  # noqa: BLE001
            log.append({"file": p.name, "state": st, "rows": 0, "note": f"unreadable: {type(ex).__name__}: {ex}"})
            continue
        if "org_name" not in fields and "last_name" not in fields:
            log.append({"file": p.name, "state": st, "rows": 0, "note": f"no name column found; header {list(df.columns)[:8]}"})
            continue
        out = normalize_state_list(df, fields, st, p.name)
        out = match_names_to_nppes(out, provider_dim) if provider_dim is not None else out.assign(match_basis="npi_on_list")
        frames.append(out)
        # NPI diagnostics on the RAW column: blank vs present-but-invalid (length/Luhn),
        # with samples of the invalid values so a formatting problem is visible
        npi_nonblank = npi_invalid = 0
        invalid_samples: dict = {}
        if "npi" in fields:
            raw_npi = df[fields["npi"]].fillna("").astype(str).str.strip()
            digits = raw_npi.str.replace(r"\D", "", regex=True)
            nb = raw_npi != ""
            bad = nb & ~digits.map(lambda s: len(s) == 10 and is_valid_npi(s))
            npi_nonblank, npi_invalid = int(nb.sum()), int(bad.sum())
            invalid_samples = raw_npi[bad].value_counts().head(6).to_dict()
        top_reasons = (df[fields["reason"]].fillna("").astype(str).str.strip().replace("", "(blank)")
                       .value_counts().head(6).to_dict() if "reason" in fields else {})
        if "reason2" in fields:
            top_reasons.update({f"[2] {k}": v for k, v in df[fields["reason2"]].fillna("").astype(str).str.strip()
                                .replace("", "(blank)").value_counts().head(6).to_dict().items()})
        # low-cardinality columns NO field consumed: the place a cause hides under an odd header
        unused: dict = {}
        for c in df.columns:
            if c in fields.values():
                continue
            v = df[c].fillna("").astype(str).str.strip()
            v = v[v != ""]
            if len(v) and 1 < v.nunique() <= 25:
                unused[str(c)] = v.value_counts().head(6).to_dict()
        log.append({"file": p.name, "state": st, "sheet": sheet, "rows": int(len(out)),
                    "with_npi": int((out["npi"] != "").sum()),
                    "name_matched": int((out["match_basis"] == "name_match").sum()),
                    "dated": int(out["excl_date"].notna().sum()),
                    "npi_nonblank": npi_nonblank, "npi_invalid": npi_invalid,
                    "invalid_npi_samples": invalid_samples, "unused_categoricals": unused,
                    "header": [str(c) for c in df.columns][:16], "top_reasons": top_reasons,
                    "fields": {k: v for k, v in fields.items()}, "note": ""})
    if not frames:
        return pd.DataFrame(columns=EXCLUSION_COLS + ["state", "source_file", "match_basis"]), log
    return pd.concat(frames, ignore_index=True), log


def report(rows: pd.DataFrame, log: list[dict]) -> str:
    from src.model_a.fca_relevance import classify_series
    L = ["# STATE TERMINATIONS: what the state lists add to the label set", "",
         "_Leads for counsel review, never accusations. A state termination is tiered from its stated reason; "
         "'terminated' alone is weak._", "", "## Files", "",
         "| file | state | sheet | rows | with NPI | name-matched | dated | note |", "|---|---|---|--:|--:|--:|--:|---|"]
    for r in log:
        L.append(f"| {r['file']} | {r.get('state','')} | {r.get('sheet','')} | {r.get('rows',0)} | {r.get('with_npi','')} | "
                 f"{r.get('name_matched','')} | {r.get('dated','')} | {r.get('note','')} |")
    L += ["", "## Field map per file (which column fed each field; check this before trusting a tier)", ""]
    for r in log:
        if not r.get("rows"):
            continue
        L.append(f"**{r['file']}** (sheet {r.get('sheet') or '-'}): NPI cells non-blank {r.get('npi_nonblank', 0):,}, "
                 f"of which invalid (length/Luhn) {r.get('npi_invalid', 0):,}")
        L.append(f"- header: {r.get('header', [])}")
        L.append(f"- map: {r.get('fields', {})}")
        if r.get("top_reasons"):
            L.append("- top reason values: " + "; ".join(f"{k[:50]} ({v})" for k, v in r["top_reasons"].items()))
        if r.get("invalid_npi_samples"):
            L.append("- invalid NPI samples: " + "; ".join(f"{k[:30]!r} ({v})" for k, v in r["invalid_npi_samples"].items()))
        for col, vals in (r.get("unused_categoricals") or {}).items():
            L.append(f"- unused column {col!r}: " + "; ".join(f"{k[:40]} ({v})" for k, v in vals.items()))
        L.append("")
    if len(rows):
        t = classify_series(rows["excl_type"])
        rows = rows.assign(tier=t["fca_tier"].values, basis=t["fca_basis"].values)
        L += ["", "## Tier by state (distinct NPIs with a valid NPI)", "", "| state | hard | weak | excluded | name-only rows |", "|---|--:|--:|--:|--:|"]
        for st, g in rows.groupby("state"):
            has = g[g["npi"] != ""]
            L.append(f"| {st} | {has[has.tier=='hard']['npi'].nunique()} | {has[has.tier=='weak']['npi'].nunique()} | "
                     f"{has[has.tier=='excluded']['npi'].nunique()} | {int((g['npi']=='').sum())} |")
        unk = rows[rows["basis"].str.startswith("state termination reason unmapped")]
        if len(unk):
            L += ["", "## Reasons the tier table does not know (default weak): add them to STATE_TERM_RULES", ""]
            for reason, n in unk["excl_type"].str.split(":", n=2).str[2].str.replace(r"\|name_match$", "", regex=True).value_counts().head(40).items():
                L.append(f"- {n:>4}  {reason[:110]}")
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-dir", required=True, help="preclean/state_terminations")
    ap.add_argument("--provider-dim", default=None, help="processed/provider_dim.parquet (for name-only lists)")
    ap.add_argument("--out", required=True, help="processed/exclusions_state_terminations.parquet")
    a = ap.parse_args()
    pdim = pd.read_parquet(a.provider_dim) if a.provider_dim and Path(a.provider_dim).exists() else None
    rows, log = load_all(Path(a.in_dir), pdim)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows[EXCLUSION_COLS].to_parquet(out, index=False)
    rep = out.with_name("STATE_TERMINATIONS_REPORT.md")
    rep.write_text(report(rows, log), encoding="utf-8")
    print(f"state terminations: {len(rows):,} rows, {int((rows['npi'] != '').sum()):,} with NPI "
          f"({int((rows.get('match_basis', pd.Series(dtype=str)) == 'name_match').sum()):,} by name match) -> {out}\n  report: {rep}")


if __name__ == "__main__":
    main()
