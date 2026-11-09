"""The `enforcement` task (see scrape.json): RBI monetary penalties on named banks, NBFCs and co-operative banks.

RBI announces every penalty as a press release titled "... imposes monetary penalty on <entity>". The press-release
archive on www.rbi.org.in is an ASP.NET page: an archive postback (hidden fields hdnYear + hdnMonth, with __VIEWSTATE)
returns the list of all press releases for one month. This task claims one (year, month) queue item, posts the archive,
keeps only the penalty press releases, fetches each detail page (the full text is inline, no document download needed),
and stores one record per penalty: the entity, the amount, the order date, the law sections and the reason.

Try one month without the hub:  python enforcement.py --local 2023 2
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

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "rbi-enforcement")
BASE = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 120
fetch = rc.make_fetch(requests)

# A press release is a penalty action when its title says so.
_PENALTY_TITLE = re.compile(r"imposes?\s+(?:a\s+)?monetary\s+penalty|monetary\s+penalty\s+on", re.I)


def _hidden(soup, name):
    el = soup.find("input", {"name": name})
    return el["value"] if el and el.has_attr("value") else ""


def viewstate_fields(base_html):
    """The ASP.NET hidden fields plus the archive submit-button name, from a GET of the press-release page."""
    soup = BeautifulSoup(base_html, "lxml")
    btn = soup.find(id="btn")
    data = {
        "__VIEWSTATE": _hidden(soup, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden(soup, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden(soup, "__EVENTVALIDATION"),
    }
    btn_name = btn.get("name") if btn is not None else None
    if btn_name:
        data[btn_name] = btn.get("value", "")
    return data, btn_name


def parse_month_list(html):
    """[(prid, title)] for every press release listed on one month's archive page. Pure: tested on a saved page."""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"prid=(\d+)", a["href"])
        if not m:
            continue
        prid = m.group(1)
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if not title or prid in seen:
            continue
        seen.add(prid)
        out.append((prid, title))
    return out


def is_penalty(title):
    """True when a press-release title announces a monetary penalty."""
    return bool(_PENALTY_TITLE.search(title or ""))


def _body_text(html):
    """The main press-release text (the largest table cell) as one clean string."""
    soup = BeautifulSoup(html, "lxml")
    tds = soup.find_all("td")
    if not tds:
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    cell = max(tds, key=lambda t: len(t.get_text()))
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True))


def _entity_from_title(title):
    m = re.search(r"penalty on\s+(.+)$", title, re.I)
    name = m.group(1).strip() if m else None
    if name:
        name = re.sub(r"\s*[–-]\s*(press release|revised).*$", "", name, flags=re.I).strip()
    return name


def _entity_from_body(text):
    m = re.search(r"penalty of[^.]*?\bon\s+(.+?)\s*\((?:the|a)\s+(?:bank|company|nbfc|entity|co-operative bank|society)\)",
                  text, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"penalty of[^.]*?\bon\s+(.+?)\s+for\s+(?:contravention|non[- ]|not\s|violation|failure)", text, re.I)
    return m.group(1).strip() if m else None


def parse_penalty(html, title=None, prid=None, url=None):
    """One penalty record (dict) from a press-release detail page. Pure: tested on a saved page.

    Fields: entity, amount_text, amount_inr, order_date, law_sections, reason, published_date, title, prid, url.
    """
    text = _body_text(html)
    entity = _entity_from_title(title) or _entity_from_body(text)
    amount_text, amount_inr = rc.rupees_to_inr(text)
    md = re.search(r"order\s+dated\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})", text)
    order_date = md.group(1) if md else None
    ms = re.search(r"(Section\s+.*?Act,?\s*\d{4})", text)
    law_sections = ms.group(1).strip() if ms else None
    mr = re.search(r"\bfor\s+(contravention[^.]*?|non[- ][^.]*?|violation[^.]*?)(?:\.\s|\s+This\s+penalty)", text, re.I)
    reason = (mr.group(1).strip() if mr else None)
    full = BeautifulSoup(html, "lxml").get_text(" ")
    mp = re.search(r"Date\s*:\s*([A-Z][a-z]+\s+\d{1,2},\s*\d{4})", full)
    published_date = mp.group(1) if mp else None
    return {
        "entity": entity,
        "amount_text": amount_text,
        "amount_inr": amount_inr,
        "order_date": order_date,
        "law_sections": law_sections,
        "reason": reason[:600] if reason else None,
        "published_date": published_date,
        "title": title,
        "prid": prid,
        "url": url,
        "summary": text[:600],
    }


