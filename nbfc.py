"""The `nbfc` task (see scrape.json): RBI's registry of NBFCs and ARCs.

RBI publishes two named-entity lists as downloadable files on its document host (rbidocs.rbi.org.in):
  - List of NBFCs and ARCs registered with the RBI  (kind = "registered")
  - List of NBFCs and ARCs whose CoR has been cancelled by the RBI  (kind = "cancelled")
Each row is one company with its Certificate of Registration (CoR) number, regional office, address and classification.

RBI serves these files with a `.XLSX` name, but the real content is sometimes a true XLSX workbook and sometimes an
HTML table exported with an .XLSX name. `parse_nbfc` handles both, and maps the columns by header name so it keeps
working when RBI renames a column. The task stores one record per company and keeps the raw file in the family bucket.

Note: from some networks rbidocs.rbi.org.in answers non-browser clients with a JavaScript bot-challenge page instead of
the file. `is_challenge` detects it so the task reports the failure instead of storing nothing.

Try it without the hub:  python nbfc.py --local <path-or-url> [registered|cancelled]
"""
import io
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

import rbicommon as rc

PROJECT = os.environ.get("SCRAPE_POOL_PROJECT", "rbi-nbfc-registered")
DEADLINE = time.time() + float(os.environ.get("SCRAPE_MAX_HOURS", "1")) * 3600 - 120
fetch = rc.make_fetch(requests)

# map a header cell (lower-cased) to a canonical field name; first match wins, so date/cancellation rules come before
# the CoR-number rule (a "Date of CoR" header holds both "date" and "cor")
_FIELD_RULES = [
    ("cancelled_on", r"cancel"),
    ("cor_date", r"date"),
    ("cor_number", r"\bcor\b|certificate of regist|registration no|regn\.? no"),
    ("regional_office", r"regional office|^ro$|\bregion\b"),
    ("email", r"e-?mail"),
    ("classification", r"classif|category|layer|type of|nbfc-|\bnbfc type"),
    ("address", r"address|office"),
    ("name", r"name|company|entity"),
]


def _canonical(header):
    h = (header or "").strip().lower()
    for field, pat in _FIELD_RULES:
        if re.search(pat, h):
            return field
    return None


def _rows_from_xlsx(content):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = []
    for r in ws.iter_rows(values_only=True):
        rows.append(["" if v is None else str(v).strip() for v in r])
    wb.close()
    return rows


def _rows_from_html(content):
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    soup = BeautifulSoup(text, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []
    tbl = max(tables, key=lambda t: len(t.find_all("tr")))
    rows = []
    for tr in tbl.find_all("tr"):
        rows.append([re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])])
    return rows


def _find_header(rows):
    """Index of the header row and its {col_index: canonical_field} map. Picks the row that maps the most columns."""
    best_i, best_map = None, {}
    for i, row in enumerate(rows[:15]):
        cmap = {}
        for j, cell in enumerate(row):
            field = _canonical(cell)
            if field and field not in cmap.values():
                cmap[j] = field
        if "name" in cmap.values() and len(cmap) > len(best_map):
            best_i, best_map = i, cmap
    return best_i, best_map


def detect_format(content):
    if rc.is_challenge(content):
        return "challenge"
    head = content[:8] if isinstance(content, (bytes, bytearray)) else content[:8].encode()
    if head[:2] == b"PK":
        return "xlsx"
    low = (content[:200].lower() if isinstance(content, (bytes, bytearray)) else content[:200].lower().encode())
    if b"<html" in low or b"<!doctype" in low or b"<table" in low:
        return "html"
    return "unknown"


def parse_nbfc(content, kind, url=None):
    """[(record_id, data)] for one NBFC/ARC list file. Pure: tested on saved files.

    Raises ValueError('challenge') when the bytes are RBI's bot-challenge page, so the caller can report it.
    """
    fmt = detect_format(content)
    if fmt == "challenge":
        raise ValueError("challenge")
    if fmt == "xlsx":
        rows = _rows_from_xlsx(content)
    elif fmt == "html":
        rows = _rows_from_html(content)
    else:
        raise ValueError(f"unrecognised file format: {fmt}")

    hi, cmap = _find_header(rows)
    if hi is None:
        raise ValueError("no header row with a name column found")
    headers = rows[hi]
    out, ids = [], set()
    for row in rows[hi + 1:]:
        if not any(c.strip() for c in row):
            continue
        rec = {"kind": kind, "url": url}
        for j, cell in enumerate(row):
            cell = (cell or "").strip()
            if j in cmap:
                rec[cmap[j]] = cell
            elif j < len(headers) and headers[j].strip():       # keep unmapped columns under their own header
                rec.setdefault("extra", {})[headers[j].strip()] = cell
        name = rec.get("name", "").strip()
        if not name or re.match(r"(?i)^(sr\.?\s*no|name of)", name):
            continue
        cor = re.sub(r"\s+", "", rec.get("cor_number", ""))
        rid = cor if cor and cor.upper() not in ("", "NA", "-") else f"{kind}-{rc.slug(name)}"
        base, k = rid, 1
        while rid in ids:
            k += 1
            rid = f"{base}-{k}"
        ids.add(rid)
        out.append((rid, rec))
    return out


# ---- hub loop ----------------------------------------------------------------------------------------------------

def local(src, kind):
    from scrape_hub_client import LocalHub   # counters in hub.counts, like the HubClient metrics

    hub = LocalHub()
    if re.match(r"^https?://", src):
        content = fetch(hub, requests.Session(), src, binary=True)
    else:
        content = open(src, "rb").read()
    if content is None:
        print("fetch failed", file=sys.stderr)
        return
    try:
        rows = parse_nbfc(content, kind, url=src)
    except ValueError as e:
        print(f"parse error: {e} (format={detect_format(content)})", file=sys.stderr)
        return
    for rid, data in rows[:20]:
        print(json.dumps({"record_id": rid, "data": data}, ensure_ascii=False))
    print(f"{len(rows)} records (showing <=20), counters {hub.counts}", file=sys.stderr)


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
            url = item["payload"]["url"]
            kind = item["payload"].get("kind", "registered")
            content = fetch(hub, session, url, binary=True, timeout=90)
            if content is None:
                hub.complete([item["key"]], "failed", error="file did not download")
                continue
            if rc.is_challenge(content):
                hub.failure("bot_challenge", item["key"], "rbidocs returned a bot-challenge page, not the file", url=url)
                hub.complete([item["key"]], "failed", error="bot-challenge page")
                continue
            ext = "xlsx" if detect_format(content) == "xlsx" else "html"
            hub.upload_many([(f"source/{item['key']}.{ext}", content,
                              "application/octet-stream")])
            try:
                rows = parse_nbfc(content, kind, url=url)
            except ValueError as e:
                hub.complete([item["key"]], "failed", error=f"parse: {e}"[:120])
                continue
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
        local(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "registered")
    else:
        main()
