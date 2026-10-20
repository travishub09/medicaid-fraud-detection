# Label verification read, 2026-10-20

The first live research-lane cycle verified 110 strict-label rows (104 decided):
confirmed 96 (92 percent), wrong_npi 5, wrong_person 3, cannot_verify 6, pending 9.
The written rule (confirmed under 90 percent OR more than one wrong_npi = tighten)
trips on the wrong_npi count. This is the read of the eight flagged rows and the
four rules they became. Precision by evidence strength stays at the 2026-09-29
reading (strong 39/40, moderate 92 percent): the fix is targeted gates, not a
strength-floor move, because the wrong rows sit in both strata.

## The eight rows

| # | NPI | defendant | basis / level / strength | verdict | the read | rule |
|---|---|---|---|---|---|---|
| 1 | 1396720504 | Martin E. Cutler, M.D., P.C. (MA) | resolved_defendant / name_state / moderate | wrong_npi | A solo professional corporation named for its physician; the individual NPI is the P.C.'s operator (the same policy that keeps a convicted owner's billing vehicle). KEEP. | none (policy: `name_agrees` accepts the P.C.-principal shape) |
| 2 | 1366065849 | Live Care Inc., Venice FL | resolved_defendant / research_medium / moderate | wrong_npi | Researched NPI is LIVECARE FLORIDA, P.A. in Tampa: an extra distinctive word and a different city. AND the case row's source is `usao-mdfl/pr`, the office's press-release index, not a release. WRONG, twice. | 1 + 3 |
| 3 | 1124314356 | Dermatology Healthcare of Florida, LLC | resolved_defendant / research_medium / moderate | wrong_npi | Researched NPI is NATIONAL DERMATOLOGY HEALTHCARE, LLC: a mention word (Florida) missing from the NPPES name. Possibly an affiliate; not the named party. WRONG until ownership says otherwise. | 3 |
| 4 | 1124066097 | Lokesh Tantuwaya (CA AG) | resolved_defendant / name_state / moderate | wrong_person | The cited release names Dr. Lagattuta and Lags Medical, never Tantuwaya: the research harvest attached the wrong defendant to the URL. WRONG. | 4 |
| 5 | 1679467971 | Stephanie Cook (MS state list) | name_match / unique_in_state / moderate | wrong_npi | The list row is a nurse's aide with no NPI; the unique in-state name hit is a family nurse practitioner. WRONG. | 2 |
| 6 | 1609955350 | Frederick Brown (OIG, TX) | resolved_defendant / name_city_state / strong, role agrees | wrong_person | The agent read the OIG listing, which names the lab executives; the physician is named in the linked release our harvest read (the mention carried his city). KEEP; re-verify from the DOJ release. | none |
| 7 | 1831961044 | Mika Kamissa Harris (ND FL) | resolved_defendant / research_high / strong | wrong_npi | Researched NPI is MIKALA HARRIS, Longwood: a different first name. WRONG (a research answer at high confidence, which is why the gate reads NPPES itself). | 3 |
| 8 | 1467560805 | Sudipta Mazumder MD (NC) | resolved_defendant / name_state / strong, role agrees | wrong_person | The source is a 40-page NC legislative MFCU report; the agent read the first cases on it. Our mention came from the report's own text. KEEP; cannot_verify is the honest grade. | none |

Net: five wrong (2, 3, 4, 5, 7), three keep (1, 6, 8). Estimated precision on the
decided rows after the read: 99 of 104.

## The four rules (`src/enforcement/label_gates.py`, bundle 330)

1. **Index-page URL** (`is_index_url`): a case row whose source is a listing page
   (`/usao-xx/pr`, `/news`, `/fraud/enforcement/`) is never evidence. Quarantined by
   the CIA / commercial / MFCU harvest loaders, dropped by `case_labels` on every
   file (log line), so the rows already in the case DB leave the label at the next
   export.
2. **Non-NPI occupation** (`non_npi_role`): a state-list row whose own words say
   aide / CNA / PCA / driver / clerk / homemaker / "individual worker" (and no
   NPI-holding profession beside them) never name-matches NPPES
   (`match_basis = non_npi_role`). The list's type / category / title / description
   columns are all read. Re-run the state loader to apply.
3. **Research name agreement** (`name_agrees`, in `defendant_research.fold_results`
   when a provider_dim is given; the lane passes `processed/provider_dim.parquet`,
   DuckDB-filtered): a researched NPI must carry the defendant's name in NPPES. A
   person needs the last name present and the FIRST name exact (Mika is not Mikala);
   an org needs every distinctive mention word in the NPPES name, at most one extra
   word, and city agreement when both are known. A conflict is
   `status = research_name_conflict` with the NPI kept in `npi_candidate` and the
   reason in `gate_note`, listed in the lane quarantine. The solo P.C. shape agrees.
4. **Defendant on the page** (`name_in_page`, `research_evidence verify-sources
   --case-db`): for the research-harvested state and commercial rows, the cited page
   is read and a row whose defendant is not named on it is marked `usable=0`,
   `qc_note = defendant_not_on_page` (quarantine, never delete). A person needs first
   and last name within 60 characters; an unreachable page is left unknown.

Also: the names lane now writes its refused rows to `names_quarantine.csv` (236 rows
had no file), and the research lane writes `defendant_research_quarantine.csv` with
the name conflicts.

## What the operator does

On the VM after bundle 330 is pulled, in this order (the commands are in reminder
`lanes_gates_apply`): re-load the lanes (free; the research fold now checks NPPES),
re-run the state-termination loader (the aide rows drop their NPI), run
`verify-sources` on the case DB (network: the state / commercial rows against their
pages), then v18 with `--rebuild-graph`. The three KEEP rows need no action; row 6
goes back to the verification sample from the DOJ release URL next cycle.
