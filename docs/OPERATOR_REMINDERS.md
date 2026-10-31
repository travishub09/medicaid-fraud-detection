# OPERATOR REMINDERS (auto-generated — edit docs/reminders.json, not this file)

_As of 2026-10-31. Regenerate/update: `python -m src.reminders` (`--done <key>` after you fetch something)._

## DUE NOW

Nothing due. Good.

## COMING UP (30 days)

- mue — due 2026-11-02: CMS MUE quarterly files (practitioner + facility)
- pos — due 2026-11-02: Provider of Services file (quarterly)
- pbj — due 2026-11-02: PBJ Daily Nurse Staffing (quarterly)
- care_compare — due 2026-11-02: Care Compare provider-data archives (hospice measures, NH citations/ownership/penalties)
- nadac — due 2026-11-02: NADAC drug pricing (drug_spread_anomaly)
- work_plan — due 2026-11-02: OIG Work Plan curated table refresh (government_interest overlay)
- hrsa_340b — due 2026-11-02: 340B OPAIS Covered Entity Daily Report (contract_pharmacy)
- nppes_deactivation — due 2026-11-07: CMS Deactivated NPI Report (invalid_identity scheme)
- owner_snapshot — due 2026-11-07: make owner-snapshot (no download — archives owner edges; ownership_turnover needs 2+ months)
- sam_exclusions — due 2026-11-09: SAM.gov exclusions extract (widens the PU label)
- warn_notices — due 2026-11-09: WARN layoff notices for the OUTREACH-TIMING tool only (sourcing/warn_monitor + event_timeline): not a model feature, never trained on. A notice is useful while its outreach window is open (6 to 18 months after the departure), so the current year and the one before are worth having; older years are expired windows. Monthly: the current-year table per state.
- nppes_practice_locations — due 2026-11-14: NPPES Practice Location file (pl_pfile, inside the monthly NPPES zip): secondary practice sites per NPI, so 'one suite' is a fact in the capacity check
- leie — due 2026-11-21: OIG LEIE exclusion list (THE label source — new bans are training labels)

## STANDING TASK QUEUE

- [ ] **sims_complaint_pacer** — BUY THE SIMS COMPLAINT (at most $3; the one document still to read on Accu): the research agent confirmed it is unsealed (D.N.J., Sims v. Accu Reference Medical Lab, filed 2021-03-04, government declined 2023-05-08, unsealed 2023-05-15, dismissed without prejudice by stipulation 2023-06-15) but had no PACER account. STEPS: (1) open https://www.courtlistener.com/docket/67387905/sims-v-accu-reference-medical-lab-llc/ ; (2) on docket entry 1 (the Complaint) click 'Buy on PACER' (a PACER login is needed: pacer.uscourts.gov, Register, individual account, no fee under $30 a quarter); (3) save the PDF as <data_root>\enforcement\pi_reports\accu\Sims_complaint_ECF1.pdf (work product, never git) and drop it in chat; Claude reads it element by element against the definitive-testing theory (same transactions, same years, what a post-2021 insider adds) and the Accu handoff is re-sent. Also worth $3 each if present: the unsealing order (entry 15) and the stipulation (entry 16). (added 2026-10-30)

```bat
python -m src.reminders --done-task sims_complaint_pacer
```

- [ ] **pacer_api_setup** — PACER + COURTLISTENER API FOR THE PIPELINE (the analyst sets it up, Brad pays; the email went 2026-10-31): the court-record check on every candidate runs through the CourtListener search API on a free token and the service refuses the list after a few dozen names (two days of overnight passes for 250 names), and nobody can buy a complaint without a PACER login (the Sims complaint sat unread four days). SETUP (the analyst): (1) PACER account at pacer.uscourts.gov > Register > 'PACER - Case Search Only', Brad's card for billing ($0.10 a page, $3 cap a document, waived when the quarter stays under $30); (2) CourtListener account (free) > Profile > API Token; (3) the token and the PACER username + password reach Trey by text or the password manager, never email, and go into ~/.medicaid_env on the VM as COURTLISTENER_TOKEN (replacing the current one) + PACER_USERNAME + PACER_PASSWORD; (4) Claude then wires first_to_file to the RECAP Fetch endpoint (/api/rest/v4/recap-fetch/: a PACER purchase by docket + entry number through the API, the PDF lands in the public RECAP archive with no purchaser name) so a complaint is pulled the day it is unsealed. The Sims complaint itself is bought by hand first (task sims_complaint_pacer). Close when the three values are in ~/.medicaid_env. (added 2026-10-31)

```bat
python -m src.reminders --done-task pacer_api_setup
```

