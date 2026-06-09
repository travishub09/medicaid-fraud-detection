# `src/backtest` — LEIE exclusion-list backtest

Validate the fraud-detection pipeline against the **OIG LEIE** (List of Excluded Individuals/
Entities) as ground truth: do the anomaly scores / leads the pipeline produces actually
concentrate on providers and companies that OIG later excluded?

All backtest code lives here and is run as a package module:

```
python -m src.backtest.<module>
```

Inputs are read-only (LEIE = `~/Desktop/data/preclean/Caught.csv`; provider/company scores
from `~/Desktop/data/{features,detection}`); outputs are new files only.

_Scope and method are filled in as the backtest is built out._
