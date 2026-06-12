"""Run the lookup tool locally (internal preview — see app.py gating note).

    python -m src.lookup_tool --features <percentiles.parquet> --port 8000
"""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--features", required=True,
                    help="percentile-features parquet (npi + 0–1 metric columns)")
    ap.add_argument("--host", default="127.0.0.1")   # local by default — preview only
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    import uvicorn
    from .app import build_app
    uvicorn.run(build_app(args.features), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
