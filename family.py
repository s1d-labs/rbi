"""What the `rbi` family scrapes (Reserve Bank of India, www.rbi.org.in). Change this file first, then run Setup.

Focus: named-entity regulatory data (specific companies, banks and people), not macro statistics.

Projects:
  rbi-enforcement       monetary penalties on named banks, NBFCs and co-operative banks (press releases, by month)
  rbi-authorised-pso    authorised + cancelled Payment System Operators (one inline HTML list)
  rbi-nbfc-registered   NBFCs and ARCs registered with the RBI (downloadable list)
  rbi-nbfc-cancelled    NBFCs and ARCs whose CoR the RBI has cancelled (downloadable list)

All work runs on GitHub runners: no geo, no proxy (www.rbi.org.in answers from outside India).
"""
import datetime
import json
from pathlib import Path

FAMILY = json.loads((Path(__file__).parent / "scrape.json").read_text())["family"]

WWW = "www.rbi.org.in"
DOCS = "rbidocs.rbi.org.in"
PR = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
NBFC_REG = "https://rbidocs.rbi.org.in/rdocs/content/DOCs/List_of_NBFCs_and_ARCs_registered_with_the_RBI.XLSX"
NBFC_CAN = "https://rbidocs.rbi.org.in/rdocs/content/DOCs/List_of_NBFCs_and_ARCs_whose_CoR_has_been_cancelled_by_the_RBI.XLSX"
PSO = "https://www.rbi.org.in/Scripts/PublicationsView.aspx?id=12043"

START_YEAR = 2005                                   # RBI press-release prid history reaches back to about here
END_YEAR = datetime.date.today().year

# one enforcement queue item per calendar month; the task posts the archive for that month and keeps the penalties.
# Newest month first (fresh penalties first), and future months are skipped.
_TODAY = datetime.date.today()
_months = [(y, m) for y in range(START_YEAR, END_YEAR + 1) for m in range(1, 13)
           if not (y == _TODAY.year and m > _TODAY.month)]
_months.sort(reverse=True)
_enforcement_items = [{"key": f"{y}-{m:02d}", "payload": {"year": y, "month": m, "expected": 5}} for y, m in _months]

PROJECTS = [
    {
        "name": f"{FAMILY}-enforcement",
        "title": "RBI enforcement – monetary penalties on banks and NBFCs",
        "task": "enforcement",
        "target": WWW,
        "env": {},
        "probe": f"{FAMILY}:pressrelease",
        "unit_label": "month", "record_label": "penalty",
        "items": _enforcement_items,
    },
    {
        "name": f"{FAMILY}-authorised-pso",
        "title": "RBI authorised Payment System Operators",
        "task": "authorised",
        "target": WWW,
        "env": {"SITE": PSO},
        "probe": f"{FAMILY}:authorised",
        "unit_label": "list", "record_label": "entity",
        "items": [{"key": "authorised-entities", "payload": {"url": PSO, "expected": 300}}],
    },
    {
        "name": f"{FAMILY}-nbfc-registered",
        "title": "RBI – NBFCs and ARCs registered",
        "task": "nbfc",
        "target": DOCS,
        "env": {},
        "probe": f"{FAMILY}:nbfc",
        "unit_label": "file", "record_label": "company",
        "items": [{"key": "registered", "payload": {"url": NBFC_REG, "kind": "registered", "expected": 9000}}],
    },
    {
        "name": f"{FAMILY}-nbfc-cancelled",
        "title": "RBI – NBFCs and ARCs whose CoR is cancelled",
        "task": "nbfc",
        "target": DOCS,
        "env": {},
        "probe": f"{FAMILY}:nbfc",
        "unit_label": "file", "record_label": "company",
        "items": [{"key": "cancelled", "payload": {"url": NBFC_CAN, "kind": "cancelled", "expected": 2500}}],
    },
]

# Health checks (run every minute; work for a project holds while its probe fails). All three are www.rbi.org.in
# pages (the document host cannot be probed for text), so they gate on RBI being up.
PROBES = [
    {"name": f"{FAMILY}:pressrelease", "url": PR, "expect": "Press Releases"},
    {"name": f"{FAMILY}:authorised", "url": PSO, "expect": "Authorised"},
    {"name": f"{FAMILY}:nbfc", "url": "https://www.rbi.org.in/Scripts/BS_NBFCList.aspx", "expect": "Non Banking Financial"},
]