def detail_url(prid):
    return f"{BASE}?prid={prid}"


# ---- hub loop ----------------------------------------------------------------------------------------------------

class LocalHub:
    settings = {"threads": 1, "delay_ms": 0, "paused": False}

    def __init__(self):
        self.counts = {}

    def metric(self, name, value=1):
        self.counts[name] = self.counts.get(name, 0) + value

    def error(self, n=1):
        self.metric("errors", n)

    def block(self, n=1):
        self.metric("blocked", n)


def _process_month(hub, session, year, month, fields, record):
    """Fetch one month, store a record for every penalty. `record(prid, data)` is given by the caller."""
    data = dict(fields)
    data["hdnYear"] = str(year)
    data["hdnMonth"] = str(month)
    html = fetch(hub, session, BASE, method="POST", data=data)
    if html is None:
        return None, 0
    n = 0
    for prid, title in parse_month_list(html):
        if not is_penalty(title):
            continue
        durl = detail_url(prid)
        detail = fetch(hub, session, durl)
        if detail is None:
            hub.failure("detail", prid, "detail page did not load")
            continue
        rec = parse_penalty(detail, title=title, prid=prid, url=durl)
        record(prid, rec, detail)
        n += 1
        time.sleep(max(0.3, hub.settings.get("delay_ms", 0) / 1000))
    return html, n


def local(year, month):
    hub = LocalHub()
    session = requests.Session()
    base_html = fetch(hub, session, BASE)
    fields, _ = viewstate_fields(base_html)
    out = []
    _process_month(hub, session, year, month, fields, lambda prid, rec, _d: out.append((prid, rec)))
    for prid, rec in out:
        print(json.dumps({"record_id": prid, "data": rec}, ensure_ascii=False))
    print(f"{len(out)} penalties in {year}-{int(month):02d}, counters {hub.counts}", file=sys.stderr)


def main():
    from scrape_hub_client import HubClient
    hub = HubClient(PROJECT, job_index=int(os.environ.get("SCRAPE_JOB_INDEX", "0")),
                    total_jobs=int(os.environ.get("SCRAPE_TOTAL_JOBS", "1")))
    session = requests.Session()
    fields = None
    try:
        while time.time() < DEADLINE:
            if hub.settings.get("paused"):
                time.sleep(30)
                continue
            items = hub.claim(1, steal=True, retry_failed=True)
            if not items:
                break
            item = items[0]
            if fields is None:
                base_html = fetch(hub, session, BASE)
                if base_html is None:
                    hub.complete([item["key"]], "failed", error="press-release page did not load")
                    continue
                fields, _ = viewstate_fields(base_html)
            year = item["payload"]["year"]
            month = item["payload"]["month"]

            uploads = []

            def record(prid, rec, detail_html):
                hub.record(prid, rec)
                uploads.append((f"penalty/{prid}.html.gz", gzip.compress(detail_html.encode()), "application/gzip"))

            html, n = _process_month(hub, session, year, month, fields, record)
            if html is None:
                fields = None                                   # viewstate likely stale: re-GET next time
                hub.complete([item["key"]], "failed", error="archive post did not load")
                continue
            if uploads:
                hub.upload_many(uploads)
            hub.complete([item["key"]], "done", fetched=n)
            time.sleep(max(0.5, hub.settings.get("delay_ms", 0) / 1000))
        hub.close()
    except BaseException as e:
        hub.close("failed", f"{type(e).__name__}: {e}"[:200])
        raise


if __name__ == "__main__":
    if sys.argv[1:2] == ["--local"]:
        local(sys.argv[2] if len(sys.argv) > 2 else "2023", sys.argv[3] if len(sys.argv) > 3 else "2")
    else:
        main()
