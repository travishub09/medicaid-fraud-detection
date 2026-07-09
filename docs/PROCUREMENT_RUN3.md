# Run 3 Data Procurement Plan

Written July 9, 2026, from the audit of `C:\Users\treyr\OneDrive\Desktop\data`
(DATA_AUDIT.txt) plus a code review of what each adapter expects. Three parts:
what you already have (verified, do not re-download), the one broken file to
replace, and the missing data ranked by how much it improves the model.

---

## Part 1. What you already have, verified. Do NOT re-download.

The audit confirmed 22 of 26 sources are on disk with the right layout. This is
almost everything. Highlights:

| Source | Status on your machine | Feeds |
|---|---|---|
| Medicaid Spending.csv / spending fact | 238M rows, 2018-01 to 2024-12 | the whole pipeline |
| NPPES registry | 11.5 GB, correct layout | every per-NPI feature |
| LEIE exclusions | fresh through June 2026; 5,410 NPI-carrying bans after 2023-12 | the fraud label AND Travis's forward test |
| PECOS All Owners files (FQHC, HHA, Hospice, Hospital + 3 more) | April/May 2026 vintage, correct layout | ownership graph, banned-owner flags |
| Medicare Part B, by provider and service | 9 yearly files, 2016 onward | upcoding |
| Medicare Part D, by provider and drug | 8 yearly files (one bad, see Part 2) | drug outlier, kickback |
| DMEPOS, by referring provider and service | 8 yearly files, correct layout | DME rings, influenced dollars |
| Open Payments | 8.9 GB | kickback |
| CMS opioid metrics | 2024 file | pill mill |
| Market Saturation | correct layout | saturation fraud |
| Order & Referring | correct layout | ineligible referrals |
| NPPES deactivation report | zip present | billing after deactivation |
| HRSA 340B OPAIS | two Excel files present | contract pharmacy |
| PBJ staffing, hospice measures, deficiencies | present (the audit's "encoding" errors were a false alarm in the audit script, not your files) | worthless services, hospice ineligibility |
| HCRIS cost reports (Hospital, SNF, HHA 2023) | correct layout | cost report fraud |
| NUCC taxonomy + specialty crosswalk | already on disk | better peer groups for EVERY scheme |
| Medicare revocations | correct layout | widens the fraud label |
| OpenSanctions bulk | already downloaded AND already processed (exclusions_opensanctions.parquet exists) | 45 state exclusion lists |
| NADAC drug pricing | correct layout | drug spread anomaly |

Bottom line: your data is deeper than the plan assumed. Procurement is mostly done.

---

## Part 2. One broken file to replace (10 minutes) - DO THIS FIRST

**Medicare Part D 2018.** Your `partd_2018.csv` is the wrong dataset. It is the
provider SUMMARY (no drug names), while every other year is the correct
"by Provider and Drug" file. This leaves a hole in the multi-year drug features.

1. Go to: https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/medicare-part-d-prescribers-by-provider-and-drug
2. In the data section, pick the **2018** vintage and download the full CSV.
   Make sure the page says "by Provider and Drug", not "by Provider".
3. Save it OVER the old file as:
   `C:\Users\treyr\OneDrive\Desktop\data\preclean\partd\partd_2018.csv`
4. Sanity check: the file should have columns named `Brnd_Name` and `Gnrc_Name`.
   The old (wrong) file does not.

---

## Part 3. Missing data, ranked by impact

### Priority 1: DocGraph shared-patient data (30 to 60 minutes)
**Why it matters most:** it adds REFERRAL edges between providers, which is a
brand-new network signal, not a re-mix of what you have. Fraud rings show up as
tight referral loops. If the network test is your showpiece for Travis and Brad,
this deepens it more than anything else on this list. The adapter is already
wired (`ingest_cms.docgraph`).

1. Free historical files: https://www.nber.org/research/data/physician-shared-patient-patterns-data
   (the CMS "Physician Shared Patient Patterns" files, hosted by NBER).
   Download one year's file (the 30-day version is standard).
2. Save to: `C:\Users\treyr\OneDrive\Desktop\data\preclean\docgraph\`
   (any .csv in that folder is picked up; the file has NPI pairs plus a shared
   patient count).
3. Note: these files are older (2009 to 2015). Referral-ring STRUCTURE ages
   well, but flag the vintage in any writeup. Current-year versions are sold by
   CareSet (https://careset.com) - a Brad decision, not needed for Run 3.

### Priority 2: HUD ZIP-to-county crosswalk (5 minutes)
**Why:** upgrades the saturation signal from state level to county level, which
removes a known weakness in the 10.3x saturation headline.

1. Go to: https://www.huduser.gov/portal/datasets/usps_crosswalk.html
2. Pick the ZIP-COUNTY file, latest quarter, Excel or CSV.
3. Save as: `C:\Users\treyr\OneDrive\Desktop\data\preclean\zip_county\zip_county.csv`
   (if it downloads as Excel, open it and save-as CSV).

### Priority 3: Provider of Services file (10 minutes)
**Why:** facility capacity data (beds, staff counts) that feeds the
worthless-services checks. Small but cheap.

1. Go to: https://data.cms.gov and search "Provider of Services File
   Hospital & Non-Hospital Facilities".
2. Download the latest quarterly CSV.
3. Save as: `C:\Users\treyr\OneDrive\Desktop\data\preclean\pos\pos.csv`

### Priority 4 (optional): more opioid years (15 minutes)
**Why:** you only have 2024. Adding 2021 to 2023 gives the pill-mill signal a
trend dimension. Same page style as Part D:
https://data.cms.gov/summary-statistics-on-use-and-payments/medicare-medicaid-opioid-prescribing-rates
Save as `preclean\opioid\opioid_prescriber_<year>.csv`.

---

## Part 4. Paid or gated. Brad decisions. Do NOT block Run 3 on these.

| Item | Cost | What it adds |
|---|---|---|
| SSA Death Master File (https://dmf.ntis.gov) | about $200/yr | billing-after-death checks |
| CareSet current DocGraph | quote | current-year referral edges |
| USPS CMRA mailbox list | licensing | exact mail-drop storefront flag |
| People-data vendor + FCRA review | significant | activates Model B (whistleblower ID) |
| 64 GB compute for graph embeddings | $10 to $40 one time, NOT $500 | round-2 embeddings test, only if the forward verdict says the network family is real |

---

## Part 5. After the downloads

1. Re-run the audit to confirm everything landed right:
```
python "C:\Users\treyr\Downloads\data_audit.py" > "C:\Users\treyr\Downloads\DATA_AUDIT2.txt"
```
2. Start the full rebuild:
```
"C:\Users\treyr\Downloads\run3.bat"
```
3. Send the five report files it names at the end. We verify together, then the
Travis package goes out.
