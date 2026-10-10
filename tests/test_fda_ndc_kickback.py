"""FDA NDC Directory loader + the Medicaid-side pharma_kickback features (2026-10-10,
item 3 of the drug-file decision). Synthetic product.txt and Open Payments rows."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.ingest_cms import fda_ndc, medicaid_ndc as mndc

PH = "1003000126"
DR_PAID, DR_STEADY, DR_NONE = "1234567893", "1992999304", "1003000142"


def _product_txt(tmp_path: Path) -> Path:
    d = tmp_path / "fda_ndc"
    d.mkdir()
    rows = [
        "PRODUCTID\tPRODUCTNDC\tPRODUCTTYPENAME\tPROPRIETARYNAME\tNONPROPRIETARYNAME\tLABELERNAME\tSUBSTANCENAME\tDEASCHEDULE",
        "0071-0155_x\t0071-0155\tHUMAN PRESCRIPTION DRUG\tLIPITOR\tATORVASTATIN\tPfizer Laboratories Div Pfizer Inc\tATORVASTATIN CALCIUM\t",
        "0093-5056_x\t0093-5056\tHUMAN PRESCRIPTION DRUG\tLISINOPRIL\tLISINOPRIL\tTeva Pharmaceuticals USA, Inc.\tLISINOPRIL\t",
        "59011-410_x\t59011-410\tHUMAN PRESCRIPTION DRUG\tOXYCONTIN\tOXYCODONE HYDROCHLORIDE\tPurdue Pharma LP\tOXYCODONE HYDROCHLORIDE\tCII",
    ]
    (d / "product.txt").write_text("\n".join(rows) + "\n", encoding="latin-1")
    (d / "package.txt").write_text("PRODUCTID\tNDCPACKAGECODE\n", encoding="latin-1")
    return d


def test_loader_labeler_map_and_schedule(tmp_path):
    d = _product_txt(tmp_path)
    prod = fda_ndc.load_fda_product(d)                       # the folder
    assert {"product_ndc", "labeler", "dea"} <= set(prod.columns)
    lm = fda_ndc.labeler_map(prod).set_index("labeler5")
    assert lm.loc["00071", "mfr_key"] == "PFIZER"            # 4-digit labeler padded, key = first distinctive token
    assert lm.loc["00093", "mfr_key"] == "TEVA" and lm.loc["59011", "mfr_key"] == "PURDUE"
    assert fda_ndc.manufacturer_key("Pfizer Inc.") == "PFIZER" == fda_ndc.manufacturer_key("PFIZER LABORATORIES DIV PFIZER INC")
    assert fda_ndc.manufacturer_key("Teva Pharmaceuticals USA, Inc.") == "TEVA"
    sch = fda_ndc.product_schedule(prod).set_index("ndc9")
    assert sch.loc["590110410", "is_controlled"] == 1 and sch.loc["000710155", "is_controlled"] == 0
    # the zip form reads the same
    import zipfile
    z = tmp_path / "ndc_202610.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(d / "product.txt", "product.txt")
    assert len(fda_ndc.load_fda_product(z)) == 3


def test_medicaid_kickback_share_count_and_post_payment_lift(tmp_path):
    d = _product_txt(tmp_path)
    labs = fda_ndc.labeler_map(fda_ndc.load_fda_product(d))
    # the fact: DR_PAID wrote nothing of Pfizer's before 2023-03 and $1,000/mo after;
    # DR_STEADY wrote Pfizer's at $500/mo throughout; DR_NONE never got paid
    rows = []
    months = ["2022-10", "2022-11", "2022-12", "2023-01", "2023-02", "2023-03", "2023-04", "2023-05", "2023-06", "2023-07"]
    for m in months:
        if m >= "2023-04":
            rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH, PRESCRIBING_PROVIDER_NPI_NUM=DR_PAID, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=5, TOTAL_CLAIM_LINES=10, TOTAL_PAID=1000.0))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH, PRESCRIBING_PROVIDER_NPI_NUM=DR_PAID, NDC="00093505601", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=5, TOTAL_CLAIM_LINES=10, TOTAL_PAID=200.0))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH, PRESCRIBING_PROVIDER_NPI_NUM=DR_STEADY, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=5, TOTAL_CLAIM_LINES=10, TOTAL_PAID=500.0))
        rows.append(dict(BILLING_PROVIDER_NPI_NUM=PH, PRESCRIBING_PROVIDER_NPI_NUM=DR_NONE, NDC="00071015523", CLAIM_FROM_MONTH=m, TOTAL_PATIENTS=5, TOTAL_CLAIM_LINES=10, TOTAL_PAID=500.0))
    nd = tmp_path / "medicaid_ndc"
    nd.mkdir()
    pd.DataFrame(rows).to_csv(nd / "medicaid_ndc.csv", index=False)
    fact = tmp_path / "ndc_fact.parquet"
    mndc.build_ndc_fact(nd, fact)
    op = pd.DataFrame({
        "Covered_Recipient_NPI": [DR_PAID, DR_PAID, DR_STEADY],
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": ["Pfizer Inc.", "Pfizer Inc.", "PFIZER INC"],
        "Total_Amount_of_Payment_USDollars": ["150.00", "50.00", "20.00"],
        "Date_of_Payment": ["03/15/2023", "05/02/2023", "03/15/2023"],
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": ["LIPITOR", "LIPITOR", "LIPITOR"],
    })
    kb = mndc.medicaid_kickback(fact, op, labs, window_months=3).set_index("npi")
    # DR_PAID: Pfizer dollars 4 x 1000 of 4000 + 10 x 200 = 4000 / 6000
    assert abs(kb.loc[DR_PAID, "px_paid_mfr_share"] - 4000 / 6000) < 1e-9
    assert kb.loc[DR_PAID, "px_paid_mfr_count"] == 1 and abs(kb.loc[DR_PAID, "px_op_payment_total"] - 200.0) < 1e-9
    # first payment 2023-03: post window 2023-04..06 = 3000, pre window 2022-12..2023-02 = 0 -> lift 1.0
    assert abs(kb.loc[DR_PAID, "px_post_payment_lift"] - 1.0) < 1e-9
    # DR_STEADY: 1500 both sides -> 0; share 1.0
    assert kb.loc[DR_STEADY, "px_post_payment_lift"] == 0 and abs(kb.loc[DR_STEADY, "px_paid_mfr_share"] - 1.0) < 1e-9
    assert DR_NONE not in kb.index                             # no payments: no row, never a zero
    # the as-of cutoff drops payments after the freeze: nothing left for DR_PAID's May payment only
    kb2 = mndc.medicaid_kickback(fact, op, labs, asof_cutoff="2023-04", window_months=3).set_index("npi")
    assert DR_PAID in kb2.index                                 # the March payment is on/before the cutoff
    kb3 = mndc.medicaid_kickback(fact, op, labs, asof_cutoff="2023-02")
    assert kb3.empty                                            # every payment is after the freeze
    # the scheme registry carries the new inputs
    from src.model_a.scheme_subscores import DEFAULT_SCHEME_WEIGHTS
    assert {"px_paid_mfr_share", "px_post_payment_lift"} <= set(DEFAULT_SCHEME_WEIGHTS["pharma_kickback"])
