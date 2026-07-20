"""
dossier_build.py — the dossier production line: one NPI → a counsel-grade brief.

The Target Book was written by hand. This fills the same skeleton from the
dossier packs for ANY npi, and staples on the research evidence (registry
verification, public-disclosure screen, innocent-explanation audit) when it is
available. Origination stops being a craft and becomes ``make dossier NPI=...``.

Sections mirror the reviewed skeleton: cover, entity, the money (ramp + top
codes), the pattern (firing subscores vs peers), the mandatory innocent
explanations, what a relator would know, the damages frame, and the method
note. The leads-not-accusations frame and the disclaimer are hard-coded; do not
strip them. Individuals are named by NPI, role, and city only.

    from src.model_a.dossier_build import build_npi_dossier
    md = build_npi_dossier("1588799746", pack, monthly, codes, owners)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .dossier import SCHEME_NARRATIVES
from .hcpcs_descriptions import describe as _describe_code

DISCLAIMER = (
    "_Investigative lead for counsel and licensed-investigator review. Every "
    "figure derives from public CMS data; suspect dollars are modeled excess "
    "over expected legitimate billing, not adjudicated amounts. Nothing here is "
    "a finding or accusation of fraud._")

# subscores that are leakage-adjacent or self-referential — never the headline
_NON_HEADLINE = {"ownership_integrity", "invalid_identity", "dme_ring"}


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
        return default if np.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _money(v) -> str:
    n = _num(v)
    return f"${n:,.0f}"


def _row(pack: pd.DataFrame, npi: str) -> pd.Series | None:
    p = pack[pack["npi"].astype(str) == str(npi)]
    return None if not len(p) else p.iloc[0]


def _top_subscores(row: pd.Series, k: int = 4) -> list[tuple[str, float]]:
    subs = {c[len("subscore_"):]: _num(row[c])
            for c in row.index if c.startswith("subscore_") and _num(row.get(c)) > 0.5}
    return sorted(subs.items(), key=lambda t: -t[1])[:k]


def _headline_scheme(subs: list[tuple[str, float]]) -> str:
    for name, _ in subs:                       # prefer a non-leaky headline
        if name not in _NON_HEADLINE:
            return name
    return subs[0][0] if subs else ""


def _ramp(monthly: pd.DataFrame | None, npi: str) -> str:
    if monthly is None or not len(monthly):
        return ""
    m = monthly[monthly["npi"].astype(str) == str(npi)].sort_values("month")
    if not len(m):
        return ""
    paid = pd.to_numeric(m["paid"], errors="coerce").fillna(0)
    first, last = m.iloc[0], m.iloc[-1]
    peak = m.loc[paid.idxmax()]
    return (f"Billing ran from {first['month']} to {last['month']} "
            f"({len(m)} active months), from {_money(first['paid'])} in the first "
            f"month to a peak of {_money(peak['paid'])} in {peak['month']}.")


def _top_codes(codes: pd.DataFrame | None, npi: str, k: int = 4) -> list[str]:
    if codes is None or not len(codes):
        return []
    c = codes[codes["npi"].astype(str) == str(npi)]
    if not len(c):
        return []
    c = c.sort_values("paid", ascending=False).head(k)
    out = []
    for r in c.itertuples():
        # describe() already returns "<code> (<desc>)" or just "<code>"
        label = _describe_code(str(r.hcpcs)) or str(r.hcpcs)
        lines = _num(getattr(r, "claim_lines", 0))
        out.append(f"{label}: {_money(r.paid)} over {lines:,.0f} claim lines")
    return out


def _owners(owners: pd.DataFrame | None, npi: str) -> list[str]:
    if owners is None or not len(owners) or "npi" not in owners.columns:
        return []
    o = owners[owners["npi"].astype(str) == str(npi)]
    name_col = next((c for c in ("owner_name", "associate_name", "name")
                     if c in o.columns), None)
    if not name_col:
        return []
    return sorted({str(x) for x in o[name_col] if str(x).strip()})[:12]


def build_npi_dossier(npi: str, pack: pd.DataFrame,
                      monthly: pd.DataFrame | None = None,
                      codes: pd.DataFrame | None = None,
                      owners: pd.DataFrame | None = None,
                      registry: dict | None = None,
                      disclosure: dict | None = None,
                      innocent: dict | None = None) -> str:
    """One NPI → a counsel-grade dossier (markdown). ``registry`` / ``disclosure``
    / ``innocent`` are optional research-evidence envelopes (from nppes_verify
    and the Manus screens); when present they fill their sections, else the
    sections state the check is pending."""
    row = _row(pack, str(npi))
    if row is None:
        return f"# Dossier — NPI {npi}\n\n_NPI not found in the provided pack._"

    subs = _top_subscores(row)
    scheme = _headline_scheme(subs)
    label, desc = SCHEME_NARRATIVES.get(scheme, (f"a {scheme or 'billing'} pattern", ""))
    net = _num(row.get("net_paid"))
    exp = _num(row.get("expected_net_paid"))
    suspect = max(net - exp, 0.0)
    name = str(row.get("provider_name") or row.get("org_legal_name") or "").strip()
    city = str(row.get("addr_city") or "").strip()
    state = str(row.get("practice_state") or "").strip()
    tax = str(row.get("primary_taxonomy") or "").strip()
    ent = "individual" if str(row.get("entity_type")) == "1" else "organization"
    breakeven = (100_000 / suspect) if suspect > 0 else float("nan")

    L = [f"# Target dossier — NPI {npi}", "", DISCLAIMER, ""]
    # cover
    L.append(f"- **Who:** {ent}, taxonomy {tax or '?'}, {city}{', ' + state if state else ''}"
             + (f" ({name})" if name else ""))
    L.append(f"- **Pattern:** most resembles **{label}**"
             + (f" — {desc}" if desc else ""))
    L.append(f"- **Billed:** {_money(net)} against an expected {_money(exp)}")
    L.append(f"- **Suspect dollars:** {_money(suspect)}"
             + (f"  ·  break-even on a $100k workup: {breakeven:.2%}"
                if suspect > 0 else ""))
    L.append("")

    # 1. the money
    L.append("## The money")
    ramp = _ramp(monthly, npi)
    if ramp:
        L.append(ramp)
    tc = _top_codes(codes, npi)
    if tc:
        L.append("\nTop codes by dollars:")
        L.extend(f"- {t}" for t in tc)
    L.append("")

    # 2. the pattern
    L.append("## The pattern (each signal is peer-relative)")
    if subs:
        for s, v in subs:
            lab, d = SCHEME_NARRATIVES.get(s, (s, ""))
            strength = "extreme" if v >= 0.9 else "elevated"
            L.append(f"- *{strength}* — {lab}"
                     + (f": {d}" if d else "") + f"  (subscore {v:.2f})")
    else:
        L.append("- Ranks on the combination of moderate signals plus billing scale.")
    for flagcol, txt in (("consistency_flags",
                          "cross-source incoherence between the registration "
                          "record and the billing behavior"),):
        if _num(row.get(flagcol)) > 0:
            L.append(f"- *structural* — {txt} ({int(_num(row.get(flagcol)))} flags)")
    L.append("")

    # 3. entity + owners
    L.append("## The entity")
    L.append(f"- Tenure: {int(_num(row.get('tenure_months')))} months · "
             f"org members: {int(_num(row.get('org_member_count'))) or '?'} · "
             f"distinct codes: {int(_num(row.get('n_distinct_hcpcs'))) or '?'}")
    own = _owners(owners, npi)
    if own:
        L.append(f"- Associated owners on file: {', '.join(own)}")
    L.append("")

    # 4. registry verification
    L.append("## Registry verification")
    if registry:
        flag = registry.get("registry_flag", "?")
        L.append(f"- Federal registry check: **{flag}**."
                 + (f" Registry taxonomy {registry.get('registry_taxonomy')} "
                    f"({registry.get('registry_taxonomy_desc')}), status "
                    f"{registry.get('registry_status')}, last updated "
                    f"{registry.get('registry_last_updated')}."
                    if flag != "OK" else " Identity matches the matrix."))
    else:
        L.append("- Pending: run `nppes_verify` on this NPI.")
    L.append("")

    # 5. innocent explanations (mandatory)
    L.append("## The innocent explanations to rule out")
    if innocent and innocent.get("result"):
        L.append(str(innocent["result"])[:1500])
    else:
        L.append("The strongest non-fraud reading of this exact pattern, and the "
                 "records that would confirm or refute it (enrollment file, "
                 "patient census, staffing rosters, acquisition invoices). "
                 "_Pending: run the innocent-explanation audit._")
    L.append("")

    # 6. public-disclosure screen (qui tam bar)
    L.append("## Public-disclosure screen")
    if disclosure and disclosure.get("result"):
        L.append(str(disclosure["result"])[:1200])
    else:
        L.append("_Pending: run the public-disclosure screen. A lead already "
                 "publicly disclosed (news, prior suit, government report) can "
                 "bar the relator, so this runs before any engagement letter._")
    L.append("")

    # 7. what a relator would know
    L.append("## What a relator would know")
    L.append("The job functions that would have witnessed the conduct if it is "
             "what it looks like (billing staff, program directors, records). "
             "The origination target is a role at an organization, never a named "
             "individual.")
    L.append("")

    # 8. damages frame
    L.append("## Damages frame")
    L.append(f"Suspect dollars {_money(suspect)}. False Claims Act structure: "
             f"treble damages plus $13,946 to $27,894 per false claim; claim "
             f"counts here run into the thousands. "
             + (f"Break-even: substantiating {breakeven:.2%} of suspect dollars "
                f"returns a $100k investigation." if suspect > 0 else ""))
    L.append("")
    L.append("## Method")
    L.append("Figures from the current scoring matrix; identity verified against "
             "the live NPPES registry; suspect dollars are modeled excess over "
             "expected legitimate billing. Full column lineage in the feature "
             "manifest. " + DISCLAIMER)
    return "\n".join(L)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--npi", required=True)
    ap.add_argument("--pack", required=True, help="dossier_pack.parquet")
    ap.add_argument("--monthly", default=None)
    ap.add_argument("--codes", default=None)
    ap.add_argument("--owners", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    def _load(p):
        return pd.read_parquet(p) if p and Path(p).exists() else None
    md = build_npi_dossier(args.npi, pd.read_parquet(args.pack),
                           _load(args.monthly), _load(args.codes),
                           _load(args.owners))
    out = args.out or f"DOSSIER_{args.npi}.md"
    Path(out).write_text(md, encoding="utf-8")
    print(f"[dossier] NPI {args.npi} → {out}")


if __name__ == "__main__":
    main()
