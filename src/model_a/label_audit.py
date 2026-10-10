"""
label_audit.py — is the answer key complete, accurate, and keyed right?

The model is only as good as the label it is graded against. Before another
run is pre-registered, this audit reads the label sources the pipeline
actually uses and grades each one on the four things that decide whether a
positive is usable for a False Claims Act (FCA) lead list:

  IDENTIFIER   does the record carry a valid NPI (the only key that joins to
               billing)? Name-only rows never become a label for a provider.
  DATE         is the event dated, in a sane range, and (for a frozen run)
               placed relative to the cutoff? Undated rows cannot be a
               forward positive; future-dated rows are entry errors.
  RELEVANCE    which FCA tier (hard / weak / excluded) does the statute or
               reason map to, and how many rows map to NOTHING the tier
               table knows (an unmapped code is a silent 'weak')?
  REACH        does the NPI exist in the scored universe at all, and did it
               ever bill? A positive nobody could have scored is not a
               test of the model; it is a test of the join.

Plus the two label sources the exclusion lists do not cover:

  DOJ CASE DB  precision proxies (parse rates, duplicates, usable flags) and
               RECALL — which cases resolved to a scored NPI, which did not,
               and how many settlement dollars sit in the unresolved pile.
  OIG CIA LIST an independent official list of organizations that settled an
               FCA matter; the share of CIA parties found in the case DB is
               a direct recall measurement of the harvest.

Output: LABEL_AUDIT.md (the short story + the tables + a findings list with
the concrete fix per finding) and label_audit.json (machine-readable, for the
run registry). Diagnostics only — this module never writes or changes a label.
Leads for counsel review, never accusations: every row here is a public
integrity event, not a finding about any provider.

  python -m src.model_a.label_audit --exclusion-nodes graph/nodes/exclusion_nodes.parquet \
      [--matrix <run>/provider_features_for_model.parquet] [--case-db doj_cases_v3.csv] \
      [--cia enforcement/oig_cia.csv] [--org-nodes graph/nodes/org_nodes.parquet] \
      [--cutoff 2021-12] --out <run>/LABEL_AUDIT.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.attempt_2.clean_data import is_valid_npi
from src.entity_graph.resolve_entities import norm_org_name
from .fca_relevance import LEIE_STATUTES, classify_series

_NORM = re.compile(r"[^a-z0-9]")
_LEIE_KEYS = tuple(sorted(LEIE_STATUTES, key=len, reverse=True))
_MATRIX_COLS = ["npi", "net_paid", "last_service_month", "first_service_month",
                "provider_on_exclusion", "exclusion_label_sources", "label_basis",
                "case_ids", "fraud_scheme", "conduct_start", "conduct_end"]
UNMAPPED_RE = re.compile(r"^(LEIE statute unmapped|unrecognized source)")
_TOKEN = re.compile(r"[a-z]{3,}")


# --------------------------------------------------------------------------- #
# source detection
# --------------------------------------------------------------------------- #

def label_source(excl_type) -> str:
    """Which feed a record came from, read off the excl_type convention every
    adapter writes: bare LEIE statute codes, '<source>[:<reason>]' otherwise."""
    t = str(excl_type or "").strip().lower()
    if not t:
        return "(blank excl_type)"
    head = re.split(r"[:\s]", t, maxsplit=1)[0]
    for src in ("opensanctions", "medicare_revocation", "preclusion", "sam",
                "state_board", "state_term", "mfcu"):
        if head.startswith(src):
            return src
    if head.startswith("doj") or head.startswith("case"):
        return "doj_case"
    key = _NORM.sub("", t)
    if any(key.startswith(k) for k in _LEIE_KEYS) or key.startswith("1128"):
        return "leie"
    return f"other:{head[:20]}"


def _valid_npi_mask(npi: pd.Series) -> pd.Series:
    s = npi.fillna("").astype(str).str.strip()
    ten = s.str.fullmatch(r"\d{10}").fillna(False)
    ok = pd.Series(False, index=s.index)
    ok.loc[ten] = s.loc[ten].map(is_valid_npi)
    return ok, ten


def _ym(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str[:7], format="%Y-%m", errors="coerce")


def _cutoff_ts(cutoff: str | None) -> pd.Timestamp | None:
    if not cutoff:
        return None
    s = str(cutoff).strip()
    return pd.Timestamp(s[:7] + "-01" if len(s) == 7 else s)


def _share(n: float, d: float) -> float:
    return float(n) / float(d) if d else float("nan")


# --------------------------------------------------------------------------- #
# A. exclusion-style sources (LEIE, revocations, SAM, OpenSanctions, ...)
# --------------------------------------------------------------------------- #

def audit_exclusions(nodes: pd.DataFrame, cutoff: str | None = None,
                     universe: set | None = None,
                     billing_npis: set | None = None) -> dict:
    """Per-source completeness / identifier / date / tier / reach table, plus
    integrity checks across sources. Returns a dict with ``per_source``
    (DataFrame), ``unmapped`` (DataFrame of excl_type values the tier table
    does not know), ``collisions`` (NPIs carrying >1 distinct name), and
    ``totals``."""
    if nodes is None or not len(nodes):
        return {"per_source": pd.DataFrame(), "unmapped": pd.DataFrame(),
                "collisions": pd.DataFrame(), "totals": {"rows": 0}}
    e = nodes.copy()
    e["excl_type"] = e.get("excl_type", pd.Series("", index=e.index)).fillna("").astype(str)
    e["source"] = e["excl_type"].map(label_source)
    e["npi"] = e.get("npi", pd.Series("", index=e.index)).fillna("").astype(str).str.strip()
    ok, ten = _valid_npi_mask(e["npi"])
    e["npi_ok"] = ok
    e["npi_ten_bad_luhn"] = ten & ~ok
    e["npi_present"] = e["npi"].str.len() > 0
    date = pd.to_datetime(e.get("excl_date"), errors="coerce")
    e["date"] = date
    today = pd.Timestamp.today().normalize()
    e["future"] = date > today
    e["ancient"] = date < pd.Timestamp("1977-01-01")       # LEIE begins 1977
    tiers = classify_series(e["excl_type"])
    e["tier"] = tiers["fca_tier"].values
    e["basis"] = tiers["fca_basis"].values
    e["unmapped"] = e["basis"].str.match(UNMAPPED_RE)
    active = pd.to_numeric(e.get("currently_active"), errors="coerce")
    e["active"] = active.fillna(np.nan)
    cut = _cutoff_ts(cutoff)
    if cut is not None:
        e["post_cutoff"] = date > cut
    dup_key = [c for c in ("npi", "name_key", "excl_type") if c in e.columns]
    e["_dkey"] = e[dup_key].astype(str).agg("|".join, axis=1) + "|" + date.astype(str)
    e["dup"] = e.duplicated("_dkey", keep="first")

    rows = []
    for src, g in e.groupby("source", sort=True):
        n = len(g)
        valid = g[g["npi_ok"]]
        d_npi = set(valid["npi"])
        r = {
            "source": src, "rows": n,
            "distinct_npi": len(d_npi),
            "npi_share": _share(g["npi_ok"].sum(), n),
            "name_only_rows": int((~g["npi_present"]).sum()),
            "bad_npi_rows": int((g["npi_present"] & ~g["npi_ok"]).sum()),
            "luhn_fail_rows": int(g["npi_ten_bad_luhn"].sum()),
            "dated_share": _share(g["date"].notna().sum(), n),
            "date_min": (g["date"].min().strftime("%Y-%m") if g["date"].notna().any() else ""),
            "date_max": (g["date"].max().strftime("%Y-%m") if g["date"].notna().any() else ""),
            "future_dated": int(g["future"].sum()),
            "pre_1977": int(g["ancient"].sum()),
            "active_share": (_share(g["active"].eq(1).sum(), g["active"].notna().sum())
                             if g["active"].notna().any() else float("nan")),
            "dup_rows": int(g["dup"].sum()),
            "hard": int((g["tier"] == "hard").sum()),
            "weak": int((g["tier"] == "weak").sum()),
            "excluded": int((g["tier"] == "excluded").sum()),
            "unmapped_rows": int(g["unmapped"].sum()),
        }
        r["hard_share"] = _share(r["hard"], n)
        if universe is not None:
            inu = d_npi & universe
            r["in_universe"] = len(inu)
            r["in_universe_share"] = _share(len(inu), len(d_npi))
            if billing_npis is not None:
                r["billing_seen"] = len(d_npi & billing_npis)
                r["billing_seen_share"] = _share(len(d_npi & billing_npis), len(d_npi))
        if cut is not None:
            r["post_cutoff_rows"] = int(g["post_cutoff"].sum())
            r["post_cutoff_hard_npi"] = int(
                g.loc[g["post_cutoff"] & g["npi_ok"] & (g["tier"] == "hard"), "npi"].nunique())
        rows.append(r)
    per_source = pd.DataFrame(rows)

    unmapped = (e[e["unmapped"]].groupby("excl_type").size()
                .sort_values(ascending=False).rename("rows").reset_index())
    unmapped["source"] = unmapped["excl_type"].map(label_source)

    # identity collisions: one NPI, several distinct normalized names across
    # records (a mis-keyed NPI puts someone else's exclusion on a provider)
    # Two thresholds: 'variants' = more than one distinct normalized name on an
    # NPI (middle initials, maiden names, re-listings: usually benign), and
    # 'collisions' = two records on one NPI that share NO name token of 3+
    # letters (DOE JOHN vs ROE JANE: a different person on the same NPI).
    v = e[e["npi_ok"] & e.get("name_key", pd.Series("", index=e.index)).fillna("").ne("")]
    n_variants = 0
    if len(v) and "name_key" in v.columns:
        names = v.groupby("npi")["name_key"].agg(lambda s: sorted(set(s)))
        multi = names[names.map(len) > 1]
        n_variants = int(len(multi))
        rows_c = []
        for npi, xs in multi.items():
            toks = [set(_TOKEN.findall(str(x).lower())) for x in xs]
            if any(not (a & b) for i, a in enumerate(toks) for b in toks[i + 1:]):
                rows_c.append({"npi": npi, "n_names": len(xs), "names": " | ".join(xs[:4])})
        collisions = pd.DataFrame(rows_c, columns=["npi", "n_names", "names"])
    else:
        collisions = pd.DataFrame(columns=["npi", "n_names", "names"])

    totals = {
        "rows": int(len(e)),
        "distinct_valid_npi": int(e.loc[e["npi_ok"], "npi"].nunique()),
        "npi_share": _share(e["npi_ok"].sum(), len(e)),
        "name_only_rows": int((~e["npi_present"]).sum()),
        "bad_npi_rows": int((e["npi_present"] & ~e["npi_ok"]).sum()),
        "undated_rows": int(e["date"].isna().sum()),
        "future_dated": int(e["future"].sum()),
        "unmapped_rows": int(e["unmapped"].sum()),
        "dup_rows": int(e["dup"].sum()),
        "name_variants": n_variants,
        "collisions": int(len(collisions)),
        "hard_npi": int(e.loc[e["npi_ok"] & (e["tier"] == "hard"), "npi"].nunique()),
    }
    if universe is not None:
        valid_npis = set(e.loc[e["npi_ok"], "npi"])
        totals["in_universe"] = len(valid_npis & universe)
        totals["in_universe_share"] = _share(len(valid_npis & universe), len(valid_npis))
    return {"per_source": per_source, "unmapped": unmapped,
            "collisions": collisions, "totals": totals}


# --------------------------------------------------------------------------- #
# B. the matrix label (what the model was actually graded on)
# --------------------------------------------------------------------------- #

def load_matrix_slim(path: str | Path) -> pd.DataFrame:
    import pyarrow.parquet as pq
    names = pq.ParquetFile(path).schema_arrow.names
    cols = [c for c in _MATRIX_COLS if c in names]
    m = pd.read_parquet(path, columns=cols)
    m["npi"] = m["npi"].astype(str)
    return m


def audit_matrix(matrix: pd.DataFrame, nodes: pd.DataFrame | None = None,
                 cutoff: str | None = None) -> dict:
    """Checks on the label columns the export shipped: positives by source,
    DOJ basis mix, conduct-window completeness, positives without billing,
    unexplained positives (flagged but in no source), missed exclusions (in a
    source, in the matrix, not flagged), and the billing-to-exclusion lag."""
    out: dict = {"rows": int(len(matrix))}
    if "provider_on_exclusion" not in matrix.columns:
        out["note"] = "matrix has no provider_on_exclusion column"
        return out
    m = matrix.copy()
    pos_mask = pd.to_numeric(m["provider_on_exclusion"], errors="coerce").fillna(0) == 1
    pos = m[pos_mask]
    out["positives"] = int(len(pos))
    out["positive_rate"] = _share(len(pos), len(m))

    src = pos.get("exclusion_label_sources", pd.Series("", index=pos.index)).fillna("").astype(str)
    tags = src.str.split(";").explode().str.strip()
    tags = tags[tags != ""]
    out["by_source"] = tags.value_counts().to_dict()
    out["multi_source_positives"] = int((src.str.count(";") >= 1).sum())
    out["no_source_tag"] = int((src == "").sum())

    if "label_basis" in m.columns:
        doj = pos[pos["label_basis"].fillna("").ne("")]
        out["doj_by_basis"] = doj["label_basis"].value_counts().to_dict()
        cs = pd.to_numeric(doj.get("conduct_start"), errors="coerce")
        out["doj_positives"] = int(len(doj))
        out["doj_conduct_start_missing_share"] = _share(cs.isna().sum(), len(doj))
        ce = pd.to_numeric(doj.get("conduct_end"), errors="coerce")
        out["doj_conduct_window_incoherent"] = int(((cs > ce) & cs.notna() & ce.notna()).sum())
        if "fraud_scheme" in doj.columns:
            sch = doj["fraud_scheme"].fillna("").astype(str)
            out["doj_scheme_unknown_share"] = _share(
                sch.isin(["", "unknown"]).sum(), len(doj))
        # label CONCENTRATION: an org-billing broadcast labels every billing
        # NPI of a settled org, so one system-wide settlement can supply
        # hundreds of "fraud" positives that share a single conduct finding
        if "case_ids" in doj.columns and len(doj):
            ids = doj["case_ids"].fillna("").astype(str).str.split(";").explode().str.strip()
            ids = ids[ids != ""]
            vc = ids.value_counts()
            out["doj_cases_labelling"] = int(len(vc))
            out["doj_npis_per_case_median"] = float(vc.median()) if len(vc) else 0.0
            top5 = set(vc.head(5).index)
            in_top = doj["case_ids"].fillna("").astype(str).map(
                lambda s: any(x.strip() in top5 for x in s.split(";")))
            out["doj_top5_case_share"] = _share(in_top.sum(), len(doj))
            out["doj_top_cases"] = [{"case_id": str(k)[:70], "npis": int(v)}
                                    for k, v in vc.head(10).items()]

    paid = pd.to_numeric(pos.get("net_paid"), errors="coerce").fillna(0)
    out["positives_zero_billing"] = int((paid <= 0).sum())
    out["positives_zero_billing_share"] = _share((paid <= 0).sum(), len(pos))

    if nodes is not None and len(nodes) and "npi" in nodes.columns:
        n = nodes.copy()
        n["npi"] = n["npi"].fillna("").astype(str).str.strip()
        ok, _ = _valid_npi_mask(n["npi"])
        n = n[ok]
        cut = _cutoff_ts(cutoff)
        date = pd.to_datetime(n.get("excl_date"), errors="coerce")
        known = n if cut is None else n[date.notna() & (date <= cut)]
        known_npis = set(known["npi"])
        doj_tag = src.str.contains("doj_case|deactivation|death", regex=True)
        unexplained = pos[~pos["npi"].isin(known_npis) & ~doj_tag]
        out["unexplained_positives"] = int(len(unexplained))
        out["unexplained_sample"] = unexplained["npi"].head(10).tolist()
        in_m = m[m["npi"].isin(known_npis)]
        missed = in_m[pd.to_numeric(in_m["provider_on_exclusion"], errors="coerce").fillna(0) != 1]
        out["excluded_in_matrix"] = int(len(in_m))
        out["missed_positives"] = int(len(missed))
        out["missed_sample"] = missed["npi"].head(10).tolist()

        # billing-to-exclusion lag: last bill vs the FIRST exclusion date
        if "last_service_month" in m.columns:
            first = (n.assign(d=date).dropna(subset=["d"])
                       .groupby("npi")["d"].min())
            lag_df = pos[["npi", "last_service_month"]].copy()
            lag_df["excl"] = lag_df["npi"].map(first)
            lag_df["last"] = _ym(lag_df["last_service_month"])
            lag_df = lag_df.dropna(subset=["excl", "last"])
            if len(lag_df):
                months = ((lag_df["excl"].dt.year - lag_df["last"].dt.year) * 12
                          + (lag_df["excl"].dt.month - lag_df["last"].dt.month))
                out["lag_n"] = int(len(lag_df))
                out["lag_median_months"] = float(months.median())
                out["lag_over_24m_share"] = _share((months > 24).sum(), len(months))
                out["billed_after_exclusion"] = int((months < 0).sum())
    return out


# --------------------------------------------------------------------------- #
# C. the DOJ case DB (precision proxies + recall)
# --------------------------------------------------------------------------- #

def audit_case_db(case_db: pd.DataFrame, matrix: pd.DataFrame | None = None) -> dict:
    out: dict = {"rows": int(len(case_db))}
    if case_db is None or not len(case_db):
        return out
    c = case_db.copy()
    # one settlement announced twice counts once here (the near-duplicate finding
    # below then reports only the pairs the case DB has NOT already marked)
    if "duplicate_of" in c.columns:
        dup = c["duplicate_of"].fillna("").astype(str) != ""
        out["marked_duplicates"] = int(dup.sum())
        c = c[~dup]
    n = len(c)
    usable = (pd.to_numeric(c["usable"], errors="coerce").fillna(1) == 1
              if "usable" in c.columns else pd.Series(True, index=c.index))
    out["usable"] = int(usable.sum())
    out["usable_share"] = _share(usable.sum(), n)
    c = c[usable]
    n = len(c)
    npi_col = next((k for k in ("npi_in_source", "npi") if k in c.columns), None)
    if npi_col:
        ok, _ = _valid_npi_mask(c[npi_col])
        out["with_npi"] = int(ok.sum())
        out["with_npi_share"] = _share(ok.sum(), n)
    else:
        out["with_npi"] = 0
        out["with_npi_share"] = 0.0
    key = c.get("defendant_name_key")
    if key is None and "defendant_name" in c.columns:
        key = c["defendant_name"].fillna("").astype(str).map(norm_org_name)
    key = key.fillna("").astype(str) if key is not None else pd.Series("", index=c.index)
    out["name_key_share"] = _share((key.str.len() >= 4).sum(), n)
    amt = pd.to_numeric(c.get("amount_usd"), errors="coerce")
    out["amount_parsed_share"] = _share(amt.notna().sum(), n)
    out["amount_total_usd"] = float(amt.fillna(0).sum())
    ad = pd.to_datetime(c.get("announced_date"), errors="coerce")
    out["date_parsed_share"] = _share(ad.notna().sum(), n)
    if ad.notna().any():
        out["year_min"], out["year_max"] = int(ad.min().year), int(ad.max().year)
        out["by_year"] = ad.dt.year.value_counts().sort_index().astype(int).to_dict()
    out["dup_case_id"] = int(c["case_id"].astype(str).duplicated().sum()) if "case_id" in c.columns else 0
    for col in ("qui_tam", "intervened"):
        if col in c.columns:
            v = pd.to_numeric(c[col], errors="coerce")
            out[f"{col}_stated_share"] = _share(v.notna().sum(), n)
            out[f"{col}_yes"] = int((v == 1).sum())
    for col in ("scheme", "sector"):
        if col in c.columns:
            s = c[col].fillna("").astype(str).str.lower()
            out[f"{col}_known_share"] = _share((~s.isin(["", "unknown", "nan"])).sum(), n)
    if "source_url" in c.columns:
        u = c["source_url"].fillna("").astype(str)
        out["primary_source_share"] = _share(
            u.str.contains(r"justice\.gov|oig\.hhs\.gov|\.gov/|courtlistener", regex=True).sum(), n)

    # NEAR-DUPLICATE cases: same dollar amount, same announcement year, and a
    # defendant-name token in common (the two-press-releases-one-settlement
    # pattern: DOJ national + USAO local, or the sentencing re-announcement).
    # They double-count dollars and double-weight the label.
    d = pd.DataFrame({"case_id": c.get("case_id", pd.Series("", index=c.index)).astype(str),
                      "defendant": c.get("defendant_name", pd.Series("", index=c.index)).fillna("").astype(str),
                      "amt": amt, "yr": ad.dt.year, "key": key})
    d = d[(d["amt"] > 0) & d["yr"].notna()]
    pairs: list[dict] = []
    for (a, y), g in d.groupby(["amt", "yr"]):
        if len(g) < 2:
            continue
        rows_g = list(g.itertuples())
        # the same distinctive-token rule the case DB marks with (generic words
        # such as HEALTH / SERVICES never pair two different settlements)
        from src.enforcement.case_db import _name_tokens
        toks = [_name_tokens(str(r.key)) for r in rows_g]
        for i in range(len(rows_g)):
            for j in range(i + 1, len(rows_g)):
                if toks[i] & toks[j]:
                    pairs.append({"case_a": rows_g[i].case_id[:60], "case_b": rows_g[j].case_id[:60],
                                  "defendant": rows_g[i].defendant[:60], "amount_usd": float(a),
                                  "year": int(y)})
    out["near_dup_pairs"] = len(pairs)
    out["near_dup_dollars"] = float(sum(p["amount_usd"] for p in pairs))
    out["near_dup_sample"] = sorted(pairs, key=lambda p: -p["amount_usd"])[:15]

    if matrix is not None and "case_ids" in matrix.columns and "case_id" in c.columns:
        ids = (matrix["case_ids"].dropna().astype(str).str.split(";").explode()
               .str.strip())
        resolved = set(ids[ids != ""])
        cid = c["case_id"].astype(str)
        hit = cid.isin(resolved)
        out["resolved_cases"] = int(hit.sum())
        out["resolved_share"] = _share(hit.sum(), n)
        out["resolved_dollar_share"] = _share(amt[hit].fillna(0).sum(), amt.fillna(0).sum())
        out["unresolved_dollars"] = float(amt[~hit].fillna(0).sum())
        # which SECTORS hold the unresolved dollars: pharma / device / health
        # plan defendants never bill as a provider NPI (unreachable by design);
        # hospital / SNF / physician-group defendants should resolve
        if "sector" in c.columns:
            sec = c["sector"].fillna("").astype(str).str.lower().replace("", "unknown")
            by = amt[~hit].fillna(0).groupby(sec[~hit]).sum().sort_values(ascending=False)
            out["unresolved_by_sector"] = {str(k): float(v) for k, v in by.head(12).items()}
            cnt = sec[~hit].value_counts()
            out["unresolved_cases_by_sector"] = {str(k): int(cnt.get(k, 0)) for k in by.head(12).index}
        top = c[~hit].assign(_amt=amt[~hit].fillna(0)).sort_values("_amt", ascending=False)
        keep = [k for k in ("case_id", "defendant_name", "announced_date", "sector", "scheme")
                if k in top.columns]
        out["top_unresolved"] = (top[keep + ["_amt"]].head(15)
                                 .rename(columns={"_amt": "amount_usd"})
                                 .astype(str).to_dict("records"))
    return out


# --------------------------------------------------------------------------- #
# D. OIG CIA list recall
# --------------------------------------------------------------------------- #

def audit_cia(cia: pd.DataFrame, case_db: pd.DataFrame | None = None,
              org_nodes: pd.DataFrame | None = None, fuzzy: float = 0.92,
              year_min: int | None = None) -> dict:
    """Share of CIA parties (orgs that settled an FCA matter) found in the case
    DB by name key (exact, then conservative difflib), and resolvable to a
    graph org. ``year_min`` restricts to CIAs effective on/after that year so
    a harvest that starts in 2016 is not blamed for missing 2009 CIAs."""
    import difflib
    out: dict = {"parties": int(len(cia))}
    if cia is None or not len(cia):
        return out
    c = cia.copy()
    eff = pd.to_datetime(c.get("effective_date"), errors="coerce")
    if year_min is not None:
        c = c[eff.isna() | (eff.dt.year >= int(year_min))]
        eff = eff.loc[c.index]
    out["parties_in_window"] = int(len(c))
    out["by_status"] = c["cia_status"].value_counts().to_dict() if "cia_status" in c.columns else {}
    # both sides upper-cased: norm_org_name emits UPPER, hand-built keys may not
    keys = c["name_key"].fillna("").astype(str).str.upper()

    def _match(target_keys: set) -> pd.Series:
        target_keys = {str(k).upper() for k in target_keys}
        exact = keys.isin(target_keys)
        pool = [k for k in target_keys if len(k) >= 8]
        fz = pd.Series(False, index=keys.index)
        for i, k in keys[~exact].items():
            if len(k) >= 8 and difflib.get_close_matches(k, pool, n=1, cutoff=fuzzy):
                fz.loc[i] = True
        return exact, fz

    if case_db is not None and len(case_db):
        ck = case_db.get("defendant_name_key")
        if ck is None and "defendant_name" in case_db.columns:
            ck = case_db["defendant_name"].fillna("").astype(str).map(norm_org_name)
        target = set(ck.fillna("").astype(str)) - {""} if ck is not None else set()
        exact, fz = _match(target)
        out["case_db_exact"] = int(exact.sum())
        out["case_db_fuzzy"] = int(fz.sum())
        out["case_db_recall"] = _share((exact | fz).sum(), len(c))
        miss = c[~(exact | fz)].assign(_eff=eff[~(exact | fz)])
        miss = miss.sort_values("_eff", ascending=False)
        out["case_db_unmatched"] = (miss[["entity_name", "state", "cia_status"]]
                                    .assign(effective=miss["_eff"].dt.strftime("%Y-%m").fillna(""))
                                    .head(30).astype(str).to_dict("records"))
    if org_nodes is not None and len(org_nodes):
        ok_col = next((k for k in ("name_key", "org_name_key") if k in org_nodes.columns), None)
        if ok_col:
            target = set(org_nodes[ok_col].fillna("").astype(str)) - {""}
        else:
            nm = next((k for k in ("org_name", "name", "entity_name") if k in org_nodes.columns), None)
            target = (set(org_nodes[nm].fillna("").astype(str).map(norm_org_name)) - {""}
                      if nm else set())
        exact, fz = _match(target)
        out["org_exact"] = int(exact.sum())
        out["org_fuzzy"] = int(fz.sum())
        out["org_resolvable_share"] = _share((exact | fz).sum(), len(c))
    return out


# --------------------------------------------------------------------------- #
# E. findings — every number that should change what the operator does next
# --------------------------------------------------------------------------- #

def findings(ex: dict, mx: dict | None, cdb: dict | None, cia: dict | None,
             cutoff: str | None) -> list[dict]:
    f: list[dict] = []
    ps = ex.get("per_source")
    t = ex.get("totals", {})

    def add(sev, what, fix):
        f.append({"severity": sev, "finding": what, "fix": fix})

    if ps is not None and len(ps):
        for r in ps.itertuples():
            if r.rows >= 50 and r.npi_share < 0.5:
                add("high",
                    f"{r.source}: only {r.npi_share:.0%} of {r.rows:,} rows carry a valid NPI "
                    f"({r.name_only_rows:,} name-only). Those rows never label a provider.",
                    "Resolve names to NPIs through the person/org resolver (NPPES name + "
                    "state + specialty) or treat this feed as corroboration only, not label.")
            if r.luhn_fail_rows:
                add("medium",
                    f"{r.source}: {r.luhn_fail_rows:,} ten-digit NPIs fail the Luhn check "
                    "(typos or non-NPI ids stored in the NPI field).",
                    "Quarantine them at ingest (canonicalize_series) so they can't join.")
            if r.future_dated:
                add("medium", f"{r.source}: {r.future_dated:,} rows are dated in the future.",
                    "Re-parse the date column (format drift) and quarantine the rest.")
            if r.pre_1977:
                add("low", f"{r.source}: {r.pre_1977:,} rows dated before 1977.",
                    "Check the date format on those rows (two-digit years?).")
            if r.rows >= 50 and r.dated_share < 0.95:
                add("medium",
                    f"{r.source}: {1 - r.dated_share:.0%} of rows are undated; they can never "
                    "be forward positives or be placed against a cutoff.",
                    "Find the date field in the raw feed; undated rows stay out of the label.")
            if r.unmapped_rows:
                add("high",
                    f"{r.source}: {r.unmapped_rows:,} rows have a statute/reason the FCA tier "
                    "table does not know; they default to 'weak' silently.",
                    "Add the codes listed under 'Unmapped statutes' to fca_relevance.")
            if r.dup_rows:
                add("low", f"{r.source}: {r.dup_rows:,} exact duplicate rows.",
                    "Harmless for a per-NPI label; de-duplicate at merge for clean counts.")
            iu = getattr(r, "in_universe_share", None)
            if iu is not None and not np.isnan(iu) and r.distinct_npi >= 50 and iu < 0.3:
                add("info",
                    f"{r.source}: only {iu:.0%} of its {r.distinct_npi:,} NPIs exist in the "
                    "scored universe (the rest never billed the programs we can see).",
                    "Expected for individuals excluded before they billed; nothing to fix, "
                    "but do not count them as missed positives.")
    if t.get("collisions"):
        add("medium",
            f"{t['collisions']:,} NPIs carry two names that share no token across exclusion "
            f"records (a different person on the same NPI; {t.get('name_variants', 0):,} "
            "NPIs have benign name variants such as initials or re-listings).",
            "Review the 'Identity collisions' sample; confirm against NPPES before use.")

    if mx:
        if mx.get("unexplained_positives"):
            add("high",
                f"{mx['unexplained_positives']:,} matrix positives are in NO exclusion source "
                "known at the cutoff and carry no DOJ/deactivation/death tag.",
                "Trace them: a label leaked from a post-cutoff record or a stale cache.")
        if mx.get("missed_positives"):
            add("high",
                f"{mx['missed_positives']:,} providers have a known exclusion, sit in the "
                "matrix, and are NOT flagged positive.",
                "The widened-label join dropped them; check NPI string typing and the "
                "as-of graph's exclusion nodes.")
        zs = mx.get("positives_zero_billing_share")
        if zs is not None and zs > 0.25:
            add("medium",
                f"{zs:.0%} of positives never billed in the spending fact; the model can "
                "only see them through registry/graph features.",
                "Grade on the ACTIVE universe (label_lag --active-within) and keep the "
                "silent ones out of lift; they are not leads either.")
        cm = mx.get("doj_conduct_start_missing_share")
        if cm is not None and cm > 0.2:
            add("medium",
                f"{cm:.0%} of DOJ-labelled providers have no conduct window; a frozen run "
                "drops them, a current run cannot time-box them.",
                "Fill conduct_start/conduct_end from the complaint or settlement text "
                "(case_qc); it is the single most valuable field per case.")
        t5 = mx.get("doj_top5_case_share")
        if t5 is not None and t5 > 0.4:
            add("medium",
                f"Label concentration: the top 5 DOJ cases supply {t5:.0%} of the "
                f"{mx.get('doj_positives', 0):,} DOJ positives (org-billing broadcast labels "
                "every billing NPI of a settled system with one conduct finding).",
                "Cap the org_billing broadcast per case (max_org_members on tier 2, as tier 3 "
                "already has) or weight each positive 1/n per case in training, so a single "
                "hospital-system settlement cannot dominate the fraud label.")
        if mx.get("doj_conduct_window_incoherent"):
            add("low", f"{mx['doj_conduct_window_incoherent']:,} DOJ rows have conduct_start "
                "after conduct_end.", "Fix in the case file (case_corrections).")
        if mx.get("billed_after_exclusion"):
            add("info",
                f"{mx['billed_after_exclusion']:,} positives billed AFTER their first "
                "exclusion date (billing_after_exclusion is a leakage-hard feature).",
                "Nothing to fix; confirms the feature is fenced from training.")
        lm = mx.get("lag_median_months")
        if lm is not None and lm > 24:
            add("info",
                f"Median gap from last bill to first exclusion is {lm:.0f} months.",
                "Back-date labels to the last billing month for training targets, or "
                "grade only the active universe.")
    if cdb:
        rs = cdb.get("resolved_share")
        if rs is not None and rs < 0.5:
            add("high",
                f"Only {rs:.0%} of usable DOJ cases resolved to a scored NPI; "
                f"${cdb.get('unresolved_dollars', 0)/1e6:,.0f}M of settlements carry no label.",
                "Work the 'Largest unresolved cases' list: add the defendant's NPI/CCN by "
                "hand (npi_in_source) or its PECOS enrollment name so the org join lands.")
        if cdb.get("dup_case_id"):
            add("medium", f"{cdb['dup_case_id']:,} duplicate case_ids in the case DB.",
                "De-duplicate; a duplicate double-weights its case.")
        if cdb.get("near_dup_pairs"):
            add("medium",
                f"{cdb['near_dup_pairs']:,} case pairs share the same amount, year and a "
                f"defendant-name token (one settlement harvested twice: DOJ national plus the "
                f"local USAO release, or a sentencing re-announcement); "
                f"${cdb.get('near_dup_dollars', 0)/1e6:,.0f}M is double-counted.",
                "Mark the second row of each pair usable=0 in the case file (case_qc / "
                "case_corrections); the 'Near-duplicate cases' table lists them.")
        if cdb.get("with_npi_share", 1) < 0.1 and cdb.get("rows", 0) >= 50:
            add("medium",
                f"Only {cdb['with_npi_share']:.0%} of cases carry an NPI in the source; every "
                "other label rides on a name match.",
                "Ask the harvest agents to capture NPI/CCN when the release names one, and "
                "keep the fuzzy threshold conservative (0.92).")
        if cdb.get("scheme_known_share", 1) < 0.7:
            add("low", f"Scheme is unknown on {1 - cdb['scheme_known_share']:.0%} of cases.",
                "Scheme-stratified training needs it; fill from the summary text.")
    if cia:
        rc = cia.get("case_db_recall")
        if rc is not None and rc < 0.8:
            add("high",
                f"Case-DB recall against the OIG CIA list is {rc:.0%}: "
                f"{cia.get('parties_in_window', 0) - cia.get('case_db_exact', 0) - cia.get('case_db_fuzzy', 0):,} "
                "organizations that settled an FCA matter are missing from the harvest.",
                "Feed the 'CIA parties not in the case DB' list to the next harvest batch "
                "(each one has a public settlement and usually a press release).")
        orr = cia.get("org_resolvable_share")
        if orr is not None and orr < 0.6:
            add("medium",
                f"Only {orr:.0%} of CIA parties resolve to a graph org by name.",
                "CIA parties are parent companies; add a parent->enrollment crosswalk "
                "(PECOS legal business name) before using them as labels.")
    if not f:
        add("info", "No defects found at the thresholds checked.", "Proceed.")
    return f


# --------------------------------------------------------------------------- #
# F. report
# --------------------------------------------------------------------------- #

def _pct(v) -> str:
    return f"{v:.0%}" if isinstance(v, (int, float)) and not (isinstance(v, float) and np.isnan(v)) else "n/a"


def to_markdown(ex: dict, mx: dict | None, cdb: dict | None, cia: dict | None,
                fnd: list[dict], inputs: dict, cutoff: str | None) -> str:
    t = ex.get("totals", {})
    L = ["# LABEL AUDIT — is the answer key complete, accurate, and keyed right?", ""]
    L += [f"_Inputs: {', '.join(f'{k}={v}' for k, v in inputs.items() if v)}"
          + (f"; cutoff {cutoff}" if cutoff else "") + "._", ""]
    L += ["## The short story", ""]
    L += [f"- The exclusion-style sources hold **{t.get('rows', 0):,} records** naming "
          f"**{t.get('distinct_valid_npi', 0):,} distinct valid NPIs**; "
          f"{_pct(t.get('npi_share'))} of records carry a usable NPI, "
          f"{t.get('name_only_rows', 0):,} are name-only and can never label a provider."]
    if "in_universe_share" in t:
        L += [f"- {_pct(t['in_universe_share'])} of those NPIs exist in the scored universe "
              f"({t['in_universe']:,}); the rest never billed the programs we can see."]
    L += [f"- {t.get('hard_npi', 0):,} NPIs have at least one HARD-tier (FCA-shaped) event; "
          f"{t.get('unmapped_rows', 0):,} records have a statute or reason the tier table "
          "does not know."]
    if mx and "positives" in mx:
        L += [f"- The run's matrix flags **{mx['positives']:,} positives** "
              f"({_pct(mx.get('positive_rate'))} of {mx['rows']:,} providers); "
              f"{mx.get('positives_zero_billing', 0):,} of them never billed."]
        if "missed_positives" in mx:
            L += [f"- Join check: {mx.get('unexplained_positives', 0):,} positives with no "
                  f"source, {mx.get('missed_positives', 0):,} known exclusions in the matrix "
                  "left unflagged. Both should be zero."]
    if cdb and cdb.get("rows"):
        L += [f"- DOJ case DB: {cdb.get('usable', cdb['rows']):,} usable cases, "
              f"${cdb.get('amount_total_usd', 0)/1e9:,.2f}B in settlements; "
              + (f"{_pct(cdb.get('resolved_share'))} of cases resolved to a scored NPI, "
                 f"${cdb.get('unresolved_dollars', 0)/1e6:,.0f}M unresolved."
                 if "resolved_share" in cdb else "resolution not measured (no matrix).")]
    if cia and cia.get("parties"):
        L += [f"- OIG CIA list: {cia.get('parties_in_window', cia['parties']):,} parties; "
              f"case-DB recall {_pct(cia.get('case_db_recall'))}"
              + (f", {_pct(cia.get('org_resolvable_share'))} resolve to a graph org."
                 if "org_resolvable_share" in cia else ".")]
    hi = [x for x in fnd if x["severity"] == "high"]
    L += [f"- **{len(hi)} high-severity finding(s)**; the full list with fixes is at the end.", ""]

    L += ["## A. Exclusion-style sources (one row per feed)", "",
          "_npi = share of rows with a valid Luhn NPI; dated = share with a parseable date; "
          "tiers = hard / weak / excluded per fca_relevance; unmapped = statute or reason the "
          "tier table does not know; in universe = distinct NPIs present in the matrix; "
          "billing = of those, NPIs with any paid dollars._", ""]
    ps = ex.get("per_source")
    if ps is not None and len(ps):
        has_u = "in_universe" in ps.columns
        has_b = "billing_seen" in ps.columns
        has_c = "post_cutoff_rows" in ps.columns
        hdr = "| source | rows | distinct NPI | npi | name-only | bad NPI | dated | span | " \
              "future | active | dups | hard / weak / excl | unmapped |"
        sep = "|---|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|---|--:|"
        if has_u:
            hdr += " in universe |"; sep += "--:|"
        if has_b:
            hdr += " billing |"; sep += "--:|"
        if has_c:
            hdr += " post-cutoff rows (hard NPIs) |"; sep += "--:|"
        L += [hdr, sep]
        for r in ps.itertuples():
            row = (f"| {r.source} | {r.rows:,} | {r.distinct_npi:,} | {_pct(r.npi_share)} | "
                   f"{r.name_only_rows:,} | {r.bad_npi_rows:,} | {_pct(r.dated_share)} | "
                   f"{r.date_min}..{r.date_max} | {r.future_dated} | {_pct(r.active_share)} | "
                   f"{r.dup_rows:,} | {r.hard:,} / {r.weak:,} / {r.excluded:,} | "
                   f"{r.unmapped_rows:,} |")
            if has_u:
                row += f" {r.in_universe:,} ({_pct(r.in_universe_share)}) |"
            if has_b:
                row += f" {r.billing_seen:,} ({_pct(r.billing_seen_share)}) |"
            if has_c:
                row += f" {r.post_cutoff_rows:,} ({r.post_cutoff_hard_npi:,}) |"
            L.append(row)
    else:
        L += ["_no exclusion records_"]
    L += [""]
    um = ex.get("unmapped")
    L += ["### Unmapped statutes / reasons (default to 'weak' today)", ""]
    if um is not None and len(um):
        L += ["| excl_type | source | rows |", "|---|---|--:|"]
        for r in um.head(30).itertuples():
            L.append(f"| {str(r.excl_type)[:90]} | {r.source} | {r.rows:,} |")
    else:
        L += ["_none — every statute and reason is tiered._"]
    L += [""]
    co = ex.get("collisions")
    L += ["### Identity collisions (one NPI, two names with no token in common)", "",
          f"_{t.get('name_variants', 0):,} NPIs carry more than one name spelling (initials, "
          "maiden names, re-listings: benign); the collisions below are the subset where "
          "the names share nothing._", ""]
    if co is not None and len(co):
        L += [f"_{len(co):,} NPIs; first 15._", "", "| npi | names |", "|---|---|"]
        for r in co.head(15).itertuples():
            L.append(f"| {r.npi} | {r.names} |")
    else:
        L += ["_none._"]
    L += [""]

    if mx:
        L += ["## B. The matrix label (what the model was graded on)", ""]
        L += ["| check | value |", "|---|--:|"]
        for k in ("rows", "positives", "positive_rate", "multi_source_positives", "no_source_tag",
                  "doj_positives", "doj_conduct_start_missing_share", "doj_scheme_unknown_share",
                  "doj_conduct_window_incoherent", "doj_cases_labelling",
                  "doj_npis_per_case_median", "doj_top5_case_share", "positives_zero_billing",
                  "positives_zero_billing_share", "excluded_in_matrix", "missed_positives",
                  "unexplained_positives", "lag_n", "lag_median_months", "lag_over_24m_share",
                  "billed_after_exclusion"):
            if k in mx:
                v = mx[k]
                L.append(f"| {k} | {_pct(v) if 'share' in k or 'rate' in k else (f'{v:,}' if isinstance(v, int) else v)} |")
        if mx.get("by_source"):
            L += ["", "Positives by source tag (a provider can carry several):", ""]
            L += ["| source | positives |", "|---|--:|"]
            for k, v in sorted(mx["by_source"].items(), key=lambda kv: -kv[1]):
                L.append(f"| {k} | {v:,} |")
        if mx.get("doj_by_basis"):
            L += ["", "DOJ positives by match basis:", "", "| basis | providers |", "|---|--:|"]
            for k, v in mx["doj_by_basis"].items():
                L.append(f"| {k} | {v:,} |")
        if mx.get("doj_top_cases"):
            L += ["", "DOJ cases supplying the most positives (org-billing broadcast):", "",
                  "| case_id | NPIs labelled |", "|---|--:|"]
            for r in mx["doj_top_cases"]:
                L.append(f"| {r['case_id']} | {r['npis']:,} |")
        for k in ("unexplained_sample", "missed_sample"):
            if mx.get(k):
                L += ["", f"{k}: {', '.join(mx[k])}"]
        L += [""]

    if cdb and cdb.get("rows"):
        L += ["## C. DOJ case DB (precision proxies + recall)", ""]
        L += ["| check | value |", "|---|--:|"]
        for k in ("rows", "usable", "usable_share", "with_npi", "with_npi_share", "name_key_share",
                  "amount_parsed_share", "amount_total_usd", "date_parsed_share", "year_min",
                  "year_max", "dup_case_id", "qui_tam_stated_share", "qui_tam_yes",
                  "intervened_stated_share", "intervened_yes", "scheme_known_share",
                  "sector_known_share", "primary_source_share", "near_dup_pairs",
                  "near_dup_dollars", "resolved_cases",
                  "resolved_share", "resolved_dollar_share", "unresolved_dollars"):
            if k in cdb:
                v = cdb[k]
                if "share" in k:
                    s = _pct(v)
                elif k.endswith("usd") or k.endswith("dollars"):
                    s = f"${v/1e6:,.1f}M"
                else:
                    s = f"{v:,}" if isinstance(v, int) else str(v)
                L.append(f"| {k} | {s} |")
        if cdb.get("by_year"):
            L += ["", "Cases by announcement year: " +
                  ", ".join(f"{y}: {n}" for y, n in cdb["by_year"].items())]
        if cdb.get("top_unresolved"):
            L += ["", "### Largest unresolved cases (no scored NPI carries this label)", "",
                  "| case_id | defendant | announced | amount |", "|---|---|---|--:|"]
            for r in cdb["top_unresolved"]:
                amt = float(r.get("amount_usd", 0) or 0)
                L.append(f"| {str(r.get('case_id', ''))[:50]} | {str(r.get('defendant_name', ''))[:60]} | "
                         f"{r.get('announced_date', '')} | ${amt/1e6:,.1f}M |")
        if cdb.get("unresolved_by_sector"):
            L += ["", "### Unresolved dollars by sector (pharma / device / health-plan defendants "
                  "never bill as a provider NPI, so those are unreachable by design; provider "
                  "sectors are the recall gap to work)", "",
                  "| sector | unresolved $ | cases |", "|---|--:|--:|"]
            cnt = cdb.get("unresolved_cases_by_sector", {})
            for k, v in cdb["unresolved_by_sector"].items():
                L.append(f"| {k} | ${v/1e6:,.0f}M | {cnt.get(k, 0):,} |")
        if cdb.get("near_dup_sample"):
            L += ["", "### Near-duplicate cases (same amount, same year, shared defendant token)", "",
                  "| case a | case b | defendant | year | amount |", "|---|---|---|---|--:|"]
            for p in cdb["near_dup_sample"]:
                L.append(f"| {p['case_a']} | {p['case_b']} | {p['defendant']} | {p['year']} | "
                         f"${p['amount_usd']/1e6:,.1f}M |")
        L += [""]

    if cia and cia.get("parties"):
        L += ["## D. OIG CIA list recall", ""]
        L += ["| check | value |", "|---|--:|"]
        for k in ("parties", "parties_in_window", "case_db_exact", "case_db_fuzzy", "case_db_recall",
                  "org_exact", "org_fuzzy", "org_resolvable_share"):
            if k in cia:
                v = cia[k]
                L.append(f"| {k} | {_pct(v) if 'share' in k or 'recall' in k else f'{v:,}'} |")
        if cia.get("by_status"):
            L += ["", "By status: " + ", ".join(f"{k}: {v}" for k, v in cia["by_status"].items())]
        if cia.get("case_db_unmatched"):
            L += ["", "### CIA parties not in the case DB (harvest these next)", "",
                  "| party | state | status | effective |", "|---|---|---|---|"]
            for r in cia["case_db_unmatched"]:
                L.append(f"| {r['entity_name'][:70]} | {r['state']} | {r['cia_status']} | {r['effective']} |")
        L += [""]

    L += ["## Findings and fixes", ""]
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    for x in sorted(fnd, key=lambda x: order.get(x["severity"], 9)):
        L.append(f"- **[{x['severity']}]** {x['finding']}  \n  _Fix:_ {x['fix']}")
    L += ["", "## Best-practice checklist for an FCA label", "",
          "1. **Key on NPI, never on name alone.** A name match is corroboration; the label rides on the identifier that joins to billing.",
          "2. **Date the conduct, not just the sanction.** Train on features before the conduct window; the sanction date can be years later (see the lag row).",
          "3. **Separate fraud from trouble.** Hard tier = FCA-shaped events; weak = misconduct; excluded = paperwork and debt. Report lifts per ruler.",
          "4. **Measure recall, not just precision.** The CIA list and the OIG semiannual reports are the public benchmarks; a harvest without a recall number is a guess.",
          "5. **Grade on the universe the model could see.** A positive that never billed is not a miss; use the active ruler.",
          "6. **Every label carries provenance.** Source, basis (named NPI / org billing / affiliation), match score, and a citation the reviewer can open.",
          "", "_Leads for counsel review, never accusations. Every record above is a public integrity event, not a finding about any provider._"]
    return "\n".join(L) + "\n"


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return o.astype(object).where(o.notna(), None).to_dict("records")
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    return o


def run(exclusion_nodes: str | Path, matrix: str | Path | None = None,
        case_db: str | Path | None = None, cia: str | Path | None = None,
        org_nodes: str | Path | None = None, cutoff: str | None = None,
        cia_year_min: int | None = None) -> tuple[str, dict]:
    nodes = pd.read_parquet(exclusion_nodes)
    m = load_matrix_slim(matrix) if matrix else None
    universe = set(m["npi"]) if m is not None else None
    billing = (set(m.loc[pd.to_numeric(m.get("net_paid"), errors="coerce").fillna(0) > 0, "npi"])
               if m is not None and "net_paid" in m.columns else None)
    ex = audit_exclusions(nodes, cutoff=cutoff, universe=universe, billing_npis=billing)
    mx = audit_matrix(m, nodes, cutoff) if m is not None else None
    cdb_df = None
    if case_db:
        p = str(case_db)
        cdb_df = pd.read_csv(p, dtype=str) if p.lower().endswith(".csv") else pd.read_parquet(p)
    cdb = audit_case_db(cdb_df, m) if cdb_df is not None else None
    cia_res = None
    if cia:
        from src.enforcement.cia import normalize_cia
        from src.attempt_2.clean_data import read_csv_text
        p = str(cia)
        raw = read_csv_text(p) if p.lower().endswith(".csv") else pd.read_parquet(p)
        cn = normalize_cia(raw)
        on = pd.read_parquet(org_nodes) if org_nodes else None
        cia_res = audit_cia(cn, cdb_df, on, year_min=cia_year_min)
    fnd = findings(ex, mx, cdb, cia_res, cutoff)
    inputs = {"exclusion_nodes": Path(exclusion_nodes).name,
              "matrix": Path(matrix).name if matrix else "",
              "case_db": Path(case_db).name if case_db else "",
              "cia": Path(cia).name if cia else ""}
    md = to_markdown(ex, mx, cdb, cia_res, fnd, inputs, cutoff)
    payload = {"inputs": inputs, "cutoff": cutoff, "exclusions": _jsonable(ex),
               "matrix": _jsonable(mx), "case_db": _jsonable(cdb), "cia": _jsonable(cia_res),
               "findings": fnd}
    return md, payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exclusion-nodes", required=True)
    ap.add_argument("--matrix", default=None, help="run's provider_features_for_model.parquet")
    ap.add_argument("--case-db", default=None, help="DOJ case DB csv/parquet")
    ap.add_argument("--cia", default=None, help="OIG CIA list csv (see enforcement.cia)")
    ap.add_argument("--org-nodes", default=None, help="graph/nodes/org_nodes.parquet")
    ap.add_argument("--cutoff", default=None, help="YYYY-MM for a frozen run")
    ap.add_argument("--cia-year-min", type=int, default=None,
                    help="only CIAs effective on/after this year count for recall")
    ap.add_argument("--out", required=True, help="LABEL_AUDIT.md path")
    a = ap.parse_args()
    md, payload = run(a.exclusion_nodes, a.matrix, a.case_db, a.cia, a.org_nodes,
                      a.cutoff, a.cia_year_min)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    jp = out.with_name("label_audit.json")
    jp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[label_audit] wrote {out} and {jp.name}")
    for x in payload["findings"]:
        print(f"  [{x['severity']}] {x['finding']}")


if __name__ == "__main__":
    main()
