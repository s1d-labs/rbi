"""Parsing tests on saved pages and files: fast, no network. Run: python -m pytest tests -q

Fixtures (tests/pages/):
  enforcement-2023-02.html   a real RBI press-release archive page for Feb 2023 (frozen history)
  penalty-55293.html         a real penalty press-release detail page (frozen history)
  authorised-pso.html        a real snapshot of the authorised Payment System Operators list
  nbfc-registered-sample.xlsx a true .xlsx with RBI's registered-NBFC columns
  nbfc-cancelled-sample.html  an HTML-table export named like RBI's .XLSX-but-HTML files
  nbfc-challenge.html         a real rbidocs bot-challenge page (served instead of a file)
"""
from pathlib import Path

import pytest

import authorised
import enforcement
import nbfc
import rbicommon as rc

PAGES = Path(__file__).parent / "pages"
rd = lambda f: (PAGES / f).read_text(encoding="utf-8", errors="replace")
rb = lambda f: (PAGES / f).read_bytes()


# ---- enforcement -------------------------------------------------------------------------------------------------

def test_month_list_finds_all_press_releases():
    ml = enforcement.parse_month_list(rd("enforcement-2023-02.html"))
    prids = {p for p, _ in ml}
    assert len(ml) > 100                                   # Feb 2023 had many press releases
    assert len(prids) == len(ml)                           # no duplicate prids
    assert "55293" in prids                                # a known penalty is in the month


def test_penalty_filter_keeps_only_penalties():
    ml = enforcement.parse_month_list(rd("enforcement-2023-02.html"))
    penalties = [(p, t) for p, t in ml if enforcement.is_penalty(t)]
    assert len(penalties) == 17                            # frozen history: exactly 17 penalties in Feb 2023
    assert all("penalty" in t.lower() for _, t in penalties)
    by_prid = dict(penalties)
    assert by_prid["55293"].startswith("RBI imposes monetary penalty on Vyaparik Audhyogik Sahakari Bank Limited")


def test_is_penalty():
    assert enforcement.is_penalty("RBI imposes monetary penalty on XYZ Bank")
    assert enforcement.is_penalty("Reserve Bank of India imposes monetary penalty on ABC Co-operative Bank")
    assert not enforcement.is_penalty("Auction of 91-Day, 182-Day and 364-Day Treasury Bills")
    assert not enforcement.is_penalty("Directions under Section 35A of the Banking Regulation Act")


def test_parse_penalty_detail_fields():
    title = "RBI imposes monetary penalty on Vyaparik Audhyogik Sahakari Bank Limited, Indore"
    rec = enforcement.parse_penalty(rd("penalty-55293.html"), title=title, prid="55293", url="u")
    assert rec["entity"] == "Vyaparik Audhyogik Sahakari Bank Limited, Indore"
    assert rec["amount_inr"] == 100000                     # ₹1.00 lakh
    assert "1.00 lakh" in rec["amount_text"]
    assert rec["order_date"] == "February 20, 2023"
    assert rec["published_date"] == "Feb 27, 2023"
    assert "Banking Regulation Act, 1949" in rec["law_sections"]
    assert rec["reason"] and "Exposure Norms" in rec["reason"]
    assert rec["prid"] == "55293"


def test_rupees_to_inr():
    assert rc.rupees_to_inr("a monetary penalty of ₹1.00 lakh (Rupees One lakh)")[1] == 100000
    assert rc.rupees_to_inr("penalty of ₹25 lakh on")[1] == 2_500_000
    assert rc.rupees_to_inr("penalty of ₹5 crore")[1] == 50_000_000
    assert rc.rupees_to_inr("penalty of ₹10,00,000 (Rupees Ten Lakh only)")[1] == 1_000_000
    assert rc.rupees_to_inr("no amount here") == (None, None)


# ---- authorised payment system operators -------------------------------------------------------------------------

def test_authorised_pso_parse():
    rows = authorised.parse_authorised(rd("authorised-pso.html"), url="u")
    auth = [d for _, d in rows if d["status"] == "authorised"]
    canc = [d for _, d in rows if d["status"] == "cancelled"]
    assert len(auth) > 150 and len(canc) > 20
    ids = [rid for rid, _ in rows]
    assert len(ids) == len(set(ids))                       # record ids are unique
    names = {d["name"] for d in auth}
    assert any("National Payments Corporation of India" in n for n in names)
    assert any("Clearing Corporation of India" in n for n in names)
    # every authorised entity carries at least one (payment system, date) authorisation and a category
    for d in auth:
        assert d["authorisations"] and d["authorisations"][0]["payment_system"]
        assert d["category"]
    canc_names = {d["name"] for d in canc}
    assert any("Federal Bank" in n for n in canc_names)


# ---- NBFC / ARC registry -----------------------------------------------------------------------------------------

def test_detect_format():
    assert nbfc.detect_format(rb("nbfc-registered-sample.xlsx")) == "xlsx"
    assert nbfc.detect_format(rb("nbfc-cancelled-sample.html")) == "html"
    assert nbfc.detect_format(rb("nbfc-challenge.html")) == "challenge"


def test_nbfc_registered_xlsx():
    rows = nbfc.parse_nbfc(rb("nbfc-registered-sample.xlsx"), "registered", url="u")
    assert len(rows) == 3
    by_id = dict(rows)
    assert "B-13.00243" in by_id                           # CoR number is the record id
    bajaj = by_id["B-13.00243"]
    assert bajaj["name"] == "Bajaj Finance Limited"
    assert bajaj["regional_office"] == "Mumbai"
    assert "Deposit Taking" in bajaj["classification"]
    assert bajaj["cor_date"] == "05.03.1998"               # "Date of CoR" maps to cor_date, not the CoR number
    assert bajaj["kind"] == "registered"


def test_nbfc_cancelled_html_and_unescape():
    rows = nbfc.parse_nbfc(rb("nbfc-cancelled-sample.html"), "cancelled", url="u")
    assert len(rows) == 2
    by_id = dict(rows)
    assert by_id["B-05.02345"]["name"] == "Alpha Leasing & Finance Ltd."   # HTML entity decoded
    assert by_id["B-05.02345"]["cancelled_on"] == "12.08.2021"
    assert by_id["B-05.02345"]["kind"] == "cancelled"


def test_nbfc_challenge_raises():
    with pytest.raises(ValueError, match="challenge"):
        nbfc.parse_nbfc(rb("nbfc-challenge.html"), "registered")


def test_nbfc_unmapped_columns_kept():
    rows = nbfc.parse_nbfc(rb("nbfc-registered-sample.xlsx"), "registered", url="u")
    # 'Sr. No.' is not a canonical field, so it is preserved under 'extra'
    assert any("extra" in d and d["extra"] for _, d in rows)
