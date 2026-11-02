"""recap_fetch.py — buy a docket entry's PDF through CourtListener's RECAP Fetch API (2026-11-01).

WHY: nobody on the team could read a complaint without a PACER login, so the Sims complaint
(Accu) sat unread for four days while the packet waited. With a PACER account (the funder's
card; ten cents a page, three dollars a document, waived under thirty dollars a quarter) and
a CourtListener API token, this module buys the document the day it is unsealed and saves
it under the case folder. The purchase goes through CourtListener, so the PDF also lands in
the public RECAP archive (no purchaser name on it) and the next reader gets it free.

    python -m src.enforcement.recap_fetch --docket 67387905 --entry 1 --out <folder>
    python -m src.enforcement.recap_fetch --docket 67387905 --entry 1 --out <folder> --dry-run

Environment: COURTLISTENER_TOKEN, PACER_USERNAME, PACER_PASSWORD (the VM's ~/.medicaid_env;
never on a command line, never in chat). Transport injectable for tests.

The flow (CourtListener API v4): GET docket-entries?docket=<id>&entry_number=<n> -> the
RECAP document id; GET recap-documents/<id>: if `filepath_local` is set the PDF is already
in the archive (free); else POST recap-fetch {request_type: 2, recap_document: <id>,
pacer_username, pacer_password}, poll recap-fetch/<id>/ until status 2 (success) or a
failure code, then download storage.courtlistener.com/<filepath_local>. A sealed entry
has no purchasable document; the result says so.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

CL = "https://www.courtlistener.com/api/rest/v4/"
STORAGE = "https://storage.courtlistener.com/"
FETCH_SUCCESS, FETCH_FAILED = 2, 3
MAX_WAIT_S = 300


def _auth() -> dict:
    tok = os.environ.get("COURTLISTENER_TOKEN", "")
    if not tok:
        raise RuntimeError("COURTLISTENER_TOKEN is not set (the VM's ~/.medicaid_env)")
    return {"Authorization": f"Token {tok}"}


class RequestsTransport:
    """GET / POST JSON + a binary download through requests (the default)."""

    def get(self, url: str, params: dict | None = None) -> dict:
        import requests
        r = requests.get(url, params=params, headers=_auth(), timeout=60)
        r.raise_for_status()
        return r.json()

    def post(self, url: str, data: dict) -> dict:
        import requests
        r = requests.post(url, data=data, headers=_auth(), timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} from {url}: {r.text[:300]}")
        return r.json()

    def download(self, url: str, dest: Path) -> int:
        import requests
        with requests.get(url, headers=_auth(), timeout=120, stream=True) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            n = 0
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
                    n += len(chunk)
        return n


def find_document(docket_id: str, entry_number: int, transport) -> dict:
    """The RECAP document record for a docket entry (the main document, not an attachment)."""
    page = transport.get(CL + "docket-entries/", {"docket": str(docket_id), "entry_number": int(entry_number)})
    entries = page.get("results") or []
    if not entries:
        return {"status": "no_entry", "note": f"docket {docket_id} has no entry {entry_number} on CourtListener "
                                              "(sealed, or the docket sheet is stale: buy the docket sheet first)"}
    e = entries[0]
    docs = e.get("recap_documents") or []
    main = next((d for d in docs if str(d.get("document_type", "1")) in ("1", "main") and not d.get("attachment_number")), None) \
        or (docs[0] if docs else None)
    if main is None:
        return {"status": "no_document", "note": "the entry has no RECAP document record (a minute entry or sealed)"}
    if isinstance(main, str):                       # a URL, not an embedded record
        main = transport.get(main)
    return {"status": "ok", "document": main, "entry": {k: e.get(k) for k in ("id", "entry_number", "date_filed", "description")}}


def _doc_url(doc: dict) -> str:
    return str(doc.get("resource_uri") or doc.get("absolute_url") or (CL + f"recap-documents/{doc.get('id')}/"))


def refresh_docket(docket_id: str, transport, sleep=time.sleep, max_wait_s: int = MAX_WAIT_S, log=print) -> dict:
    """Buy the DOCKET SHEET from PACER through the RECAP Fetch API (request_type 1) so the
    CourtListener docket carries the entries filed since it was last scraped. 2026-11-02:
    the Sims docket (67387905) was scraped while the qui tam was SEALED, so CourtListener
    listed no entry 1 after the unsealing and the complaint could not be bought until the
    sheet was refreshed. Costs ten cents a page of the sheet; nothing when it fails."""
    user, pw = os.environ.get("PACER_USERNAME", ""), os.environ.get("PACER_PASSWORD", "")
    if not user or not pw:
        return {"status": "failed", "note": "PACER_USERNAME / PACER_PASSWORD are not set (the VM's ~/.medicaid_env)"}
    try:
        req = transport.post(CL + "recap-fetch/", {"request_type": 1, "docket": str(docket_id),
                                                  "pacer_username": user, "pacer_password": pw})
    except RuntimeError as e:
        # 2026-11-02: PACER's "Did not get NextGenCSO cookie" = the login itself was refused
        # (wrong password, or a new account never signed in on pacer.uscourts.gov to accept
        # its terms): a one-line status, never a traceback
        msg = str(e)
        hint = (" (PACER refused the login: sign in once at pacer.uscourts.gov with these credentials, "
                "accept the terms, or reset the password)" if "PacerLogin" in msg or "NextGenCSO" in msg else "")
        return {"status": "failed", "note": f"docket refresh refused: {msg[:240]}{hint}"}
    rid = req.get("id")
    log(f"[recap_fetch] docket sheet refresh requested (fetch id {rid}) for docket {docket_id}")
    waited, status = 0, req.get("status")
    while status not in (FETCH_SUCCESS, FETCH_FAILED) and waited < max_wait_s:
        sleep(5)
        waited += 5
        req = transport.get(CL + f"recap-fetch/{rid}/")
        status = req.get("status")
    if status != FETCH_SUCCESS:
        return {"status": "failed", "note": f"docket refresh {rid} ended with status {status}: "
                                             f"{req.get('message') or req.get('error_message') or ''}"[:300]}
    return {"status": "refreshed", "fetch_id": rid}


def fetch_pdf(docket_id: str, entry_number: int, out_dir: Path, transport=None, dry_run: bool = False,
              sleep=time.sleep, max_wait_s: int = MAX_WAIT_S, log=print, refresh: bool = False) -> dict:
    """Buy (or take from the archive) the entry's PDF into ``out_dir``. Returns a dict
    with status: archived | purchased | dry_run | no_entry | no_document | sealed | failed.
    ``refresh``: when the entry is missing from CourtListener's copy of the docket, buy the
    docket sheet first (refresh_docket) and look again."""
    transport = transport or RequestsTransport()
    found = find_document(docket_id, entry_number, transport)
    if found["status"] == "no_entry" and refresh and not dry_run:
        log(f"[recap_fetch] no_entry: {found['note']} -> refreshing the docket sheet from PACER")
        r = refresh_docket(docket_id, transport, sleep=sleep, max_wait_s=max_wait_s, log=log)
        if r["status"] != "refreshed":
            return r
        found = find_document(docket_id, entry_number, transport)
    if found["status"] != "ok":
        log(f"[recap_fetch] {found['status']}: {found['note']}")
        return found
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    doc = found["document"]
    doc_id = doc.get("id")
    dest = Path(out_dir) / f"docket{docket_id}_entry{entry_number}.pdf"
    if doc.get("is_sealed"):
        return {"status": "sealed", "note": "the document is sealed on the court's docket; nothing to buy"}
    if doc.get("filepath_local"):
        if dry_run:
            return {"status": "dry_run", "note": f"archived already: {doc['filepath_local']} (free)", "dest": str(dest)}
        n = transport.download(STORAGE + str(doc["filepath_local"]).lstrip("/"), dest)
        log(f"[recap_fetch] archived copy ({n:,} bytes, free) -> {dest}")
        return {"status": "archived", "dest": str(dest), "bytes": n, "cost_usd": 0.0}
    if dry_run:
        pages = doc.get("page_count")
        est = min(3.0, 0.10 * pages) if pages else None
        return {"status": "dry_run", "note": f"not in the archive; a purchase would cost about ${est if est is not None else '<= 3.00'}",
                "dest": str(dest), "recap_document": doc_id}
    user, pw = os.environ.get("PACER_USERNAME", ""), os.environ.get("PACER_PASSWORD", "")
    if not user or not pw:
        return {"status": "failed", "note": "PACER_USERNAME / PACER_PASSWORD are not set (the VM's ~/.medicaid_env)"}
    try:
        req = transport.post(CL + "recap-fetch/", {"request_type": 2, "recap_document": doc_id,
                                                  "pacer_username": user, "pacer_password": pw})
    except RuntimeError as e:
        msg = str(e)
        hint = (" (PACER refused the login: sign in once at pacer.uscourts.gov with these credentials, "
                "accept the terms, or reset the password)" if "PacerLogin" in msg or "NextGenCSO" in msg else "")
        return {"status": "failed", "note": f"purchase refused: {msg[:240]}{hint}"}
    rid = req.get("id")
    log(f"[recap_fetch] purchase requested (fetch id {rid}) for recap document {doc_id}")
    waited = 0
    status = req.get("status")
    while status not in (FETCH_SUCCESS, FETCH_FAILED) and waited < max_wait_s:
        sleep(5)
        waited += 5
        req = transport.get(CL + f"recap-fetch/{rid}/")
        status = req.get("status")
    if status != FETCH_SUCCESS:
        return {"status": "failed", "note": f"fetch {rid} ended with status {status}: {req.get('message') or req.get('error_message') or ''}"[:300]}
    doc2 = transport.get(_doc_url(doc))
    if not doc2.get("filepath_local"):
        return {"status": "failed", "note": "the fetch succeeded but the document carries no file path yet; retry in a minute"}
    n = transport.download(STORAGE + str(doc2["filepath_local"]).lstrip("/"), dest)
    pages = doc2.get("page_count")
    cost = (min(3.0, 0.10 * pages) if pages else None)
    log(f"[recap_fetch] purchased ({n:,} bytes; about ${cost if cost is not None else '<= 3.00'}) -> {dest}")
    return {"status": "purchased", "dest": str(dest), "bytes": n, "cost_usd": cost, "fetch_id": rid}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docket", required=True, help="the CourtListener docket id (the number in the docket URL)")
    ap.add_argument("--entry", type=int, required=True, help="the docket entry number (ECF number)")
    ap.add_argument("--out", required=True, help="folder for the PDF (work product: under <data_root>/enforcement/)")
    ap.add_argument("--dry-run", action="store_true", help="say whether it is archived (free) or what a purchase would cost")
    ap.add_argument("--max-wait", type=int, default=MAX_WAIT_S)
    ap.add_argument("--refresh-docket", action="store_true",
                    help="when CourtListener lists no such entry, buy the docket sheet from PACER first "
                         "(a docket scraped while sealed carries no entries) and look again")
    a = ap.parse_args()
    res = fetch_pdf(a.docket, a.entry, Path(a.out), dry_run=a.dry_run, max_wait_s=a.max_wait, refresh=a.refresh_docket)
    print(f"[recap_fetch] {res}")
    if res.get("status") in ("failed",):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
