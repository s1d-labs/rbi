"""Shared helpers for the RBI family tasks.

The functions here are small and pure (no hub, no network) so the tests can use them. `fetch` is the one polite request
helper the tasks share: it reports the standard counters (n_http, t_http_s, http_<code>, http_error) the hub reads for
politeness and tracking, and marks blocks (403/429/503) and errors for the hub's automatic back-off.
"""
import re
import time

UA = "s1d-labs scraper (+https://github.com/s1d-labs)"

# RBI's document host (rbidocs.rbi.org.in) answers non-browser clients from some networks with a JavaScript bot
# challenge (an F5/Shape "TSPD" page), not the file. These markers detect that page so a task reports it instead of
# storing an empty record.
_CHALLENGE_MARKERS = (b"bobcmn", b"TSPD_101", b"challenge.support_id", b"something went wrong", b"Oops")


def is_challenge(content):
    """True when `content` (bytes) is RBI's bot-challenge page, not the real document."""
    if isinstance(content, str):
        content = content.encode("utf-8", "replace")
    head = content[:4000].lower()
    low = content.lower()
    if head.startswith(b"<!doctype html") or head.lstrip().startswith(b"<html"):
        if any(m.lower() in low for m in _CHALLENGE_MARKERS):
            return True
    return False


def rupees_to_inr(text):
    """Parse an RBI penalty amount phrase into (amount_text, amount_inr).

    Handles '₹1.00 lakh', '₹25 lakh', '₹5 crore' and plain Indian-grouped figures like '₹10,00,000'.
    Returns (None, None) when no amount is found.
    """
    m = re.search(r"penalty of\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*(crore|lakh)?", text, re.I)
    if not m:
        return None, None
    num = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    mult = {"crore": 10_000_000, "lakh": 100_000, "": 1}[unit]
    amount_text = m.group(0).split("penalty of", 1)[-1].strip()
    return amount_text, int(round(num * mult))


def slug(text, maxlen=70):
    """A stable, filename-safe slug of an entity name (used in record ids and upload paths)."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:maxlen] or "x"


def make_fetch(requests_module):
    """Return a `fetch(hub, session, url, **kw)` bound to a `requests` module.

    Kept as a factory so `rbicommon` imports cleanly without requests installed (the pure tests do not need it).
    """
    def fetch(hub, session, url, method="GET", data=None, timeout=40, binary=False):
        t = time.time()
        try:
            r = session.request(method, url, data=data, timeout=timeout,
                                 headers={"User-Agent": UA})
        except requests_module.RequestException:
            hub.metric("http_error")
            hub.error()
            return None
        hub.metric("n_http")
        hub.metric("t_http_s", time.time() - t)
        if r.status_code != 200:
            hub.metric(f"http_{r.status_code}")
            if r.status_code in (403, 429, 503):
                hub.block()
            return None
        if binary:
            return r.content
        if not r.encoding or r.encoding.lower() == "iso-8859-1":   # no charset declared: don't let requests assume Latin-1
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    return fetch
