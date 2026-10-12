import pandas as pd

from src.parquet_probe import probe_columns, value_counts


def test_probe_reports_liveness_and_values(tmp_path):
    p = tmp_path / "m.parquet"
    pd.DataFrame({"npi": ["1", "2", "3"], "deact_paid_after": [None, 0.0, 2.5],
                  "billing_after_death": [None, None, None], "status": ["TERMINATED", "ACTIVE", "TERMINATED"]}).to_parquet(p)
    rows = {r["column"]: r for r in probe_columns(str(p), "deact|death")}
    assert rows["deact_paid_after"]["non_null"] == 2 and rows["deact_paid_after"]["positive"] == 1
    assert rows["billing_after_death"]["non_null"] == 0
    assert value_counts(str(p), "status")[0] == ("TERMINATED", 2)
