# VM runbook: night runs on a rented machine

Written 2026-10-11 after the operator's laptop (16 GB, 840 MB free at the time of the check) lost five sources to memory on v10 and has been embedding 1.5 million of the 13.6 million connected graph nodes since September. The laptop keeps doing the merges, the Manus dispatches and the reading. The VM only runs.

## 1. Cost

| option | size | on-demand price | a 6 hour run | a month at 4 runs |
|---|---|---|---|---|
| AWS r6i.2xlarge | 8 vCPU, 64 GB | about $0.50 per hour | about $3 | about $12 compute plus the disk |
| AWS r6i.4xlarge (recommended) | 16 vCPU, 128 GB | about $1.00 per hour | about $6 | about $24 compute plus the disk |
| Azure E8s v5 | 8 vCPU, 64 GB | about $0.50 per hour | about $3 | same shape |
| Azure E16s v5 | 16 vCPU, 128 GB | about $1.00 per hour | about $6 | same shape |

The disk: a 1 TB volume at about $0.08 per GB per month, about $80, and it is the only charge while the VM is stopped. Prices are the published on-demand Linux rates for the US East regions as of this writing; check the console before choosing, and stop the VM when a run is not going. The 128 GB class is recommended because the full embedding of 13.6 million nodes is the one step whose peak memory has not been measured; a dollar an hour removes the question. Spot or preemptible pricing is a third of this but the machine can be taken back mid-run; the export's resume cache survives that, the diagnostics do not, so start with on-demand.

## 2. Provision

1. Ubuntu 22.04 or 24.04, the size above, a 1 TB disk (the data root alone is 291 GB), SSH key login only, no public ports except SSH. The data is public billing data plus public enforcement records, but the lead lists are confidential work product: keep the machine private and delete the disk when the project retires it.
2. SSH in and run the setup script (installs Python, git, rclone, clones the dev branch, builds the venv, prepares the data root):

```bash
curl -fsSL https://raw.githubusercontent.com/travishub09/medicaid-fraud-detection/claude/happy-mccarthy-3skrn3/ops/vm/setup_ubuntu.sh | bash
```

3. Connect rclone to OneDrive once (interactive; it prints a link to open in the laptop's browser, then pastes a token back):

```bash
rclone config
```

Name the remote `onedrive`, type Microsoft OneDrive, default options. Then pull the data root. The first pull copies everything (expect an hour or two); later pulls move only what changed:

```bash
bash ~/medicaid-fraud-detection/ops/vm/sync_data.sh
```

4. If the dossier dispatch will ever run from the VM, put the Manus key in `~/.medicaid_env` by hand. It is never committed.

## 3. Run

Every run is pre-registered first, from the laptop or the VM (the registry files sync both ways through `sync_data.sh`):

```bash
cd ~/medicaid-fraud-detection && source ~/.medicaid_env
python -u -m src.model_a.run_registry --data-root "$MEDICAID_DATA_ROOT" --preregister --tag frozen_2021-12_v12 --goal "..." --expected "..." --check "hits50 >= 2" "hits100 >= 4" "prec50_lo > base50" "lead_ge_gap >= 0.5" "inst_share50 <= 0.4" "top50_jaccard >= 0.7" "eval_policy = fca" "horizon = 36" "frozen = True" "seeds >= 10" "groups = case_cluster" "broadcast = weight" "train = hard"
```

Then the runner, inside `tmux` or `nohup` so an SSH drop does not kill it:

```bash
tmux new -s night
bash ~/medicaid-fraud-detection/ops/vm/run_frozen_night.sh v12 2021-12
```

The runner trains the production scorer on the HARD tier only (`TRAIN_TIER=hard`: FCA-shaped exclusion records and DOJ cases; license actions and unstated-cause exclusions never train) and records it as `train_policy` in the registry, which the check `train = hard` enforces. The forward ruler is the FCA case ruler as before.

The runner refuses to start under 24 GB free, rebuilds the as-of graph with full embeddings into `graph_asof_2021-12_full` the first time (later runs reuse it; pass `--rebuild-graph` as the third argument to force it), and stops after the export if servicing, MUE, sector schemes, enrollment, the billing LM or the graph embeddings were skipped. It writes everything to `night_run.log` in the run folder. Detach with Ctrl-b then d; `tmux attach -t night` to come back.

Pull the code forward before a run when a new bundle has been merged and pushed from the laptop:

```bash
cd ~/medicaid-fraud-detection && git pull --ff-only origin claude/happy-mccarthy-3skrn3 && source .venv/bin/activate && pip install -r requirements.txt -q
```

## 4. Bring the results back

The packet for Claude, and the reports plus the registry files back to OneDrive so the laptop's dashboard and reminders see them:

```bash
cd $MEDICAID_DATA_ROOT/model_a/frozen_2021-12_v12 && zip -r ~/run_packet_v12.zip *.md *.json ab_broadcast_weight ab_broadcast_mask
bash ~/medicaid-fraud-detection/ops/vm/sync_data.sh up frozen_2021-12_v12
```

Download `~/run_packet_v12.zip` with scp (or from OneDrive after the upload) and attach it to the chat. Then stop the VM.

## 5. Storage: where the 291 GB lives

The data root is 291 GB in 25,310 files (measured 2026-10-11: preclean 178 GB, interim 23 GB, features 15 GB, processed 15 GB, model_a 6.5 GB, the rest under 3 GB each). The VM pulls about 230 GB: everything except the run folders and `features` (stage outputs the frozen runs never read). Three copies, each with a job:

1. **OneDrive** (Desktop/data) is the laptop's master and already a cloud copy; it needs the 1 TB plan. The laptop keeps `processed`, `enforcement`, `model_a`, `graph` and `detection` on disk for its own tools and may mark `preclean` (178 GB) and `interim` (23 GB) "Free up space" by hand once the VM is the runner (never through Storage Sense, and never run the export on the laptop after that).
2. **The VM disk** (1 TB gp3, about $80 a month while stopped) holds the working copy the runs read and write.
3. **S3** is the durable copy, mirrored FROM THE VM after the first pull (free, fast, same region). About $7 a month at Standard, half that at Standard-IA. Private bucket, versioning on, public access blocked:

```bash
sudo snap install aws-cli --classic
aws configure                     # an IAM user with S3 access only; region us-east-2
aws s3 mb s3://medicaid-fraud-data-<initials> --region us-east-2
aws s3api put-bucket-versioning --bucket medicaid-fraud-data-<initials> --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket medicaid-fraud-data-<initials> --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3 sync /data/medicaid s3://medicaid-fraud-data-<initials>/data --storage-class STANDARD_IA
```

Rebuilding a VM disk from S3 takes minutes; from OneDrive, hours. Re-run the sync after each procurement session.

## 6. What the VM changes and what it does not

It changes completeness: every source present, the embeddings uncapped, the motif columns real, repeats and seeds affordable. It does not change the label, the ruler or the checks, so a VM run is graded exactly like a laptop run and is a new pre-registration like any other. The laptop remains the operator's seat: merges, pushes, Manus, reading.

v12 is the first VM run (2026-10-11: the AWS vCPU quota request was still pending, so v11 ran on the laptop fallback run_v11_night.bat). On the laptop: on the laptop the embeddings stay capped and the five memory casualties are a coin flip, on the VM both are settled. Start the data pull only after the fuzzy last step has synced to OneDrive, so the VM reads the resolver table that carries the 47 promoted rows.

_Leads for counsel review, never accusations._