- [ ] **ssh_retry_rule** — SSH LINE FORMAT from 2026-10-30 18:40: every VM line carries -n (never reads the next pasted line) and -o ConnectionAttempts=6 -o ConnectTimeout=10 (six tries ten seconds apart, so the laptop's minute-long path drops no longer fail a line). The security group has been open to 0.0.0.0/0 all day; fail2ban is on; auth.log showed one failed login, so nothing is flooding the VM: the drops are on the laptop's network path. If a line still times out after the six tries, wait a minute and paste it again; if it happens on one particular network every time, say 'do 443' and SSH gets a second port hotspots never block. Close once a day passes without a timeout. (added 2026-10-30)

```bat
python -m src.reminders --done-task ssh_retry_rule
```

- [ ] **vm_ssh_rule** — DECIDED 2026-10-30 (operator: ease over safety; the /32 rule timed out three times in one day as the home IP rotated): open the SSH rule to 0.0.0.0/0 ONCE and leave it. The instance accepts key-only logins (AWS Ubuntu images ship with PasswordAuthentication no), so the .pem on the laptop is the lock; the only cost is scanner noise, which fail2ban (line 2, after the next successful ssh) throttles. Console: EC2 us-east-2 > Security Groups > sg-01ed08aca02724f15 > Edit inbound rules > the SSH row Source = Anywhere-IPv4 (0.0.0.0/0) > Save. CLI alternative = line 1 (needs the aws cli configured on the laptop). Also allocate an ELASTIC IP and attach it to the instance so the address never changes on a stop/start (EC2 > Elastic IPs > Allocate > Associate; free while attached to a running instance). Close when a cold ssh works from a new network. (added 2026-10-30)

```bat
aws ec2 authorize-security-group-ingress --region us-east-2 --group-id sg-01ed08aca02724f15 --protocol tcp --port 22 --cidr 0.0.0.0/0
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "grep -i '^PasswordAuthentication' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null; sudo apt-get install -y -qq fail2ban >/dev/null 2>&1 && sudo systemctl enable --now fail2ban && echo fail2ban on"
python -m src.reminders --done-task vm_ssh_rule
```

- [ ] **ohio_cost_report_followups** — THE OHIO COST REPORTS, READ (2026-10-30; 21 homes, 70 facility-years; OHIO_COST_REPORTS.md + ohio_cost_reports.csv sent in chat: save under data\enforcement\pi_reports\wesleyan\ohio_cost_reports\, never git). WHAT THEY SHOW: (1) Wesleyan Village answers NO to 'any costs from a related party' (Schedule C-3 Q1) in all four years while booking consulting/management fees that rise from $23k (2021) to $275k (2024), carrying related-party LOAN INTEREST on Schedule E ($1.24M in 2023, $2.76M in 2024, non-allowable), listing NO related facilities on Q5 while its president sits as SVP on 13 Northwood/Eastwood homes' officer lists, declaring no home office, and filing an EMPTY $10,000+ contract list in 2023 and 2024 (the 2021 and 2022 lists are identical, copied forward). (2) Buckeye Forest Greenville and Piqua answer NO all three years while naming Comprehensive LTC Services ('financial management', $127k-$137k) and Katone Consulting ('operation management', $56k-$84k) on the contract list and booking $10.8M / $13.1M of related-party loan interest in 2023; no officers listed (Q4 blank) in any year. (3) Mayfield, Euclid Beach, Minerva answered NO in 2022 with the Eastwood home office declared, YES from 2023; Q4 blank every year. (4) Group-wide, 34 facility-years declare a home office (Northwood / Eastwood / Garden Healthcare Group) with every home-office cost line at zero while management fees of $110k to $459k are booked, and 51 facility-years book $10k+ of management fees with no management contractor on the $10k+ list. RECORDS QUESTIONS (to the research agent first, task ohio_cr_research; counsel reads the answers): who owns Comprehensive LTC Services, Katone Consulting, Northwood Healthcare Group, Eastwood Healthcare (Ohio SOS + the PECOS owner file); who received Wesleyan's 7610 fees in 2023-24 (no contractor named); the lenders behind the related-party loan interest (Schedule E detail, the mortgage holders on Q3); whether ODM's rate-setting audit ever reclassified these. NEXT: the HCRIS A-8-1 side (hcris_affiliates --focus on the 21 CCNs) joined to this table = the two-filing contradiction per home; the Wesleyan resident's case gets the cost-report page as its second document. Close when the join is in the Wesleyan PI file. THE RESEARCH READ (2026-10-30, five answers): CMS enrollment lists WV Healthcare Management LLC in operational/managerial control of Wesleyan since 2019-07-03 with Ephram Lahasky president, Evan Lahasky treasurer, Eliezer Gewirtzman secretary; Arroyo v. Wesleyan Village (N.D. Ohio 2020, settled 2023, dismissed with prejudice) states Northwood managed day-to-day operations under a management contract from 2019-07-04; the Gifford FLSA order (S.D. Ohio 2:22-cv-04389, 2023) records a strong likelihood Northwood and Garden are one enterprise (shared COO, identical handbooks). CORRECTION to the cost-report read: the Ayden / Buckeye Forest six ARE Lahasky homes on the CMS record (Piqua 100% Lahasky in 2022, 51% by 2025; Euclid 100% indirect; Minerva 70%; Oregon 51/49 with Kaplan; Waterville / Wauseon / Toledo Lahasky-Kaplan; eight CHOWs to Buckeye Forest entities on 2021-12-31), so the Ohio filings listing NO officers where PECOS lists the owner is itself the contradiction. Lenders found (Dwight Capital, White Oak, Metropolitan Commercial Bank) are not shown owner-related, so the $10.8M / $13.1M of related-party loan interest claimed comes from loans the public record does not show. The Ohio SOS and UCC portals blocked the agent: a person runs businesssearch.ohiosos.gov for every entity (an afternoon; Josh could). No audit, appeal, settlement or enforcement matter found for any home. NEXT: (1) the SOS pull (Josh), (2) the county recorder images for Lorain / Miami / Darke, (3) the ODM home-office cost statement + related-party schedules by public-records request (same route as the cost reports), (4) the CourtListener names pass below, (5) interviews by role from the formers the research found (13 across the group, names in the research CSVs only). The handoff went to ChatGPT 2026-10-30 (destination: the new SIU group, Ohio). (added 2026-10-30)

```bat
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && export MEDICAID_DATA_ROOT=/data/medicaid && printf '%s\n' 'WESLEYAN VILLAGE' 'WV HEALTHCARE MANAGEMENT' 'NORTHWOOD HEALTHCARE GROUP' 'GARDEN HEALTHCARE GROUP' 'EASTWOOD HEALTHCARE' 'BUCKEYE FOREST AT PIQUA' 'BUCKEYE FOREST AT GREENVILLE' 'AYDEN HEALTHCARE' 'KATONE CONSULTING' 'COMPREHENSIVE LTC SERVICES' > /data/medicaid/enforcement/ohio_names.txt && python -m src.enforcement.first_to_file --since 2010-01-01 --name-gap 12 --names-file /data/medicaid/enforcement/ohio_names.txt --out /data/medicaid/enforcement/first_to_file_ohio && grep -h -B3 '| YES ' /data/medicaid/enforcement/first_to_file_ohio/FIRST_TO_FILE.md | grep -E '^## |^\| YES' | head -40"
python -m src.ingest_cms.ohio_cost_report --in C:\Users\treyr\OneDrive\Desktop\data\enforcement\pi_reports\wesleyan\ohio_cost_reports --out C:\Users\treyr\OneDrive\Desktop\data\enforcement\pi_reports\wesleyan\ohio_cost_reports\read  (needs pdftotext on the laptop: poppler; else use the csv already sent)
python -m src.reminders --done-task ohio_cost_report_followups
```

- [ ] **v26_top250_docket_read** — MORNING READ of the overnight court-record pass on v26's top 250 (started 2026-10-30 ~01:10 UTC in tmux ftf250; about two hours at a 12-second gap). The question it answers: how many of the 250 have an open or past False Claims Act case on the PUBLIC docket (the NPI-tagged count is 7 resolved / 0 open; the exact-name pass against the case database found no more). Line 1 prints the summary (names searched, dockets found, FCA-shaped) and every FCA-shaped docket with its company; names the service REFUSED (HTTP 429) rerun later as a names file with --name-gap 20, never back to back. Paste the output; the combined figure goes on slide 2 of the Brad deck if it moves. A sealed whistleblower case is invisible to every public search: the count is a floor. FIRST PASS READ 2026-10-30 07:00: 33 of 250 names searched before the service refused the rest (217 refused). Among the 33: open unsealed FCA dockets on Inova (Santry v. Inova, D.D.C. 2019, no termination) and Accu Reference (the Thermo Fisher case); past FCA dockets on Quest, Stanford Health Care, Nassau Health Care, Assurex, UCHealth. The retry runs the 217 refused names in chunks of 25 with a 20-second gap and 30 minutes between chunks (ops/vm/ftf_retry.sh, about five hours) in tmux ftf250b; the read line prints every chunk's summary and the FCA rows. STATE 2026-10-30 22:30 UTC: first pass 33 + retry chunks 1-3 (75) searched = 108 names, 20 orgs with an FCA-type docket (open today: Community Health Network x2, Inova/Santry, Accu/Thermo Fisher, Michigan Healthcare Professionals in Cardinal Health; past: LabCorp/Caris, Ideal Option, Oakwood, Trilogy, Pathways/Molina, Fulgent, LOVING CARE AGENCY (its own 2011-2018 D.N.J. qui tam: a v27 dossier name), Quest x2, Pioneers/Healogics, Accredo x2, LIFECARE MEDICAL TRANSPORTS (2017-2020 Whitaker qui tam, E.D. Va.), Stanford x4, Nassau, Assurex/Bachman, UCHealth). Retry chunks 4-6 were refused again (CourtListener 429). Third pass ftf250c (108 names, 15 a chunk, 30 s gap, 40 min between chunks) started 22:10 UTC and runs about five hours alone; ftf250b killed; the 67 leftover names (chunks 7-9 never ran) are in ftf250_retry/leftover_names.txt for the LAST pass (line 2) only after ftf250c's log reads DONE (line 1). Then line 3 = the full tally over all four folders. RULE: one CourtListener session at a time, ever. STATE 2026-10-31 (morning read, ftf250c DONE, no tmux session): 108 names in 8 chunks of 15; chunks 1-5 read 'dockets found: 0' on 75 names, which is the refused shape (a real search of 15 health-care companies never returns zero dockets), chunks 6-8 found 131 / 73 / 81 dockets with 5 / 2 / 0 FCA-shaped. Line 1 pulls the code on the VM (no run is going) and runs ops/vm/ftf_tally.py: every pass tallied, each name's BEST result across passes, the FCA-shaped dockets per company de-duplicated, and ftf250_remaining_seed.md = the names still owed a search (refused + the 67 never attempted); line 2 starts the LAST pass on that seed through ftf_retry.sh (15 a chunk, 30 s between names, 40 min between chunks) in tmux ftf250d. Then the full tally over first_to_file_v26top250 + ftf250_retry + ftf250_retry2 + ftf250_retry3. (added 2026-10-30)

```bat
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git pull --ff-only && source .venv/bin/activate && python ops/vm/ftf_tally.py /data/medicaid/enforcement; tmux ls"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tmux new -d -s ftf250d 'cd ~/medicaid-fraud-detection && bash ops/vm/ftf_retry.sh /data/medicaid/enforcement/ftf250_remaining_seed.md /data/medicaid/enforcement/ftf250_retry3 15 30 2400 2>&1 | tee /data/medicaid/enforcement/ftf250d.log'; sleep 5; tmux ls; tail -3 /data/medicaid/enforcement/ftf250d.log"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tail -2 /data/medicaid/enforcement/ftf250d.log; tmux ls"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && python ops/vm/ftf_tally.py /data/medicaid/enforcement"
python -m src.reminders --done-task v26_top250_docket_read
```

- [ ] **track3_sweep** — THE THIRD TRACK (visit-decided shapes beyond labs and day care; built 2026-10-29 for the pre-presentation round): three screens on the cd1 fact, ambulance_repetitive (stretcher runs at dialysis intensity with no emergencies: the card is watching who walks to the rig), nemt_fleet (trips a day a fleet cannot make: the card is counting vehicles at the base), dme_mailbox (equipment suppliers billing from a mailbox / PO box or a suite shared by 5+ organizations: the card is the storefront). ORDER: v27 is running, so the code goes into a SECOND checkout (~/mfd_screens, line 1; the running tree is never pulled mid-run; the repo is not pip-installed, so the main venv runs the clone's code), run the sweep (line 2), paste the three Candidates tables, court records on the top twenty names per screen (line 3, about 15 minutes each, silent), then the kill prompts are written from the tables and dispatched on the operator's go (credits). Read the distribution line above each per-day table before the rank (a median peak in the thousands means lines are miles, not trips). FIRST RUN 2026-10-29: the names came out doubled (provider_dim fills both name columns; the docket search ran on the doubles and found nothing), each --screen overwrote the report, and the DME screen found no mailbox above $1M (every row was the shared-building rule on office parks). Fixed: one name, one report per run, suite-level sharing only. Rerun lines 1 to 3. Close when the round is read. (added 2026-10-29)

```bat
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/mfd_screens && git pull --ff-only 2>&1 | tail -1 && git log --oneline -1"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/mfd_screens && source ~/medicaid-fraud-detection/.venv/bin/activate && set -a && source ~/.medicaid_env && set +a && export MEDICAID_DATA_ROOT=/data/medicaid && python -m src.model_a.theory_screens --spending /data/medicaid/processed/spending_fact.parquet --provider-dim /data/medicaid/processed/provider_dim.parquet --verdicts /data/medicaid/model_a/pursuit_verdicts.csv --out /data/medicaid/enforcement/theory_screens3 --screen ambulance_repetitive,nemt_fleet,dme_mailbox; grep -n -B 3 -A 24 '### Candidates' /data/medicaid/enforcement/theory_screens3/THEORY_SCREENS.md | grep -E 'Peak lines|### Candidates|^[0-9]+-\| [0-9]+ \|' | head -90"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/mfd_screens && source ~/medicaid-fraud-detection/.venv/bin/activate && set -a && source ~/.medicaid_env && set +a && export MEDICAID_DATA_ROOT=/data/medicaid && head -20 /data/medicaid/enforcement/theory_screens3/names_ambulance_repetitive.txt > /data/medicaid/enforcement/theory_screens3/top20_ambulance.txt && head -20 /data/medicaid/enforcement/theory_screens3/names_nemt_fleet.txt > /data/medicaid/enforcement/theory_screens3/top20_nemt.txt && head -20 /data/medicaid/enforcement/theory_screens3/names_dme_mailbox.txt > /data/medicaid/enforcement/theory_screens3/top20_dme.txt && python -m src.enforcement.first_to_file --since 2015-01-01 --names-file /data/medicaid/enforcement/theory_screens3/top20_ambulance.txt --out /data/medicaid/enforcement/first_to_file_ambulance && python -m src.enforcement.first_to_file --since 2015-01-01 --names-file /data/medicaid/enforcement/theory_screens3/top20_nemt.txt --out /data/medicaid/enforcement/first_to_file_nemt && python -m src.enforcement.first_to_file --since 2015-01-01 --names-file /data/medicaid/enforcement/theory_screens3/top20_dme.txt --out /data/medicaid/enforcement/first_to_file_dme; grep -h -B 1 -A 3 '^| ' /data/medicaid/enforcement/first_to_file_ambulance/FIRST_TO_FILE.md /data/medicaid/enforcement/first_to_file_nemt/FIRST_TO_FILE.md /data/medicaid/enforcement/first_to_file_dme/FIRST_TO_FILE.md | head -120; grep -h '^Names searched' /data/medicaid/enforcement/first_to_file_*/FIRST_TO_FILE.md"
python -m src.reminders --done-task track3_sweep
```

- [ ] **siu_packets_handoff** — SEVEN PACKETS SENT 2026-10-29 (chat files; save under <data_root>\enforcement\pi_reports\<org>\, work product, never git): Bergen Adult Day Care (the one-day card: a weekday count at the door at 139 E Central Blvd, Palisades Park, arrivals and departures across the whole day, the posted hours, the vehicles; sixty at one time is the licence; do it NOW, it does not wait for the records request), American Forensic (NY bars lab reflex; Cordant CIA covered it 2020-2025), ERSO, Lab24, Appalachian, Nepenthe (Oregon D23 covers definitive only on a discrepant screen), Emerald (Oregon D23). The four earlier packets (TrueTox, Genesis, National Labs, Accu (the Sims complaint is a research read)) complete the hand-over set of eleven. Hand the set to Sage / the SIU with the roles tables; the former-staff names stay in the research CSVs under enforcement\. BERGEN GOES TO SAGE (operator 2026-10-29: Sage is in the area): a NEW case, never in packet v11, never visited; Brad's approval for one investigator day is slide 5 + decision row 4 of the model-evolution deck (v3). Close when Brad has approved and Sage has the Bergen packet. BRAD 2026-10-30: GO on the four New Jersey day-care counts (Bergen first, then Totowa, Forever Young, Golden: one day each, the licence file arrives in parallel); GO on the recommended labs with the written proof plan (PI_PLAN_2026-10-30.md, sent in chat: interviews by role, no visits; National Labs and Genesis first, TrueTox after counsel, the Oregon pair, then the rest; Accu after the research read of the 2021 Sims complaint (task sims_complaint_read)); transport = one more research round, then a yes/no per company (task nemt_fleet_round2). The Bergen packet and the plan go to Sage; the lab packets go to the investigators doing interviews. OPERATOR 2026-10-30, THE SPLIT: Sage takes the NJ adult day cares and any transport investigation local to NJ/NY (none of the three transport candidates is: Medichair CA, Texass TX, Big Wheel NC); every other case goes to a NEW first-time SIU group. HANDOFF RULE (standing, every packet for Brad, Sage or the new SIU from now on): Claude writes a detail-heavy HANDOFF_<case>.md (packet + every data field + every research answer verbatim + court records + the plan part + the rules) and ChatGPT distills it into the final short packets (one page for Brad, a two-to-three page field packet with an investigator-only appendix) under CHATGPT_INSTRUCTIONS.md; more is better for the distiller. The first set (19 handoffs + the instructions, handoff_2026-10-30.zip) went in chat 2026-10-30: file under <data_root>\enforcement\handoffs\2026-10-30\ (work product, never git), paste the instructions file first and one HANDOFF per ChatGPT thread; the distilled <case>_BRAD.md / <case>_FIELD.md come back to the same folder. RULE (operator 2026-10-30): counsel never gates a read; a complaint, an audit or an order is pulled by the research agent (PACER if no free copy) or read by us, and the public-disclosure / first-to-file read is derived from the text. 2026-10-30 LATER: the operator sent ALL 19 handoffs + instructions to ChatGPT before the transport round 2 (Medichair / Texass / Big Wheel) and the Sims / TrueTox reads returned. When those come back, Claude rebuilds those FIVE handoffs and sends a second zip with an ADDENDUM instruction file ('replace these five; the other fourteen stand; the verdict line and section 3 changed; re-distill only these'). Ohio (Wesleyan + the homes) becomes its own NEW handoff after the fifth answer. (added 2026-10-29)
- [ ] **nj_amdc_opra** — FILED 2026-10-29 (two Government Records Requests on the NJ DOH portal, confirmation numbers with the operator): Request 1 to Certificate of Need and Licensing (Medical Day Care) for the four licence files (project proposals, declared shifts and hours, capacity and hours amendments 2018 to date, expansion applications); Request 2 to Health Facility Survey and Field Operations (Adult Day Health Care Facilities) for every survey, inspection, expansion survey, complaint investigation and plan of correction 2018 to date, with census by shift. The statutory response window is seven business days (due about 2026-11-09); an extension letter is common, a denial goes to the Government Records Council (866-850-0511, grc@dca.state.nj.us). When the files arrive: save them under <data_root>\enforcement\pi_reports\nj_amdc\ (work product, never git) and upload the licence pages for the read; the approved shift schedule decides Totowa, Forever Young and Golden; Bergen is over on any schedule. The verdict lines below record the current read if not yet run. BRAD 2026-10-30: go on all four; the three watch rows become pursue (the verdict line below supersedes the earlier watch lines; the licence file still reads the approved shifts). (added 2026-10-29)

```bat
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && export MEDICAID_DATA_ROOT=/data/medicaid && python -m src.model_a.pursuit_verdicts --add org:name:2ND_HOME_TOTOWA pursue 'Brad 2026-10-30: count at the door now; 152 a calendar day on a licence of 104 (about 214 an open day vs 208 on two shifts); the licence file reads the approved shifts' --name '2ND HOME TOTOWA LLC' --run nj_amdc && python -m src.model_a.pursuit_verdicts --add org:name:FOREVER_YOUNG_MEDICAL_DAY_CARE pursue 'Brad 2026-10-30: count at the door now; 266 a calendar day on 200 (about 367 an open day vs 400 on two shifts); the licence file reads the approved shifts' --name 'FOREVER YOUNG MEDICAL DAY CARE' --run nj_amdc && python -m src.model_a.pursuit_verdicts --add org:name:GOLDEN_ADULT_DAY_CARE pursue 'Brad 2026-10-30: count at the door now; 264 a calendar day on 220 (about 372 an open day vs 440 on two shifts; the March 2025 survey recorded two shifts); the licence file reads the approved shifts' --name 'GOLDEN ADULT DAY CARE INC' --run nj_amdc"
python -m src.reminders --done-task nj_amdc_opra
```

- [ ] **capacity_rosters_next_states** — CA + NY LOADED 2026-10-30 (19,561 licensed sites from 5 files, CA / NJ / NY; 26,155 NPIs matched, 23,531 by address). THE READ of the per-diem sweep with them: no new door-count card above the case bar. The four rows that surfaced were California and small: a county alcohol-and-drug programme (government; now set aside by the widened rule), Right Choice Senior Living ($2.3M, a six-bed licence read 2.7x: the assisted-living-waiver shape where one number bills several six-bed homes, so the loader now sums a licensee's homes = basis address+name), Reimagine Network ($14M adult day, 1.28x on calendar days but 0.94 on two shifts and the peak was January 2021 under COVID remote-service rules: watch at most), Promise Assisted Living (exactly 1.00). New York's adult-home roster produced no candidate; the $5.9B NY no-roster rows are ADULT DAY HEALTH CARE (261Q), which needs the DOH ADHC list, not the ACF directory. NEXT: after the laptop pull, rerun the loader + sweep lines (the two fixes change the CA capacities); then the next rosters, one per session, in dollar order of the no-roster table: MO (DMH residential / day habilitation: Easter Seals Midwest $344M, Supported Living $235M, Easy Living, Emmaus), TX (HHSC HCS / ICF rosters: Consumer Directed Services $306M, Caregivers Home Health $168M), AZ (DDD / DHS licensed homes: Tungland $294M), ME (Granite Bay $281M), VA (DBHDS licensed residential: Wall Residences $274M), NH, TN; and the NY ADHC list. RULE learned: a residential-habilitation operator's capacity is the SUM of its licensed homes, so a state roster must carry the licensee or operator name, not just the home's name, or the join lands on one house. (added 2026-10-29)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection && git pull --ff-only
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "bash ~/medicaid-fraud-detection/ops/vm/sync_data.sh down-preclean 2>&1 | tail -3 && ls -la /data/medicaid/preclean/state_capacity/"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && export MEDICAID_DATA_ROOT=/data/medicaid && git pull --ff-only | tail -1 && python -m src.ingest_cms.licensed_capacity --rosters /data/medicaid/preclean/state_capacity --provider-dim /data/medicaid/processed/provider_dim.parquet --out /data/medicaid/processed/licensed_capacity.parquet 2>&1 | tail -15"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && export MEDICAID_DATA_ROOT=/data/medicaid && python -m src.model_a.theory_screens --spending /data/medicaid/processed/spending_fact.parquet --provider-dim /data/medicaid/processed/provider_dim.parquet --verdicts /data/medicaid/model_a/pursuit_verdicts.csv --screen perdiem_occupancy --out /data/medicaid/enforcement/theory_screens && grep -n -A 40 '### Candidates' /data/medicaid/enforcement/theory_screens/THEORY_SCREENS.md | head -60"
python -m src.reminders --done-task capacity_rosters_next_states
```

- [ ] **omni_ruling_watch** — Re-check the ruling on the government's motion to dismiss the relator's complaint in United States ex rel. Omni Healthcare v. Thermo Fisher Scientific et al., N.D. Tex. 3:22-cv-01710 (docket 71053664). Accu Reference and Wellspring USA (Genesis) are named defendants on a urine-PCR theory (12 pct / 4 pct of their Medicaid dollars); HealthTrackRx under its old name too. While the case is pending a later case on the same facts is barred; our theories are different conduct and stay pursue. If the motion is granted, note the date in the verdicts file reasons. Monthly until ruled. (added 2026-10-29)

```bat
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "set -a && source ~/.medicaid_env && set +a && curl -s -H 'Authorization: Token '$COURTLISTENER_TOKEN 'https://www.courtlistener.com/api/rest/v4/docket-entries/?docket=71053664&order_by=-date_filed' | python3 -m json.tool | grep -E 'date_filed|description' | head -12"
python -m src.reminders --done-task omni_ruling_watch
```

- [ ] **v21_run** — FINISHED 2026-10-23 00:51 UTC; packet read; verdict HOLD on Jaccard only (read-out in docs/). Close. (added 2026-10-22)

```bat
python -m src.reminders --done-task v21_run
```

- [ ] **first_to_file_check** — FIRST-TO-FILE CHECK (bundle 402, src/enforcement/first_to_file.py): before money goes into a lead, the public dockets are searched for the party through the free CourtListener API (RECAP / PACER metadata, any nature of suit; FCA-shaped rows flagged). A sealed qui tam is invisible to every public search; a clean result rules out public cases, settlements and government actions only. Names: the home care physician and his practice + the five agencies, the podiatrist + his three companies, and the ten v26 targets. Free; a minute per name. COURTLISTENER_TOKEN in ~/.medicaid_env raises the rate limit (optional). Paste FIRST_TO_FILE.md's head. RULE (2026-10-28, the Americare lesson): the docket search on a CHHA's name missed the December 2025 settlement with its SISTER agency (Americare, Inc.); every names list carries the sibling entities, the d/b/a names and the owners' other companies (from the Medicare ownership file / PECOS), not only the billing entity. (added 2026-10-26)

```bat
REM after merge 402 + the VM pull:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && python -m src.enforcement.first_to_file --names 'Andes, Scott;Vantage Medical Associates;Americare Certified Special Services;Extended Nursing Personnel;Prime Home Health Services;Excellent Home Care Services;HCS Certified Home Care;Morrison, Norris;High Desert Limb Salvage Institute;Foot and Wound Care of Indio;Norris Morrison DPM;HealthTrackRx;Stavros Center for Independent Living;Ideal Option;Unilab Corporation;Diagnostic Laboratory of Oklahoma;Laboratory Services Cooperative;Simple Laboratories;Mini Pharmacy Enterprises;Kennedy Donovan Center;TrueTox Laboratories' --since 2015-01-01 --out /data/medicaid/enforcement/first_to_file && head -n 80 /data/medicaid/enforcement/first_to_file/FIRST_TO_FILE.md"
python -m src.reminders --done-task first_to_file_check
```

- [ ] **attending_concentration_run** — RUN THE ATTENDING-CONCENTRATION REPORT ON THE VM (bundle 400, src/model_a/attending_concentration.py): the physician-of-record shape the matrix cannot see (an individual NPI in the servicing field of other organizations' claims), found by hand 2026-10-26: one Brooklyn internist as clinician of record on $97M of five agencies' home aide claims from mid-2023, first in the nation on the aide codes. The report ranks every such individual nationally with the ramp, the billers, the home-care share, the new-patient visits per working day and three flags. Current-day, no model, a few minutes of DuckDB. Read the top 30 and tell Claude which rows are new; each flagged row is a desk-research card before any PI time. (added 2026-10-26)

```bat
REM after merge 400 + the VM pull:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && python -m src.model_a.attending_concentration --spending /data/medicaid/processed/spending_fact.parquet --provider-dim /data/medicaid/processed/provider_dim.parquet --min-paid 2000000 --top 60 --out /data/medicaid/enforcement/attending_concentration && head -n 80 /data/medicaid/enforcement/attending_concentration/ATTENDING_CONCENTRATION.md"
python -m src.reminders --done-task attending_concentration_run
```

- [ ] **repo_migration_trey_github** — MOVE THE REPO TO TREY'S OWN GITHUB (Travis 2026-10-26: nothing routes through the Equitable-connected GitHub; the Claude GitHub App is not installed on travishub09, which is why every push from the Claude session returns 403 and bundles are the delivery). Steps: (1) create an EMPTY private repo medicaid-fraud-detection under your own GitHub account (no README); (2) paste block A on the laptop: it adds your repo as a second remote, pushes every branch and tag, then renames the remotes so YOUR repo is origin and Travis's is 'travis' (merge_push.bat pushes to origin and best-effort to every other remote, so both stay in step during the overlap); (3) install the Claude GitHub App on your account (github.com/apps/claude, choose the new repo), then at claude.ai/code reconnect GitHub and start a new session on the new repo: from then on Claude pushes directly and bundles end; (4) on the VM, paste block B once (a deploy key: the public key it prints goes into the new repo's Settings > Deploy keys, read-only), then block C switches the VM's remote to ssh. Replace <USERNAME> with your GitHub username in every line. The CLAUDE.md branch rule stays claude/happy-mccarthy-3skrn3 until the new session names its own branch. (added 2026-10-26)

```bat
REM A. laptop (replace <USERNAME>):
cd /d C:\Users\treyr\medicaid-fraud-detection
git remote add trey https://github.com/<USERNAME>/medicaid-fraud-detection.git && git push trey --all && git push trey --tags && git remote rename origin travis && git remote rename trey origin && git remote -v
REM B. VM, once (prints the public key to paste into the new repo's Deploy keys, read-only):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "test -f ~/.ssh/repo_key || ssh-keygen -t ed25519 -f ~/.ssh/repo_key -N '' -C medicaid-vm; printf 'Host github.com\n  IdentityFile ~/.ssh/repo_key\n  IdentitiesOnly yes\n' > ~/.ssh/config; chmod 600 ~/.ssh/config; cat ~/.ssh/repo_key.pub"
REM C. VM, after the deploy key is saved on GitHub (replace <USERNAME>):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git remote set-url origin git@github.com:<USERNAME>/medicaid-fraud-detection.git && ssh -o StrictHostKeyChecking=accept-new -T git@github.com; git fetch origin && git status -sb | head -2"
python -m src.reminders --done-task repo_migration_trey_github
```

- [ ] **freeze_v25** — FREEZE v25 as the fallback (operator 2026-10-26: 'freeze this version and come back to it if v26/v27 do not get there'). Three layers: (1) CODE: tag freeze-v25-2026-10-25 + branch freeze/v25-2026-10-25 at 3898faa, carried by bundle 393 and pushed by merge_push.bat (return with `git checkout freeze-v25-2026-10-25`); (2) RUN ARTIFACTS: the VM folder /data/medicaid/model_a/frozen_2021-12_v25 (matrix, lists, dossier) stays on the VM disk; `sync_data.sh up` copies the reports + registry to OneDrive; keep run_packet_v25.tar.gz in Downloads; (3) THE POINTER: `run_registry --pin-baseline` writes /data/medicaid/model_a/BASELINE.json so every output CLI accepts --run-dir @baseline; a HOLD run can be the baseline, never the champion. ORDER: merge 393 (the tag rides along), pull on the VM, pin, up-sync. The pin moves only on the operator's word. PIN DONE IN FACT (console 2026-10-26 00:12: BASELINE pinned frozen_2021-12_v25, commit 3898faa); the up-sync line not yet seen; close with the done line when both have run. (added 2026-10-26)

```bat
REM 1. laptop merge (carries the freeze tag), then the VM pull:
cd C:\Users\treyr\medicaid-fraud-detection && ops\laptop\merge_push.bat 393
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git pull --ff-only && git fetch origin 'refs/tags/freeze-*:refs/tags/freeze-*' && git tag | grep freeze"
REM 2. pin v25 as the baseline on the VM (the registry lives there):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && python -m src.model_a.run_registry --data-root /data/medicaid --pin-baseline frozen_2021-12_v25 --freeze-tag freeze-v25-2026-10-25 --reason 'first honest, stable, 19-of-20 list; the fallback until v26/v27 beat it on an independent ruler' && python -m src.model_a.run_registry --data-root /data/medicaid --champion"
REM 3. copy the v25 reports + registry to OneDrive (tmux; a few minutes):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && tmux new -d -s up25 'bash ops/vm/sync_data.sh up frozen_2021-12_v25 > /data/medicaid/sync_up_v25.log 2>&1'"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tmux ls; tail -3 /data/medicaid/sync_up_v25.log"
python -m src.reminders --done-task freeze_v25
```

- [ ] **owners_2023_editions** — DOWNLOADED BY JOSH (2026-10-27, owners_2023.zip, six files: hospital 2023-12-01 snapshot, SNF 2023-12-01, HHA 2023-10-02, hospice 2023-10-02, FQHC 2023-11-01, RHC 2023-11-01; no Clinical Laboratory All Owners dataset exists on data.cms.gov, so no lab file, which is correct). The picker now reads his short names (bundle 423 maps snf/hha/hospice/hospital/fqhc/rhc onto the CMS dataset tokens), so the files go in UNCHANGED under preclean\owners_prior\. Line 1 extracts the zip there on the laptop (Windows tar); OneDrive syncs; line 2 pulls preclean to the VM and lists the folder; line 3 is the proof-guarded close. Then v27 = the 2023-12 export again with --rebuild-graph (ownership_turnover and the owner edges at the second freeze, plus the hospice-edition fix). (added 2026-10-26)

```bat
cd /d C:\Users\treyr\OneDrive\Desktop\data\preclean && (if not exist owners_prior mkdir owners_prior) && tar -xf C:\Users\treyr\Downloads\owners_2023.zip -C C:\Users\treyr\OneDrive\Desktop\data\preclean\owners_prior --strip-components=1 && dir C:\Users\treyr\OneDrive\Desktop\data\preclean\owners_prior
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "bash ~/medicaid-fraud-detection/ops/vm/sync_data.sh down-preclean 2>&1 | tail -3 && ls -la /data/medicaid/preclean/owners_prior/ | tail -12"
python -m src.reminders --done owners_2023_editions
```

- [ ] **v25_run** — v25 = the RE-GRADE of v21's matrix WITHOUT class priors (bundle 391): the v24 arms showed honest priors add nothing (the nested-prior refit and the no-priors refit read the same operator book 0/1/3/4; no-priors has the better NPI grain), so the rule written before v24 applies. CLASS_PRIORS='' overrides the source manifest on every ship and arm line; the nested priors are graded as ab_class_priors/ (CLASS_PRIORS_AB default taxonomy,entity); PRIOR_ARMS='' (no encoder arms: nothing to encode); prune and broadcast arms stay. Pre-registered under the twenty with priors = none and the new score_scale >= 0.1. About nine hours (v23 and v24 each took nine). A pass = the first PROMOTE candidate; the dossier dispatch is then the operator's call. ORDER: merge 391, pull on the VM (idle), pre-register, launch under tmux v25, status, the tail-30 and the packet at [v25] DONE. (added 2026-10-25)

```bat
REM 1. laptop merge, then the VM pull:
cd C:\Users\treyr\medicaid-fraud-detection && ops\laptop\merge_push.bat 391
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git pull --ff-only && git log --oneline -1"
REM 2. pre-register:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && python -m src.model_a.run_registry --data-root /data/medicaid --preregister --tag frozen_2021-12_v25 --expected 'v21 matrix re-scored WITHOUT class priors (the v24 arms: honest priors add nothing measurable); the pursuit book holds 3 / 4 / 7 at 50 / 100 / 250 with the NPI grain above 3 and a top-50 Jaccard at or above 0.6 (v19, the last no-prior run, read 0.618)' --goal 'v25 = the simpler model on the same matrix: no class priors shipped, nested priors graded as ab_class_priors, prune and broadcast arms; no export, same label and ruler; a pass is the first PROMOTE candidate' --check 'large_hits50 >= 2' 'large_prec50_lo > large_base50' 'hits100 >= 4' 'hits250 >= 6' 'lead_ge_gap >= 0.5' 'inst_share50 <= 0.4' 'top50_jaccard >= 0.6' 'npi_lift1pct >= 3' 'score_scale >= 0.1' 'eval_policy = fca' 'horizon = 36' 'frozen = True' 'seeds >= 10' 'groups = case_cluster' 'broadcast = weight' 'train = hard' 'priors = none' 'prune = none' 'prior_inner >= 5'"
REM 3. launch (tmux v25):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && tmux new -d -s v25 'cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && CLASS_PRIORS= PRIOR_ARMS= PRIOR_INNER=5 PRIOR_MIN_N=300 PRIOR_WEIGHTED=1 PURSUIT_ORDER=pmax_basis50 TRAIN_TIER=hard bash ops/vm/regrade_from.sh v25 v21 2021-12 > /data/medicaid/model_a/regrade_v25.log 2>&1'"
REM 4. status any time:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tmux ls; grep -c 'step ' /data/medicaid/model_a/regrade_v25.log; grep 'step ' /data/medicaid/model_a/regrade_v25.log | tail -1; tail -3 /data/medicaid/model_a/regrade_v25.log"
REM 5. at [v25] DONE: the tail, the packet, the scp, then upload run_packet_v25.tar.gz to the chat:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tail -30 /data/medicaid/model_a/regrade_v25.log"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd /data/medicaid/model_a/frozen_2021-12_v25 && tar -czf /home/ubuntu/run_packet_v25.tar.gz *.md *.json *.csv $(ls -d ab_* 2>/dev/null)"
scp -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ConnectTimeout=15 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139:/home/ubuntu/run_packet_v25.tar.gz C:\Users\treyr\Downloads\
```

- [ ] **v24_run** — v24 = the RE-GRADE of v21's matrix under bundle 390: the NESTED class-prior encoder (the v23 read-out found that v21..v23 encoded training rows leave-one-out and LightGBM read the prior column as the label; every prior result so far is contaminated). Ship PRIOR_INNER=5 PRIOR_MIN_N=300 PRIOR_WEIGHTED=1, order pmax_basis50, train hard; arms ab_prior_loo_v22/ (the leaking v22 encoder on the same matrix) and ab_prior_nested_unweighted/, plus the usual broadcast / prune / no_priors arms, every arm under the shipped flags. The grade that matters: the pursuit book against the no-priors arm (v23: 0/1/3/4 on the operator book), NPI lift above 3, a Jaccard that is not fold noise. About 3 to 4 hours. ORDER: merge 390 on the laptop (389 first if not yet), pull on the VM (idle), pre-register (twenty checks), launch under tmux v24, status, paste the last 30 lines at [v24] DONE, then the packet. If nested priors do not beat no priors on the operator book, v25 ships without class priors. (added 2026-10-24)

```bat
REM 1. laptop merge (389 then 390 if 389 is not merged yet), then the VM pull:
cd C:\Users\treyr\medicaid-fraud-detection && ops\laptop\merge_push.bat 390
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git pull --ff-only && git log --oneline -1"
REM 2. pre-register (the v23 seventeen + the three encoder checks):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && python -m src.model_a.run_registry --data-root /data/medicaid --preregister --tag frozen_2021-12_v24 --expected 'v21 matrix re-scored with the NESTED class-prior encoder (inner five-fold for training rows; the v21..v23 leave-one-out encoder leaked the label): the pursuit book beats the no-priors arm at 50/100/250, the NPI grain holds above 3, the head is no longer fold noise' --goal 'v24 = the leak fixed as policy (prior_inner_folds 5, floor 300, label-weighted); arms = the v22 encoder on the same matrix and nested-unweighted; every arm under the shipped flags; no export, same label and ruler' --check 'large_hits50 >= 2' 'large_prec50_lo > large_base50' 'hits100 >= 4' 'hits250 >= 6' 'lead_ge_gap >= 0.5' 'inst_share50 <= 0.4' 'top50_jaccard >= 0.6' 'npi_lift1pct >= 3' 'eval_policy = fca' 'horizon = 36' 'frozen = True' 'seeds >= 10' 'groups = case_cluster' 'broadcast = weight' 'train = hard' 'priors = taxonomy,entity' 'prune = none' 'prior_inner >= 5' 'prior_floor = 300' 'prior_weighted = True'"
REM 3. launch (tmux v24):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && tmux new -d -s v24 'cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && PRIOR_INNER=5 PRIOR_MIN_N=300 PRIOR_WEIGHTED=1 PURSUIT_ORDER=pmax_basis50 TRAIN_TIER=hard bash ops/vm/regrade_from.sh v24 v21 2021-12 > /data/medicaid/model_a/regrade_v24.log 2>&1'"
REM 4. status any time:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tmux ls; grep -c 'step ' /data/medicaid/model_a/regrade_v24.log; grep 'step ' /data/medicaid/model_a/regrade_v24.log | tail -1; tail -3 /data/medicaid/model_a/regrade_v24.log"
REM 5. at [v24] DONE: the tail, the packet, the scp, then upload run_packet_v24.tar.gz to the chat:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tail -30 /data/medicaid/model_a/regrade_v24.log"
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd /data/medicaid/model_a/frozen_2021-12_v24 && tar -czf /home/ubuntu/run_packet_v24.tar.gz *.md *.json *.csv $(ls -d ab_* 2>/dev/null)"
scp -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ConnectTimeout=15 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139:/home/ubuntu/run_packet_v24.tar.gz C:\Users\treyr\Downloads\
```

- [ ] **v23_run** — v23 = the RE-GRADE of v21's matrix under bundle 388 (the prior encoder as policy): the floor-only encoder SHIPPED (PRIOR_MIN_N=300 PRIOR_WEIGHTED=0), the v21 (0/0) and weighted (300/1) encoders as refit arms ab_prior_v21/ and ab_prior_weighted/, each graded on the main list AND the operator book under pmax_basis50 (acc_pursuit_*). About 3 to 4 hours (two extra refits). ORDER: merge 388 on the laptop, pull on the VM, pre-register, launch under tmux (OVERWRITE=1 is not needed: v23 is a new folder), status, paste the last 30 lines at [v23] DONE. The data scan and capacity check wait until v23 is DONE. BUNDLE 389 (built while v23 ran, NOT on the VM until DONE): every refit arm and graded book now inherits the shipped encoder ($PRIOR_FLAGS), and the shipped list records its effective policy for the registry. READ v23 WITH THIS IN MIND: its broadcast / prune / charged arms refit under the WEIGHTED encoder (the manifest default), not the floor-only ship, so only ab_prior_v21 / ab_prior_weighted vs the shipped grade are a clean comparison; its registry row reads the encoder as 0 / off (v21's copied manifest), while the ship was 300 / 0 from the launch line. After DONE and the packet: merge 389 on the laptop, pull on the VM. (added 2026-10-24)

```bat
REM 1. laptop merge, then the VM pull (the VM is idle):
cd C:\Users\treyr\medicaid-fraud-detection && ops\laptop\merge_push.bat 388
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git pull --ff-only && git log --oneline -1"
REM 2. pre-register (the v22 seventeen):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && python -m src.model_a.run_registry --data-root /data/medicaid --preregister --tag frozen_2021-12_v23 --expected 'v21 matrix re-scored with the floor-only prior encoder (300-row class floor, positives by row): the pursuit head returns toward v21 (3/3/4/8) while the NPI grain holds above 3; the v21 and weighted encoders graded as arms on the main list AND the operator book' --goal 'v23 = the three-encoder A/B from the v22 read-out: floor-only shipped, v21 and weighted as refit arms; every arm grades the operator book (acc_pursuit_*); no export, same label and ruler' --check 'large_hits50 >= 2' 'large_prec50_lo > large_base50' 'hits100 >= 4' 'hits250 >= 6' 'lead_ge_gap >= 0.5' 'inst_share50 <= 0.4' 'top50_jaccard >= 0.6' 'npi_lift1pct >= 3' 'eval_policy = fca' 'horizon = 36' 'frozen = True' 'seeds >= 10' 'groups = case_cluster' 'broadcast = weight' 'train = hard' 'priors = taxonomy,entity' 'prune = none'"
REM 3. launch (tmux v23; floor-only shipped):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && tmux new -d -s v23 'cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && PRIOR_MIN_N=300 PRIOR_WEIGHTED=0 PURSUIT_ORDER=pmax_basis50 TRAIN_TIER=hard bash ops/vm/regrade_from.sh v23 v21 2021-12 > /data/medicaid/model_a/regrade_v23.log 2>&1'"
REM 4. status any time:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tmux ls; grep -c 'step ' /data/medicaid/model_a/regrade_v23.log; tail -4 /data/medicaid/model_a/regrade_v23.log"
REM 5. at [v23] DONE: the packet (every arm from inside the run folder), copy down, upload:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd /data/medicaid/model_a/frozen_2021-12_v23 && tar -czf /home/ubuntu/run_packet_v23.tar.gz *.md *.json *.csv $(ls -d ab_* 2>/dev/null) && ls -la /home/ubuntu/run_packet_v23.tar.gz"
scp -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139:/home/ubuntu/run_packet_v23.tar.gz C:\Users\treyr\Downloads\
python -m src.reminders --done-task v23_run
REM 6. after [v23] DONE and the packet is uploaded (never while it runs): merge 389, then pull on the VM
cd C:\Users\treyr\medicaid-fraud-detection && ops\laptop\merge_push.bat 389
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git pull --ff-only && git log --oneline -1"
```

- [ ] **v22_run** — FINISHED 2026-10-24 06:07 UTC; packet read; HOLD (read-out in docs/). Close. (added 2026-10-23)

```bat
python -m src.reminders --done-task v22_run
```

- [ ] **wesleyan_ledger_check** — The Wesleyan Village (CCN 365162) resident's own transaction ledger (nine photos, read 2026-10-22; the read-out + the 40 transcribed therapy lines are in the bundle-376 chat files, saved under data\enforcement\pi_reports\wesleyan\, NEVER git; no name anywhere): care level HIGH billed every day Jan 2025 to Sep 2026 against her 'not high care since end of 2023'; PT Jan 2025 (two Sundays) and OT May + Sep 2025 (eval then fifteen straight weekdays) she says never happened, each $50 line offset to zero (therapy is inside the per diem, so the damages theory is the MDS / case-mix level, not the lines); $30/day private-room incentive to verify; cafeteria charges to a Medicaid resident. ORDER: (1) download the 2025 non-nurse PBJ quarters (reminder pbj_nonnurse_2025) and run the therapy-hours check below; (2) counsel requests her records (therapy notes + MDS Section O / GG 2024-2026, care plan, the plan's level-of-care authorizations, UHC claims history); (3) send the Ohio cost-report records request (task ohio_medicaid_cost_report_request, same facility); (4) print the Care Compare citation rows for 365162 from preclean/facility NH_HealthCitations (assessment accuracy F636 / F641). Close when the PBJ report is in the PI file and counsel has the memo. RESULT 2026-10-22: PBJ check run on the 2025 Q1-Q3 non-nurse files: 0 of the 40 disputed sessions fall on a day the home reported zero hours of that discipline (all 40 on days with OT / PT hours facility-wide, the two January Sundays included); the Care Compare rows for 365162 are in CARE_COMPARE_365162.md. The public record neither contradicts nor supports her; the case rests on her therapy notes + MDS (counsel's request) and on the Ohio cost-report request. Steps 1 and 4 done; 2 and 3 open. STEP 3 DONE 2026-10-30: the Ohio cost reports arrived and were read (task ohio_cost_report_followups carries what they show and the records questions). (added 2026-10-22)

```bat
REM the PBJ check and the Care Compare rows are done (2026-10-22). Open:
REM 2. counsel requests her records (therapy notes with the therapist's name for Jan / Apr-May / Sep 2025, every MDS 2024-2026 with Section O + GG, the care plan, the plan's level-of-care authorizations, UHC claims history)
REM 3. send the Ohio cost-report records request (task ohio_medicaid_cost_report_request)
REM the facility-level summary (no resident data) to paste to Claude:
powershell -c "Get-Content 'C:\Users\treyr\OneDrive\Desktop\data\enforcement\pi_reports\wesleyan\PBJ_THERAPY_365162.md' -Head 12"
python -m src.reminders --done-task wesleyan_ledger_check
```

- [ ] **lanes_dispatch_2** — LOAD 3 DONE 2026-10-22 (free): outcome_refresh 60 completed / 69 ERRORED at the service / 141 rows loaded, 39 charged rows cleared, 503 charged rows still pending; cia 77 completed (1 new row, 891 quarantined, 123 defendant_not_on_page); names 35 completed / 26 errored (0 new); defendant_research 21 folded. OPEN: (a) the 69 + 26 errored tasks (error_detail in the registry; a re-dispatch costs credits: operator's go), (b) the 42 unsent outcome prompts (credits: operator's go). Both wait until after v20 prints DONE (the load rewrites the case DB the run reads). (added 2026-10-20)

```bat
REM the 42 unsent outcome prompts (credits; only on the operator's go):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && source ~/.medicaid_env && python -u -m src.model_a.manus_lanes --data-root /data/medicaid --lanes outcome_refresh --dispatch --max-new 50 --total-budget 50"
python -m src.reminders --done-task lanes_dispatch_2
```

- [ ] **brad_ri_package** — Send Brad the RI dossier v4.1 + honest-grade email (ready; your call) (added 2026-08-02)
- [ ] **state_letter:_every_named** — State letters: nine of ten named deactivated-NPI billers are live organizations paid under an old NPI (MI: Mariners Inn, Meridian; FL: Kidz Medical, CCS Medical, Memorial Hospital of Tampa psych unit, Miami Personal Management; AZ: Creative Innervisions; CO: Adeo; RI: Kent County Memorial Hospital). Draft the program-integrity letters per state once the last name (1831471960) lands; not a PI matter (added 2026-09-16)
- [ ] **pi_packet_v9_feedback** — PI Packet v11 (final for this round, 2026-09-16; v10 superseded by the limitations clock: paid_after check over Tiers 1-2, Tier 1 reordered by live dollars, Infinity/Jacoby/Perspectives to desk-first) sent to Trey: 9 Tier 1 + 7 Tier 2 + 2 Tier 3 + 6 national + 1 desk-first, 19 do-not-send rows, all 50 cards reviewed. OPEN until the PIs' feedback comes back and is folded into v10 (or Trey confirms no changes). Next capacity pass to-dos recorded in the packet: minutes map H2035=60; find the pre-2020 Hope Pediatrics NPI in the spending fact; Special Homes billed residential days vs 76 licensed places. Consensus read 2026-09-16: five-run national book is hospitals/health systems/national chains (do-not-send class); it adds no PI card; ACT HOME HEALTH SERVICES, INC (org:pac:3971547985, $18M/yr, hot 3/5, rapid_ramp) is the one new non-institutional name, held for a desk check next round (state unknown; no state column in the CSV). Successor-NPI check ran 2026-09-16: Renew Resources, Perspectives Counseling Center and Velocity have no claims through 2024-12; Perspectives is lowest priority, Velocity waits for the next data refresh. FIRST PI REPORT RECEIVED 2026-10-12 (Sage Intelligence case 260136, dated 2026-10-07, to Brad; attorney work product, keep it OUT of git and OUT of OneDrive-shared folders: save under <data_root>\enforcement\pi_reports\): Part I surveillance of the three Chinatown cards (Lau 2 Mott St, Chang 217 Grand St, Xie 139 Centre St): Lau = a real solo pediatric office with two receptionists, two child patients in 75 minutes, a Brooklyn second site, and two NPs plus an FNP tied to the address (NP services billed under the physician NPI is the innocent explanation to rule out first, and the scheme if the rate was the physician's); Chang = busy ophthalmology office open 7 days, an optometrist and a second physician at the address; Xie = neurology office EMPTY on both visits (85 minutes), a PA and an NP tied to it at Brooklyn addresses. Part II: former employees with tenure windows identified for about 20 packet practices; next phase = door knocks and interviews. OUR NEXT STEP: send Sage the WHEN sheet (pi_facts --deep now writes PI_FACTS_*_WINDOWS.md: active years / ramp month / peak year per practice) so interviews go to formers whose tenure overlaps the active window; and the three surveillance facts go into the Lau / Chang / Xie cards as field observations. 2026-10-13: Special Homes of NJ RESOLVED by the capacity ratio (74 residents per calendar day worst month against 76 licensed places; census 70 median): ordinary near-full provider, drop the card from the next packet. Lau's second billing number (the P.C.) is $674k and stopped 2022-07: no change to the card. (added 2026-09-16)

```bat
(no command: paste the PIs' notes into the chat)
```

- [ ] **consensus_v7_disagreement** — CONSENSUS.md 2026-09-16: the 2021-12_v7 run scores the top-5 hospitals (Carolinas, Montefiore, Barnes Jewish, CEP America) at 0.00-0.02 while v3/v4/v5 score them 0.99-1.00; top_schemes are contract_pharmacy-led. Diagnose before the next pre-registration: is the 2023-12 freeze's 340B/contract_pharmacy input the driver, and is it absent under the 2021-12 freeze? Only 11 of 2,738 orgs are hot in >=3 runs, one in 4/5. Feed the answer into scheme triage; never gate a run on it. (added 2026-09-16)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.scheme_triage --run "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v7"
```

- [ ] **editions_undated_reference** — LOW: fill EDITIONS.csv data_period for the reference files that carry no date inside or in their name (PECOS.csv, hud/zip_county.*, nucc/*, opensanctions/targets.simple.csv): the download date is the period. Operator entries are never overwritten. (added 2026-09-16)

```bat
notepad "C:\Users\treyr\OneDrive\Desktop\data\preclean\EDITIONS.csv"
```

- [ ] **preclean_orphans_2026_09** — Operator's call (nothing runs on them): preclean/pecos/enrollment.csv is a 321 MB duplicate of PECOS.csv; preclean/nppes/npi_deactivation.csv is an unreadable 8 MB orphan (the wired file is nppes_deactivation/deactivation_20260608.zip). Delete or move both to free space; no adapter reads either. (added 2026-09-16)

```bat
dir "C:\Users\treyr\OneDrive\Desktop\data\preclean\pecos" "C:\Users\treyr\OneDrive\Desktop\data\preclean\nppes"
```

- [ ] **build_ready_to_wire_schemes** — SCHEME_DISCOVERY read 2026-10-21 (0 of 9 READY is correct: three are now WIRED (nh_ownership_stakes, nh_penalties, pbj_weekend_staffing) and the six hospice candidates read CHECK ROWS because the checker reads the header of a LONG-format file; facility.py already pivots the Measure Code rows, so the checker should read the pivoted view). CHECKER STALENESS to fix (Claude): the consumed-column registry misses columns adapters already read (Spending SERVICING_PROVIDER_NPI_NUM = the servicing grain; NDC PRESCRIBING_PROVIDER_NPI_NUM; the opioid Opioid_Prscrbr_Rate / LA rate = pill_mill), so they appear as 'unused signal'. THREE NEW DESIGN LEADS from the unused-field table: (a) Open Payments Physician_Ownership_Indicator + Third_Party_Payment_Recipient_Indicator / Name_of_Third_Party_Entity (a payment routed through an entity the physician owns: the kickback shape pharma_kickback does not see); (b) POS MDCD_VNDR_NUM + CROSS_REF_PROVIDER_NUMBER (the state Medicaid vendor number per CCN: a crosswalk for the state termination lists and the NPPES type-05 Medicaid ids); (c) CLIA AFFILIATED_PROVIDER_NUMBER_1..8 (lab-to-provider affiliations as graph edges for the lab-kickback ring shape); plus NH Complaint Deficiency / Infection Control Inspection Deficiency as worthless_services corroboration. No operator action. CLAUDE build queue from SCHEME_DISCOVERY 2026-09-16: three READY TO WIRE (nh_ownership_stakes from NH_Ownership_Nov2023 Ownership Percentage/Role; nh_penalties from NH_Penalties_Nov2023 Fine Amount; pbj_weekend_staffing from pbj_daily_staffing Hrs_RN by WorkDate weekday); the six hospice candidates need the long-format Measure Code pivot (facility.py already pivots hospice, so the discovery check should read the pivoted view); new idea: HCRIS HHA discipline visit counts (RN/LPN/PT/OT/SLP visits, Medicaid Title XIX) as a home-health capacity check (visits per licensed nurse). No operator action. (added 2026-09-16)
- [ ] **fl_la_state_lists** — 2026-10-21: FOUND and filed: the Louisiana State Exclusion List (5,972 rows with reasons) and the Florida AHCA Final Orders report (7,108 rows with NPIs and violation CODES: the codes need a code-to-statute map before any tiers hard; read STATE_TERMINATIONS_REPORT.md after the VM load). Still open: the s.409.913 terminated-for-cause list by records request to PublicRecordsReq@ahca.myflorida.com (the Denied/Terminated/Suspended download was a template). (added 2026-09-27)
- [ ] **fca_strict_label_export** — PARKED UNTIL THE PURPLELAB POC STARTS (operator 2026-10-09: nothing sent, POC not started). When it starts, send two attachments: the handoff brief PurpleLab_POC_Handoff_Brief_v4.pdf (chat, 2026-09-29; re-rendered on request) and processed\fca_label_strong\labels_fca_strict.csv (5,155 rows / 4,672 NPIs: exact_id + strong + moderate). NEVER the processed\fca_label\ copy (5,544 rows, carries the 389 weak rows that verified at 74%). STATUS OF THE LABEL: verified by the research agent on 249 rows (strong 39/40, moderate 69/75 = 92%, weak 65/88 = 74%, so weak is held out); tiered by FCA relevance; graded by label_audit. It has been used as the EVALUATION ruler (the forward FCA ruler that graded v6 and v7 today) but NO model has been TRAINED with the resolved-defendant rows yet: v7 was built 2026-09-14, the resolver ran 2026-09-28; v8 is the first such run. Say 'PurpleLab sent' after sending. (added 2026-09-27)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
dir "C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_strong\labels_fca_strict.csv"
powershell -Command "(Get-Content 'C:\Users\treyr\OneDrive\Desktop\data\processed\fca_label_strong\labels_fca_strict.csv').Count - 1"
REM attach both to the PurpleLab email when the POC starts; then say 'PurpleLab sent' in chat
```

- [ ] **storage_sense_off** — OneDrive Storage Sense dehydrated the data folder on 2026-10-02 (86.4 GB freed; every parquet became a cloud-only placeholder and queries died with 'The cloud file provider is not running'). The folder is pinned (attrib +P) but the setting must be changed or it recurs in 30 days: Settings > System > Storage > Storage Sense: set 'Content will become online-only if not opened for more than' to Never, and turn 'Automatic User content cleanup' Off. Then check free disk space; the overnight runs need tens of GB. (added 2026-10-05)

```bat
wmic logicaldisk get caption,freespace,size
python -m src.reminders --done-task storage_sense_off
```

- [ ] **build_nursing_home_dollar_basis** — BUILD QUEUE (lesson from Wesleyan Village, 2026-10-06): the cost_report_fraud scheme put Wesleyan at the 98th peer percentile (hcris_cost_anomaly 0.984, subscore 0.95) in BOTH the v6 and v7 runs, yet it sat in priority tier 6_none with zero concept signals, because the leads ranking is dollar-weighted on the Medicaid HCPCS file and a nursing home's revenue (the LT per diem) has no HCPCS code: Wesleyan shows $1.7M of assisted-living waiver lines against a multi-million per-diem stream the file cannot see. Nursing facilities (taxonomy 31xx) are therefore systematically under-ranked. Fix: for 31xx orgs use the HCRIS cost report as the dollar basis (Worksheet S-3 Medicaid days and total patient revenue, G-2/G-3 revenue) via the CCN crosswalk, and let cost_report_fraud + worthless_services + ownership_integrity carry the rank for that class. Also: the raw-HCRIS A-8-1 related-party share (hcris_related_party.py) should feed hcris.py's related_party_cost_share, which the published file lacks. (added 2026-10-06)

```bat
python -m src.reminders --done-task build_nursing_home_dollar_basis
```

- [ ] **v13_label_ablation_agency_codes** — CLAUDE / operator question for AFTER v12 (never gates a run): the enrollment file's agency codes with no stated cause are label candidates only where they are a MINORITY code in their state (FL 'TERM - MEDICAID AUTHORITY' 2,133 at 2 percent of FL's ends; IA / TX / AL 'ACTION TAKEN BY MEDICAID/CHIP' at 5 to 6 percent; CT 'MEDICAID AUTHORITY' 2,410 at 37 percent). Run label_ablation on the v12 matrix with those rows added as a source and read the forward-lift delta; admit them to v13's label only if the delta is positive with a CI above zero. (added 2026-10-12)

```bat
REM after v12 is graded; Claude writes the exact ablation line then
```

- [ ] **sage_followups_2026_10** — Sage follow-ups after the first report, the titles PDF, the WHEN sheets and the staff-NPI profile (PI_FEEDBACK_CROSSREF_2026-10-12.md, PI_ROLES_READOUT_2026-10-12.md, PI_WHEN_READOUT_2026-10-13.md, PI_STAFF_READOUT_2026-10-13.md: all with the PI report under <data_root>\enforcement\pi_reports\, never in git). DONE 2026-10-13: the seven NPPES lookups, the WHEN sheets, the staff-NPI profile (five of seven staff NPIs are absent from the fact as biller AND renderer; one Mott St NP rendered $293k over 12 months under TWO Lau billing numbers, the individual NPI and CHEUK W. LAU M.D., P.C. 1730477274, which the card never counted; the optometrist at Grand St renders only under two optical shops, never under Chang) and the Special Homes fact sheet ($59.6M 2018 to 2024, 99.6 percent H2016 per diem, one rendering NPI, 28.6 lines per patient-month). SETTLED 2026-10-13: Special Homes = 74 residents per calendar day in the worst month against 76 places (97 percent), census 70 median / 78 worst, 94 percent of days billed: an ordinary nearly-full residential provider, NO visit, drop from the next packet; Lau's P.C. number = $674k over 2018-01 to 2022-07 then stopped (4 percent of the practice), the card stands on the individual NPI. OURS, still open: (1) rerun pi_facts on the three Chinatown NPIs so the sheet names Lau's third rendering NPI by dollars (block below, one minute); (2) tell Brad the Infinity PACER first-to-file check is time-sensitive ($126M in 2022 alone). SAGE: the first-call list (PI_WHEN_READOUT section 2) and the WINDOWS sheet; titles for the Tier 4 cards (Brar, CTW / 212 Therapy, Clubhouse for Kids, Infinity, Perspectives); which practice the two Chang/Xie formers worked at; a second patient-flow window at 2 Mott St plus the Brooklyn office; EEG equipment / technologist at 139 Centre St; exam lanes at 217 Grand St; the Brooklyn route now four addresses (6402 8th Ave Rm 501, 749 and 755 61st St, the card-2 cluster); 2024 to 2026 departures for Queens Optometric and CTW; Infinity, Perspectives, Jacoby, Special Homes on hold until counsel says go. Close when the Tier 4 titles are in, the first-call list and WINDOWS sheet are with Sage, and (1) and (2) are done. WALLABOUT VISIT READ 2026-10-23 (Sage memo of Oct 21, card 1 of packet v11; the read is SAGE_WALLABOUT_READ_2026-10-23.md with the PI reports, never git): two of the card's five questions answered (the hourly arrival count, 92 in / 74 out over four hours, about 23 an hour, matching the billing's own 20-an-hour-per-office arithmetic; the FINIF Medical sign INSIDE the suite + the Layla Tov DOS record at the same suite = two of the five related entities tied to the address). NOT done: any clinician name or title (door, website, waiting room, badge: questions 2 and 3, which are the case), Boro Park and Monsey, Apt 3C, children vs adults in the count, license / registry / docket desk work; 'door open: No' contradicts the count. The eight follow-ups for Sage are section 5 of the read; send them with the first-call list. The volume is real, so the next packet frames card 1 as a rendering-provider case, not a capacity case; run the lab codes under the practice's NPIs (a CLIA physician-office lab at Boro Park, Aug 2025, is new). THE PA RULE, CHECKED 2026-10-23 (NY Medicaid Physician Policy Guidelines, eMedNY; the 837 billing reminder to physicians): card 1's 'what would end it' is ANSWERED. NY Medicaid FFS has the supervising / employing physician bill a registered PA's services and pays them at the STANDARD PHYSICIAN FEE ('no duplication or increase in charges solely because an RPA provided assistance'), so there is no rate differential and no overpayment from PA visits billed by the physician. The duty is disclosure: every physician claim must indicate the services rendered by or with the RPA with the RPA's name and Medicaid ID, the PA goes in the RENDERING field with the physician as supervising, PAs must be enrolled as non-billing providers, and a physician may supervise at most six RPAs in private practice (two under the 2022 edition). A practice that put the owner as rendering on PA visits has a claim-content defect, not a false dollar; the only money theories left are visits by unlicensed / unenrolled staff, supervision ratios, and the managed-care plans' own rendering rules (not found). DECISION: card 1 is a desk question for counsel (which plans paid, their rendering rules) and one former-employee question (who saw the children and whether any were unlicensed); no more surveillance on it. Scope the Sage follow-ups to that. (added 2026-10-12)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -u -m src.model_a.pi_facts --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v11" --npis "1508848441;1588703995;1558688754" --deep --out-prefix pi_facts_chinatown
REM say 'Sage first-call list + Wallabout follow-ups sent' when the email goes
```

- [ ] **pursuit_dossier__frozen_2021-12_v19** — The runner already built PURSUIT_DOSSIER.md (step 10). DISPATCH is gated: no Manus credits on a HOLD run (operator policy 2026-10-11); it runs only if the registry verdict is PROMOTE or the operator says go. (added 2026-10-22)

```bat
REM only on PROMOTE or the operator's go:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && source ~/.medicaid_env && python -m src.model_a.pursuit_dossier --run-dir /data/medicaid/model_a/frozen_2021-12_v19 --operator-book --dispatch"
python -m src.reminders --done-task pursuit_dossier__frozen_2021-12_v19
```

- [ ] **dossier_poll__frozen_2021-12_v19** — Only after the dossier was dispatched: ~30 min later collect the Manus research into the v19 dossier. (added 2026-10-22)

```bat
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && source ~/.medicaid_env && python -m src.model_a.pursuit_dossier --run-dir /data/medicaid/model_a/frozen_2021-12_v19 --poll"
python -m src.reminders --done-task dossier_poll__frozen_2021-12_v19
```

- [ ] **run_packet__frozen_2021-12_v21** — v21 finished 2026-10-23 00:51 UTC on the VM (17 step lines, every stage ran). Build the packet ON THE VM (tar: every report + audit file + the A/B arms, zero raw data), copy it to the laptop, upload it to the chat: Claude cannot see the run finished, so the packet IS the run's arrival. The dossier is NOT dispatched until the registry verdict is read (no Manus credits on a HOLD run). (added 2026-10-23)

```bat
REM 1. the packet on the VM (about a minute):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd /data/medicaid/model_a/frozen_2021-12_v21 && tar -czf /home/ubuntu/run_packet_v21.tar.gz *.md *.json *.csv ab_broadcast_weight ab_broadcast_mask ab_tier_charged $(ls -d ab_tier_commercial ab_class_priors ab_no_priors ab_prune 2>/dev/null) && ls -la /home/ubuntu/run_packet_v21.tar.gz"
REM 2. copy it down, then upload run_packet_v21.tar.gz to the chat:
scp -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139:/home/ubuntu/run_packet_v21.tar.gz C:\Users\treyr\Downloads\
REM 3. the reports back to OneDrive (optional, background):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && bash ops/vm/sync_data.sh up frozen_2021-12_v21"
python -m src.reminders --done-task run_packet__frozen_2021-12_v21
```

- [ ] **registry_verdict__frozen_2021-12_v21** — VERDICT DELIVERED 2026-10-23 (docs/ACCURACY_READOUT_v21_2026-10-23.md): HOLD on one of sixteen checks (top-50 Jaccard 0.247, bar 0.6); pursuit 3/3/4/8, large 3/3/5/6, the best head of the series; the class priors emptied the NPI-grain top 1% (lift 0.0) and destabilised the head; fixed in bundle 383 (weighted, size-floored priors); v22 = the re-grade. Close with the line below once you have read it. (added 2026-10-23)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v21
```

- [ ] **run_packet__frozen_2021-12_v22** — v22 finished 2026-10-24 06:07 UTC on the VM. Build the packet ON THE VM (every report + audit file + every ab_* arm, zero raw data), copy it down, upload it to the chat: the packet IS the run's arrival. No dossier dispatch until the verdict. (added 2026-10-24)

```bat
REM 1. the packet on the VM:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd /data/medicaid/model_a/frozen_2021-12_v22 && tar -czf /home/ubuntu/run_packet_v22.tar.gz *.md *.json *.csv $(ls -d ab_* 2>/dev/null) && ls -la /home/ubuntu/run_packet_v22.tar.gz"
REM 2. copy it down, then upload run_packet_v22.tar.gz to the chat:
scp -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139:/home/ubuntu/run_packet_v22.tar.gz C:\Users\treyr\Downloads\
python -m src.reminders --done-task run_packet__frozen_2021-12_v22
```

- [ ] **registry_verdict__frozen_2021-12_v22** — VERDICT DELIVERED 2026-10-24 (docs/ACCURACY_READOUT_v22_2026-10-24.md): HOLD on five of seventeen (pursuit 0/1/1/1, large 0/1/1/4, Jaccard 0.009) while the NPI grain read 19.3 (best ever) and the main list improved: the corrected priors moved the signal from the settlement classes (labs, systems) to owner-run agencies. v23 = the three-encoder A/B on one matrix. Close with the line below. (added 2026-10-24)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v22
```

- [ ] **run_packet__frozen_2021-12_v23** — Send the run packet for frozen_2021-12_v23 (every report + audit file, zero raw data) — Claude cannot see the run finished, so this zip IS the run's arrival (added 2026-10-24)

```bat
set RUN_DIR=C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v23
powershell -Command "Compress-Archive -Force -Path '%RUN_DIR%\*.md','%RUN_DIR%\*.json' -DestinationPath '%USERPROFILE%\Desktop\run_packet.zip'"
REM then upload run_packet.zip to the chat
python -m src.reminders --done-task run_packet__frozen_2021-12_v23
```

- [ ] **registry_verdict__frozen_2021-12_v23** — Open until Claude reads the packet and returns the release-registry verdict (PROMOTE/HOLD) + scheme-triage follow-ups for frozen_2021-12_v23 — close only when you have them VERDICT (2026-10-24): HOLD on two of seventeen (top50_jaccard 0.240, npi_lift1pct 0.0; pursuit 1/3/5/8, large 2/3/6/6). CAUSE = a code defect: the class-prior encoder leaked the training label (leave-one-out) in every prior run v21..v23; docs/ACCURACY_READOUT_v23_2026-10-24.md. Close with the line below once read. (added 2026-10-24)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v23
```

- [ ] **run_packet__frozen_2021-12_v24** — Send the run packet for frozen_2021-12_v24 (every report + audit file, zero raw data) — Claude cannot see the run finished, so this zip IS the run's arrival (added 2026-10-25)

```bat
set RUN_DIR=C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v24
powershell -Command "Compress-Archive -Force -Path '%RUN_DIR%\*.md','%RUN_DIR%\*.json' -DestinationPath '%USERPROFILE%\Desktop\run_packet.zip'"
REM then upload run_packet.zip to the chat
python -m src.reminders --done-task run_packet__frozen_2021-12_v24
```

- [ ] **registry_verdict__frozen_2021-12_v24** — Open until Claude reads the packet and returns the release-registry verdict (PROMOTE/HOLD) + scheme-triage follow-ups for frozen_2021-12_v24 — close only when you have them VERDICT (2026-10-25): HOLD on one of twenty (top50_jaccard 0.555; pursuit 1/3/4/7, large 1/3/4/6, NPI lift 6.7, score scale ok). The arms say honest class priors add nothing: v25 ships without them. docs/ACCURACY_READOUT_v24_2026-10-25.md. Close with the line below once read. (added 2026-10-25)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v24
```

- [ ] **run_packet__frozen_2021-12_v25** — Send the run packet for frozen_2021-12_v25 (every report + audit file, zero raw data) — Claude cannot see the run finished, so this zip IS the run's arrival (added 2026-10-25)

```bat
set RUN_DIR=C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v25
powershell -Command "Compress-Archive -Force -Path '%RUN_DIR%\*.md','%RUN_DIR%\*.json' -DestinationPath '%USERPROFILE%\Desktop\run_packet.zip'"
REM then upload run_packet.zip to the chat
python -m src.reminders --done-task run_packet__frozen_2021-12_v25
```

- [ ] **pursuit_dossier__frozen_2021-12_v25** — Build + dispatch the pursuit dossier for frozen_2021-12_v25 (skip if the run script already did — check for PURSUIT_DOSSIER.md in the run folder) THE OPERATOR'S DECISION (2026-10-25): the standing rule is no Manus credits on a HOLD run and v25 is a HOLD on one check at the noise floor; the other reading is that v25 is the first honest, stable, 19-of-20 list and the operational baseline. Say 'dispatch v25' (the line below sends ten research prompts off $MANUS_API_KEY) or 'skip v25' (closed as skipped). Nothing moves until one of those words. Run on the VM, never the laptop. OPERATOR 2026-10-26: 'running v26 tonight, dispatch tomorrow' -- the dispatch decision is deferred to the v26 packet read (2026-10-27); whichever run's dossier is dispatched, it is one dispatch, not two. 2026-10-26: SUPERSEDED as the dispatch candidate by v26 (two years more current, the method replicated); close as skipped unless the operator wants both. (added 2026-10-25)

```bat
REM ONLY on the operator's explicit 'dispatch v25' (spends Manus credits):
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && python -m src.model_a.pursuit_dossier --run-dir /data/medicaid/model_a/frozen_2021-12_v25 --operator-book --label-tier hard --exclusion-nodes /data/medicaid/graph_asof_2021-12_full/nodes/exclusion_nodes.parquet --dispatch"
python -m src.reminders --done-task pursuit_dossier__frozen_2021-12_v25
```

- [ ] **dossier_poll__frozen_2021-12_v25** — ~30 min after dispatch: collect Manus research into the frozen_2021-12_v25 dossier and send PURSUIT_DOSSIER.md Only after a dispatch (free, read-only at the service). (added 2026-10-25)

```bat
cd /d C:\Users\treyr\medicaid-fraud-detection
python -m src.model_a.pursuit_dossier --run-dir "C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2021-12_v25" --poll
python -m src.reminders --done-task dossier_poll__frozen_2021-12_v25
```

- [ ] **registry_verdict__frozen_2021-12_v25** — Open until Claude reads the packet and returns the release-registry verdict (PROMOTE/HOLD) + scheme-triage follow-ups for frozen_2021-12_v25 — close only when you have them VERDICT (2026-10-25): HOLD on one of twenty (pursuit hits@100 = 3 against 4; pursuit 2/2/3/6, large 2/2/2/6, Jaccard 0.622 PASSES, NPI lift 8.7, score scale ok). The series has converged: v17/v19/v24/v25 are one model and the head checks measure the fold draw. docs/ACCURACY_READOUT_v25_2026-10-25.md. Close with the line below once read. (added 2026-10-25)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2021-12_v25
```

- [ ] **run_packet__frozen_2023-12_v26** — Send the run packet for frozen_2023-12_v26 (every report + audit file, zero raw data) — Claude cannot see the run finished, so this zip IS the run's arrival (added 2026-10-26)

```bat
set RUN_DIR=C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2023-12_v26
powershell -Command "Compress-Archive -Force -Path '%RUN_DIR%\*.md','%RUN_DIR%\*.json' -DestinationPath '%USERPROFILE%\Desktop\run_packet.zip'"
REM then upload run_packet.zip to the chat
python -m src.reminders --done-task run_packet__frozen_2023-12_v26
```

- [ ] **pursuit_dossier__frozen_2023-12_v26** — Build + dispatch the pursuit dossier for frozen_2023-12_v26 (skip if the run script already did — check for PURSUIT_DOSSIER.md in the run folder) THE DISPATCH CANDIDATE (2026-10-26): v26's matrix is two years more current than v25's and the method holds on both freezes. The night-runner pick named a 2024 settlement first and two hospital systems: REBUILD the operator book under bundle 395 (organizations with a public case since the freeze set aside, the national list extended), READ PURSUIT_DOSSIER.md, then 'dispatch v26' sends it (credits). Order: merge 395, pull, rebuild, read, go. OPERATOR 2026-10-26: 'dispatch v26' = the explicit go; the dispatch line (rebuild + send) is authorized. TIME: the rebuild re-scores the matrix out-of-fold (ten seeds), so it runs ABOUT AN HOUR, not a minute: run it under tmux (disp26) and read the tail when it ends; close with the done line once it reports the tasks sent. REBUILD READ (2026-10-26, bundle 395 code): the head named Sunrise Medical Laboratories (Sonic Healthcare USA), LabOne (Quest Diagnostics) and ACM Medical Laboratory (Rochester Regional Health): laboratory arms of national or hospital-system owners, the do-not-send class with no cue word. Bundle 398 puts them on the national list; MERGE 398 AND PULL ON THE VM BEFORE the dispatch line (the dispatch rebuilds the pick itself, so the fix costs no extra hour). (added 2026-10-26)

```bat
REM 0. merge bundle 398 on the laptop (ONE line), then pull on the VM, THEN the dispatch line below:
call ops\laptop\merge_push.bat 398
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && git pull --ff-only && git log --oneline -1"
REM dispatch v26 was authorized 2026-10-26. The rebuild + send takes about an HOUR (out-of-fold scoring), so it runs under tmux; paste the tail to Claude when it ends:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && tmux new -d -s disp26 'python -m src.model_a.pursuit_dossier --run-dir /data/medicaid/model_a/frozen_2023-12_v26 --operator-book --label-tier hard --exclusion-nodes /data/medicaid/graph_asof_2023-12_full/nodes/exclusion_nodes.parquet --dispatch > /data/medicaid/model_a/frozen_2023-12_v26/dispatch.log 2>&1' && echo started"
REM check on it (free): the last lines of the log + the ten targets + the set-aside list
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "tail -20 /data/medicaid/model_a/frozen_2023-12_v26/dispatch.log; head -60 /data/medicaid/model_a/frozen_2023-12_v26/PURSUIT_DOSSIER.md; cat /data/medicaid/model_a/frozen_2023-12_v26/dossier_public_since_freeze.csv"
python -m src.reminders --done-task pursuit_dossier__frozen_2023-12_v26
```

- [ ] **dossier_poll__frozen_2023-12_v26** — ~30 min after dispatch: collect Manus research into the frozen_2023-12_v26 dossier and send PURSUIT_DOSSIER.md Dispatch authorized 2026-10-26; poll about 30 minutes after the send reports (free, read-only at the service). The poll runs on the VM (the registry file lives there), never on the laptop. (added 2026-10-26)

```bat
REM about 30 minutes after the dispatch log reports the tasks sent (free, read-only at the service); re-run until every prospect has findings:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && source .venv/bin/activate && set -a && source ~/.medicaid_env && set +a && python -m src.model_a.pursuit_dossier --run-dir /data/medicaid/model_a/frozen_2023-12_v26 --operator-book --label-tier hard --exclusion-nodes /data/medicaid/graph_asof_2023-12_full/nodes/exclusion_nodes.parquet --poll && grep -c 'pending' /data/medicaid/model_a/frozen_2023-12_v26/PURSUIT_DOSSIER.md"
REM then bring the dossier + research to the laptop and upload PURSUIT_DOSSIER.md to the chat:
ssh -n -o ConnectionAttempts=6 -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i C:\Users\treyr\.ssh\medicaid-vm.pem ubuntu@3.17.37.139 "cd ~/medicaid-fraud-detection && bash ops/vm/sync_data.sh up frozen_2023-12_v26"
python -m src.reminders --done-task dossier_poll__frozen_2023-12_v26
```

- [ ] **registry_verdict__frozen_2023-12_v26** — Open until Claude reads the packet and returns the release-registry verdict (PROMOTE/HOLD) + scheme-triage follow-ups for frozen_2023-12_v26 — close only when you have them VERDICT (2026-10-26): HOLD on one of seventeen (top50_jaccard 0.588; pursuit 1/2/5/6, large 1/3/5/6, NPI lift 15.0, score scale ok); FREEZE_PAIR: 8 of 8 comparable checks hold on both freezes = the method replicates on a matrix two years newer. docs/ACCURACY_READOUT_v26_2026-10-26.md. Close with the line below. (added 2026-10-26)

```bat
python -m src.reminders --done-task registry_verdict__frozen_2023-12_v26
```


## After ANY download session (standing rule)

1. `python -m src.vintage_inspect --data-root <data>` — dates + right-file check
2. `python -m src.model_a.scheme_discovery --data-root <data>` — new-scheme scan
3. Send me VINTAGE_INSPECT.md + SCHEME_DISCOVERY.md + any header that failed.

## After ANY model run (standing rule): send the run packet

The results are half the point; the feedback loops (scheme triage, release gating, expectations, source audit) need the run's OWN files. One command collects every report into a zip — replace RUN_DIR with the run folder (e.g. frozen_2023-12_v4), then upload run_packet.zip to the chat:

```bat
set RUN_DIR=C:\Users\treyr\OneDrive\Desktop\data\model_a\frozen_2023-12_v4
powershell -Command "Compress-Archive -Force -Path '%RUN_DIR%\*.md','%RUN_DIR%\*.json' -DestinationPath '%USERPROFILE%\Desktop\run_packet.zip'"
```

That captures: PROVIDER_FEATURES_EXPORT_REPORT, SOURCES_REPORT, EXPECTATIONS, SCHEME_HEALTH + SCHEME_TRIAGE, CASE_VALIDATION, PURSUIT_DOSSIER, RESULTS_DIGEST, OPERATING_POINTS, the feature manifest, and the run-registry row. No parquet/csv goes in the zip (reports only). A run is not closed until the packet is sent and Claude has updated the release registry + triage follow-ups from it.

