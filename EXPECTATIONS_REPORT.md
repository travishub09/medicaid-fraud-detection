# EXPECTATIONS_REPORT — where the data or a calculation didn't behave as designed

_4 FAIL / 1 WARN. A FAIL is a calculation or join that ran but produced something structurally wrong; a WARN is a distribution that deserves a look. This is the go-back-and-fix queue — the run itself was not stopped._

| severity | column/scope | check | observed | expected | go look at |
|---|---|---|---|---|---|
| FAIL | `billing_residual` | all_nan_feature | 0 non-null values | a produced feature carries data for someone | its source loaded zero usable rows (wrong layout / bad join key) — the DMEPOS class of failure |
| FAIL | `expected_net_paid` | all_nan_feature | 0 non-null values | a produced feature carries data for someone | its source loaded zero usable rows (wrong layout / bad join key) — the DMEPOS class of failure |
| FAIL | `expected_service_volume` | all_nan_feature | 0 non-null values | a produced feature carries data for someone | its source loaded zero usable rows (wrong layout / bad join key) — the DMEPOS class of failure |
| FAIL | `volume_residual` | all_nan_feature | 0 non-null values | a produced feature carries data for someone | its source loaded zero usable rows (wrong layout / bad join key) — the DMEPOS class of failure |
| WARN | `provider_on_leie` | base_rate_implausible | 7.1% positive | rare-event base rate (≈0.3%) | the label column may be mis-joined or fanned out |
