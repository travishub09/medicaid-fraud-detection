#!/usr/bin/env python
r"""ops/laptop/fetch_pages.py <urls.txt> <cache_dir> — download cited pages on the laptop's
home connection for the VM's verify-sources pass (2026-11-02).

Why the laptop: OMIG, Texas OIG, DOJ, NH DOJ, mass.gov and ncdoj answer the cloud VM's
address with 403, a reset or a challenge page; a residential connection reads them. This is
a download, not a computation, so it is the laptop's job.

Writes <cache_dir>/<sha1(url)>.bin (the raw bytes exactly as served) and index.csv
(url, key, status, content_type, final_url, bytes, error). Resumable: a URL already in
the index with status 200 is skipped. Polite: about one request a second per host.

    python ops\laptop\fetch_pages.py unreachable_urls.txt C:\Users\treyr\OneDrive\Desktop\data\enforcement\page_cache
"""
from __future__ import annotations

import csv
import hashlib
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
           "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
FIELDS = ["url", "key", "status", "content_type", "final_url", "bytes", "error"]


def page_cache_key(url: str) -> str:               # the same key src/enforcement/research_evidence.py reads
    return hashlib.sha1(str(url or "").strip().encode("utf-8")).hexdigest()


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    import requests
    urls = [u.strip() for u in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if u.strip().startswith("http")]
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    ip = out / "index.csv"
    done = {}
    if ip.exists():
        with open(ip, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                done[r["url"]] = r
    todo = [u for u in urls if done.get(u, {}).get("status") != "200"]
    print(f"[fetch_pages] {len(urls):,} urls, {len(todo):,} to fetch, {len(urls) - len(todo):,} already cached -> {out}")
    last = {}
    ok = fail = 0
    with open(ip, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if ip.stat().st_size == 0:
            w.writeheader()
        for i, u in enumerate(todo, 1):
            host = urlparse(u).netloc
            wait = 1.2 - (time.time() - last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            row = {"url": u, "key": page_cache_key(u), "status": "", "content_type": "", "final_url": "", "bytes": 0, "error": ""}
            try:
                r = requests.get(u, headers=HEADERS, timeout=45, allow_redirects=True)
                row.update(status=str(r.status_code), content_type=r.headers.get("Content-Type", "")[:80],
                           final_url=r.url[:500], bytes=len(r.content))
                if r.status_code == 200:
                    (out / f"{row['key']}.bin").write_bytes(r.content)
                    ok += 1
                else:
                    fail += 1
            except Exception as e:  # noqa: BLE001
                row["error"] = f"{type(e).__name__}: {str(e)[:120]}"
                fail += 1
            last[host] = time.time()
            w.writerow(row)
            fh.flush()
            if i % 50 == 0 or i == len(todo):
                print(f"[fetch_pages] {i:,}/{len(todo):,}  ok {ok:,}  failed {fail:,}  last: {host} {row['status'] or row['error'][:40]}")
    print(f"[fetch_pages] done: {ok:,} pages cached, {fail:,} failed; copy the folder to the VM and run "
          "verify-sources --page-cache")


if __name__ == "__main__":
    main()
