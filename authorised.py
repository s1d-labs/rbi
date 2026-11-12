"""The `authorised` task (see scrape.json): RBI's list of authorised Payment System Operators (PSOs).

RBI publishes the list of entities that hold a Certificate of Authorisation (CoA) under the Payment and Settlement
Systems Act as one page on www.rbi.org.in (PublicationsView.aspx?id=12043). The whole list is an inline HTML table, so
no document download is needed. The page holds two sections: entities that hold a current CoA, and entities whose CoA
was cancelled, returned or surrendered. This task stores one record per named entity from both sections.

Try it without the hub:  python authorised.py --local
"""
import gzip
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

import rbicommon as rc

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "rbi-authorised-pso")
SITE = os.environ.get("SITE", "https://www.rbi.org.in/Scripts/PublicationsView.aspx?id=12043")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 120
fetch = rc.make_fetch(requests)

_SR = re.compile(r"^\d+\.?$")
_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")


def _cells(tr):
    return [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"], recursive=False)]


def _is_category(text):
    if not text or _SR.match(text):
        return False
    if re.search(r"\bkb\b|Date\s*:|Certificates?\s+of\s+Authorisation", text, re.I):
        return False
    return len(text) <= 80


def parse_authorised(html, url=None):
    """[(record_id, data)] for every authorised / cancelled payment-system entity. Pure: tested on a saved page.

    Authorised record: {status: 'authorised', category, name, address, authorisations: [{payment_system, date}], url}.
    Cancelled record:  {status: 'cancelled', name, address, payment_system, cancelled_on, remarks, url}.
    """
    soup = BeautifulSoup(html, "lxml")
    out, ids = [], {}
    category = None
    last_auth = None

    def add(rid, data):
        if rid in ids:                                          # keep record ids unique within the project
            ids[rid] += 1
            rid = f"{rid}-{ids[rid]}"
        else:
            ids[rid] = 1
        out.append((rid, data))
        return rid

    for tr in soup.find_all("tr"):
        cells = _cells(tr)
        n = len(cells)
        if n == 1:
            if _is_category(cells[0]):
                category = cells[0]
            continue
        if n == 2:                                              # continuation of the previous authorised entity
            if last_auth is not None and _DATE.search(cells[1] or ""):
                last_auth["authorisations"].append({"payment_system": cells[0], "date": cells[1]})
            continue
        if n == 5 and _SR.match(cells[0]):
            data = {"status": "authorised", "category": category, "name": cells[1], "address": cells[2],
                    "authorisations": [{"payment_system": cells[3], "date": cells[4]}], "url": url}
            add(f"auth-{rc.slug(cells[1])}", data)
            last_auth = data
            continue
        if n == 6 and _SR.match(cells[0]):
            data = {"status": "cancelled", "name": cells[1], "address": cells[2], "payment_system": cells[3],
                    "cancelled_on": cells[4], "remarks": cells[5], "url": url}
            add(f"canc-{rc.slug(cells[1])}", data)
            last_auth = None
            continue
        last_auth = None                                        # any other row ends a continuation run
    return out


# ---- hub loop ----------------------------------------------------------------------------------------------------

def local():
    from scrape_hub_client import LocalHub   # counters in hub.counts, like the HubClient metrics

    hub = LocalHub()
    page = fetch(hub, requests.Session(), SITE)
    rows = parse_authorised(page, url=SITE) if page else []
    for rid, data in rows:
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))
    print(f"{len(rows)} entities, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")),
                    total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()
    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            url = item["payload"].get("url", SITE)
            page = fetch(hub, session, url)
            if page is None:
                hub.complete([item["key"]], "failed", error="page did not load")
                continue
            hub.upload_many([(f"pages/{item['key']}.html.gz", gzip.compress(page.encode()), "application/gzip")])
            rows = parse_authorised(page, url=url)
            for rid, data in rows:
                hub.record(rid, data)
            hub.complete([item["key"]], "done", fetched=len(rows))
            time.sleep(max(0.5, hub.settings.get("delay_ms", 0) / 1000))
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local()
    else:
        main()
