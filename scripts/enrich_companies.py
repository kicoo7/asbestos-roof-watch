"""Company public record per business website: the VAT number the business publishes, its legal name and form,
confirmed against the EU VIES service.

Input: distinct websites from outputs/roof_occupants.csv (`website`), else outputs/contact_worksheet.csv
(`business_website`), cleaned with roofwatch.urls.public_website (messaging links and URLs carrying a phone number or an
e-mail address are dropped) and grouped by domain. Social networks, directories and map links are skipped (not_own_site).
Per domain: robots.txt, the home page and, when it shows no VAT number, up to 2 more same-domain pages (legal notes,
contacts, privacy...). A page reached through a redirect to another host is used only if that host's robots.txt allows it.
The best VAT number is checked on VIES (member state IT).
Polite: one request at a time per registrable domain (shop.x.it and x.it share one pace), >= 1 s apart or the site's
Crawl-delay up to 30 s (longer: home page only), at most 4 sites in parallel, VIES at most 1 request/s.
Cache: per domain a small record (data/raw/company_pages/<domain>/record.json: status, VAT, legal name, REA, source page,
dates) and VIES answers in data/raw/vies/<vat>.json, so a rerun makes no requests. Page bodies hold e-mails and phone
numbers: they are deleted once the record is written (--keep-pages keeps them, for debugging only).
--retry-failed crawls failed sites again and rechecks unanswered VIES lookups.
Suppression after an objection: outputs/company_suppress.csv (optional, hand-maintained, columns `domain` and/or `vat`). A
listed domain (or a subdomain of one), and any domain showing a listed VAT number or a listed domain's VAT number, gets a
`suppressed` row with every identity column empty, in every run while it is listed, whether or not its listing is still in
the input. Its record shrinks to a tag-only record (no VAT number, name or URL), its VIES answer is deleted, and it is not
fetched or looked up again while listed.
Collects only the output columns: no phones, emails, PEC addresses or names of people. For sole traders the VIES
address (possibly a home) is neither written nor cached.
-> outputs/company_register.csv (private, git-ignored)
"""
import argparse
import codecs
import csv
import hashlib
import http.client
import json
import os
import queue
import re
import shutil
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.company import (VIES_TRANSIENT, VIES_URL, entity_type, extract_legal_name, extract_vat_candidates, legal_form, page_text,
                               parse_vies, rea_number, select_pages)
from roofwatch.occupants import read_table
from roofwatch.urls import normalize_domain, public_website

UA = "roofwatch-prototype (research)"
TIMEOUT, MAX_BYTES, GAP, MAX_DELAY, WORKERS, RETRIES, EXTRA_PAGES = 15, 1_500_000, 1.0, 30.0, 4, 2, 2
PAGES, VIES_DIR = ROOT / "data/raw/company_pages", ROOT / "data/raw/vies"
RECORD = "record.json"
OUT = ROOT / "outputs/company_register.csv"
SUPPRESS = "outputs/company_suppress.csv"  # under ROOT; optional, written by hand after an objection
COLS = ["domain", "website", "status", "vat", "legal_name", "legal_form", "entity_type", "registered_office", "vies_valid",
        "vies_checked_on", "rea", "source_url", "fetched_on", "tls_unverified"]
# hosts that are not the business's own site: social networks, directories, maps, link-in-bio pages.
# Google Business Profile sites (*.business.site) were shut down in 2024 and now redirect to Google Maps.
NOT_OWN = {"facebook.com", "fb.com", "fb.me", "instagram.com", "linkedin.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
           "tiktok.com", "goo.gl", "g.page", "g.co", "business.site", "paginegialle.it", "paginebianche.it", "wikipedia.org", "linktr.ee",
           "linkin.bio", "wa.me", "whatsapp.com", "t.me", "telegram.me", "subito.it", "tuttocitta.it", "virgilio.it", "misterimprese.it",
           "reteimprese.it", "kompass.com", "europages.it", "infobel.com", "cylex-italia.it", "prontopro.it", "hotfrog.it", "trovaprezzi.it",
           "etsy.com", "bit.ly", "about.me", "carrd.co", "beacons.ai"}
NOT_OWN_LABEL = re.compile(r"(?:^|\.)(?:google|tripadvisor|booking|facebook|instagram|pinterest|yelp|amazon|ebay|airbnb|wikipedia|"
                           r"thefork|justeat|deliveroo|glovoapp)\.[a-z]{2,3}(?:\.[a-z]{2})?$")
# second-level labels under which the registrable domain has three labels (x.co.uk, comune.brescia.bs.it is under bs.it)
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "gv"}
_lock = threading.Lock()
n_requests = 0  # real network requests made by this run (0 on a fully cached rerun)


def not_own(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    if host == "sites.google.com":  # a site builder, like wixsite.com: the business's own pages
        return False
    return any(host == d or host.endswith("." + d) for d in NOT_OWN) or bool(NOT_OWN_LABEL.search(host))


def _write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---------------------------------------------------------------- pacing

def site_key(host: str | None) -> str:
    """Registrable domain, by a simple rule: 'shop.rossi.it' and 'www.rossi.it' -> 'rossi.it'; 'x.co.uk' stays whole.
    Italian two-letter province zones count as a second level: 'comune.brescia.bs.it' -> 'brescia.bs.it'."""
    labels = (host or "").lower().rstrip(".").split(".")
    second = labels[-2] if len(labels) >= 3 else ""
    three = second in _SECOND_LEVEL or (labels[-1] == "it" and len(second) == 2 and second.isalpha())
    return ".".join(labels[-3:] if three else labels[-2:])


class Pace:
    """One request at a time, at least `gap` seconds from the end of one to the start of the next. Thread-safe:
    `with pace:` around each request. Shared by every host of a registrable domain (see pace_for)."""

    def __init__(self, gap=GAP):
        self.gap, self.last, self._lock = gap, 0.0, threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        d = self.last + self.gap - time.monotonic()
        if d > 0:
            time.sleep(d)
        return self

    def __exit__(self, *exc):
        self.last = time.monotonic()
        self._lock.release()
        return False


_paces: dict[str, Pace] = {}


def pace_for(url_or_host: str) -> Pace:
    host = urlsplit(url_or_host).hostname if "://" in url_or_host else url_or_host
    with _lock:
        return _paces.setdefault(site_key(host), Pace())


# ---------------------------------------------------------------- HTTP

def _reason(e) -> str:
    if isinstance(e, ssl.SSLCertVerificationError):
        return f"tls certificate: {e.verify_message}"
    if isinstance(e, ssl.SSLError):
        return f"tls: {e.reason or e}"
    if isinstance(e, socket.gaierror):
        return "dns"
    if isinstance(e, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(e, ConnectionRefusedError):
        return "connection refused"
    if isinstance(e, (ConnectionResetError, http.client.RemoteDisconnected, ConnectionAbortedError)):
        return "connection reset"
    if isinstance(e, str):
        return e[:80]
    return f"{type(e).__name__}: {str(e)[:80]}"


def _decompress(body: bytes, enc: str) -> bytes:
    """Bounded: at most MAX_BYTES out whatever the compression ratio, so a gzip bomb stays small. Copes with a truncated body."""
    if enc in ("gzip", "x-gzip"):
        modes = [16 + zlib.MAX_WBITS]
    elif enc == "deflate":
        modes = [zlib.MAX_WBITS, -zlib.MAX_WBITS]  # zlib-wrapped, or raw deflate as some servers send it
    else:
        return body
    for wbits in modes:
        try:
            return zlib.decompressobj(wbits).decompress(body, MAX_BYTES)
        except zlib.error:
            continue
    return b""


def _ascii_url(url: str) -> str:
    """Links can hold accents, spaces or an IDN host; http.client wants plain ASCII."""
    p = urlsplit(url)
    host = p.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    netloc = host + (f":{p.port}" if p.port else "")
    return urlunsplit((p.scheme, netloc, quote(p.path, safe="/%:@&=+$,;~!*'()"), quote(p.query, safe="=&%+/:;,?@!$'()*~"), ""))


def http_get(url: str, insecure: bool = False) -> tuple[dict, bytes]:
    """One GET with redirects. Never raises: errors are reported in meta['error']."""
    global n_requests
    with _lock:
        n_requests += 1
    ctx = ssl.create_default_context()
    if insecure:  # fallback for expired / self-signed certificates and old TLS: public pages only, nothing is sent
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    req = urllib.request.Request(_ascii_url(url), headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                               "Accept-Encoding": "identity"})
    meta = {"url": url, "final_url": url, "status": None, "content_type": "", "error": None, "truncated": False, "retry_after": None}
    body = b""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            meta.update(final_url=r.geturl(), status=r.status, content_type=r.headers.get("Content-Type", "") or "")
            deadline = time.monotonic() + 4 * TIMEOUT  # a server trickling bytes cannot hold a worker for long
            while len(body) <= MAX_BYTES and time.monotonic() < deadline:
                chunk = r.read(65536)
                if not chunk:
                    break
                body += chunk
            meta["truncated"] = len(body) > MAX_BYTES or time.monotonic() >= deadline
            body = _decompress(body[:MAX_BYTES], (r.headers.get("Content-Encoding") or "").lower().strip())
    except urllib.error.HTTPError as e:
        meta.update(status=e.code, error=f"http {e.code}", final_url=e.geturl() or url,
                    retry_after=(e.headers or {}).get("Retry-After"))
    except urllib.error.URLError as e:
        meta["error"] = _reason(e.reason)
    except Exception as e:  # timeouts during read, resets, malformed responses
        meta["error"] = _reason(e)
    return meta, body


def _transient(meta) -> bool:
    return meta["status"] in (429, 500, 502, 503, 504) or meta["error"] in ("timeout", "connection reset")


def fetch(url: str, pace: Pace) -> tuple[dict, bytes]:
    """GET with 2 retries on transient errors (honouring Retry-After up to 30 s) and one retry without certificate checks
    on TLS errors. meta['tls_unverified'] says the answer was read without certificate verification."""
    insecure, attempt = False, 0
    while True:
        with pace:
            meta, body = http_get(url, insecure)
        if meta["error"] and meta["error"].startswith("tls") and not insecure:
            insecure = True
            continue
        if _transient(meta) and attempt < RETRIES:
            attempt += 1
            ra = meta.get("retry_after")
            time.sleep(min(30, int(ra)) if ra and str(ra).isdigit() else 2 * attempt)
            continue
        meta["tls_unverified"] = insecure and not meta["error"]
        return meta, body


def get_page(domain: str, url: str, retry_failed: bool) -> tuple[dict, bytes]:
    """Cached GET while a domain is being crawled: <domain>/<sha1(url)>.json (+ .body). Failures are cached too.
    These files are temporary: purge_pages() removes them once the domain's record is written."""
    k = hashlib.sha1(url.encode()).hexdigest()[:16]
    mf, bf = PAGES / domain / f"{k}.json", PAGES / domain / f"{k}.body"
    if mf.exists():
        meta = json.loads(mf.read_text())
        if not (retry_failed and meta["error"]):
            return meta, (bf.read_bytes() if bf.exists() else b"")
    meta, body = fetch(url, pace_for(url))
    meta["fetched_on"] = date.today().isoformat()
    if not meta["error"]:
        _write_atomic(bf, body)
    _write_atomic(mf, json.dumps(meta, indent=1).encode())  # written last: marks the cache entry complete
    return meta, body


def decode(body: bytes, content_type: str) -> str:
    """Declared charset (header, then <meta>), then UTF-8, then Windows-1252: Italian sites mislabel latin-1 often."""
    if body.startswith(codecs.BOM_UTF8):
        return body[3:].decode("utf-8", "replace")
    tries = re.findall(r"charset=[\"']?([\w\-:.]+)", content_type or "", re.I)
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w\-:.]+)", body[:4096], re.I)
    if m:
        tries.append(m.group(1).decode("ascii", "ignore"))
    for cs in tries + ["utf-8"]:
        try:
            return body.decode(cs)
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode("cp1252", "replace")


def is_challenge(meta: dict, body: bytes) -> bool:
    """A bot-protection page (SiteGround answers 202 with a captcha redirect) instead of the site: we do not solve those."""
    return meta.get("status") == 202 or (len(body) < 5000 and bool(re.search(
        rb"sgcaptcha|cf-chl-|/cdn-cgi/challenge-platform/h/|_incapsula_resource|ddos-guard", body, re.I)))


def is_html(meta: dict, body: bytes) -> bool:
    ct = (meta.get("content_type") or "").lower()
    if "html" in ct:
        return True
    if ct and not ct.startswith(("text/plain", "application/octet-stream")):
        return False  # pdf, images, json...
    head = body[:2048].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html")) or b"<body" in head


# ---------------------------------------------------------------- robots.txt

def robots_ok(domain: str, url: str, robots: dict, retry_failed: bool) -> tuple[bool | None, str, str]:
    """(allowed, error, fetched_on) for `url`, per host, following RFC 9309: 4xx -> allowed; 5xx, unreachable or 429
    (after backing off) -> no crawling (None = could not tell). A Crawl-delay slows the site's pace, up to 30 s."""
    p = urlsplit(url)
    host = p.hostname or ""
    if host not in robots:
        meta, body = get_page(domain, f"{p.scheme}://{p.netloc}/robots.txt", retry_failed)
        rp, why, delay = RobotFileParser(), "", 0.0
        st = meta["status"]
        if not meta["error"]:
            rp.parse(decode(body, meta["content_type"]).splitlines())
            try:
                delay = float(rp.crawl_delay(UA) or 0)
            except (TypeError, ValueError):
                delay = 0.0
            if delay:
                pc = pace_for(host)
                pc.gap = max(pc.gap, GAP, min(delay, MAX_DELAY))
        elif st == 429:  # "slow down": not a licence to crawl
            rp, why = None, "robots.txt http 429"
        elif st and 400 <= st < 500:
            rp.allow_all = True
        else:
            rp, why = None, meta["error"]
        robots[host] = (rp, why, meta["fetched_on"], delay)
    rp, why, on, _ = robots[host]
    if rp is None:
        return None, why, on
    return rp.can_fetch(UA, url), "", on


def crawl_delay(robots: dict, url: str) -> float:
    return robots.get(urlsplit(url).hostname or "", (None, "", "", 0.0))[3]


def _redirect_ok(domain: str, meta: dict, robots: dict, retry_failed: bool) -> tuple[bool | None, str]:
    """A page that redirected to another host may be used only if that host's robots.txt allows it."""
    if not meta or meta.get("error") or "url" not in meta:
        return True, ""
    if (urlsplit(meta["url"]).hostname or "") == (urlsplit(meta["final_url"]).hostname or ""):
        return True, ""
    ok, why, _ = robots_ok(domain, meta["final_url"], robots, retry_failed)
    return ok, why


# ---------------------------------------------------------------- one domain

def _home(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc.lower()}/"


def _variants(home: str, err: str) -> list[str]:
    """Another way into a site whose home page failed: the other scheme, or the host with/without 'www.' on DNS errors."""
    p = urlsplit(home)
    if err == "dns":
        h = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
        return [f"{p.scheme}://{h}/"]
    if err == "connection refused" or (err or "").startswith("tls"):  # not after timeouts: the host is usually down
        return [f"{'http' if p.scheme == 'https' else 'https'}://{p.netloc}/"]
    return []


def _get_allowed(domain, url, robots, retry_failed) -> tuple[bool, dict, bytes]:
    """(disallowed by robots.txt, meta, body). An unreachable robots.txt means no fetch (RFC 9309) and counts as a failure."""
    ok, why, on = robots_ok(domain, url, robots, retry_failed)
    if ok is False:
        return True, {}, b""
    if ok is None:
        return False, {"error": why, "fetched_on": on}, b""
    return (False, *get_page(domain, url, retry_failed))


def _shown_url(url: str) -> str:
    """The URL kept in records and the register: public_website's cleaning, else the site's root, else nothing."""
    return public_website(url) or public_website(_home(url)) or ""


def crawl(domain: str, website: str, retry_failed: bool) -> dict:
    rec = {"domain": domain, "website": website, "status": "", "vat": "", "candidates": [], "page_name": None, "rea": "",
           "source_url": "", "fetched_on": "", "reason": "", "tls_unverified": False, "pages": []}
    if not_own(urlsplit(website).hostname):
        return {**rec, "status": "not_own_site"}
    robots = {}
    home = _home(website)
    blocked, meta, body = _get_allowed(domain, home, robots, retry_failed)
    if not blocked and meta["error"]:
        for alt in _variants(home, meta["error"]):
            blocked, m2, b2 = _get_allowed(domain, alt, robots, retry_failed)
            if not blocked and not m2["error"]:
                meta, body = m2, b2
    if blocked:
        return {**rec, "status": "robots_disallowed"}
    rec["fetched_on"] = meta.get("fetched_on", "")
    if meta["error"]:
        return {**rec, "status": "fetch_failed", "reason": meta["error"]}
    rec["pages"] = [_shown_url(meta["final_url"])]
    if not_own(urlsplit(meta["final_url"]).hostname):
        return {**rec, "status": "not_own_site", "reason": "redirects to " + (urlsplit(meta["final_url"]).hostname or "")}
    ok, why = _redirect_ok(domain, meta, robots, retry_failed)
    if ok is False:
        return {**rec, "status": "robots_disallowed", "reason": "redirect target disallows"}
    if ok is None:
        return {**rec, "status": "fetch_failed", "reason": f"redirect target {why}"}
    if is_challenge(meta, body):
        return {**rec, "status": "fetch_failed", "reason": "bot challenge"}
    if not is_html(meta, body):
        return {**rec, "status": "fetch_failed", "reason": f"not html ({meta['content_type'].split(';')[0] or 'unknown'})"}
    html = decode(body, meta["content_type"])
    pages, extra, agency_only = [(meta, html)], None, False
    if crawl_delay(robots, meta["final_url"]) > MAX_DELAY:  # a very slow pace asked for: the home page only
        extra = []
    for m, h in pages:  # grows below: the home page, then likely pages one at a time while no VAT is found
        cands = extract_vat_candidates(h)
        good = [c for c in cands if not c.credit]
        agency_only |= bool(cands) and not good
        if good:
            c, text = good[0], page_text(h)
            return {**rec, "status": "ok", "vat": c.vat, "candidates": [[x.vat, x.strength, x.count, x.credit] for x in cands],
                    "page_name": extract_legal_name(text, c.pos), "rea": rea_number(text[max(0, c.pos - 300):c.pos + 300]) or "",
                    "source_url": _shown_url(m["final_url"]), "fetched_on": m["fetched_on"], "tls_unverified": bool(m.get("tls_unverified"))}
        extra = select_pages(meta["final_url"], html, EXTRA_PAGES) if extra is None else extra
        while extra:
            u = extra.pop(0)
            if robots_ok(domain, u, robots, retry_failed)[0]:
                m2, b2 = get_page(domain, u, retry_failed)
                rec["pages"].append(_shown_url(m2.get("final_url", u)))
                if (not m2["error"] and is_html(m2, b2) and not is_challenge(m2, b2)
                        and _redirect_ok(domain, m2, robots, retry_failed)[0]):
                    pages.append((m2, decode(b2, m2["content_type"])))
                    break
    why = f"only a web-agency VAT ({len(pages)} pages)" if agency_only else f"no VAT on {len(pages)} pages"
    if crawl_delay(robots, meta["final_url"]) > MAX_DELAY:
        why += f" (crawl-delay over {MAX_DELAY:.0f} s: home page only)"
    return {**rec, "status": "no_vat_found", "reason": why}


# ---------------------------------------------------------------- per-domain record cache

_RECORD_KEYS = ("status", "reason", "vat", "candidates", "page_name", "rea", "source_url", "fetched_on", "tls_unverified", "pages")


def purge_pages(domain: str) -> int:
    """Delete a domain's page files (bodies hold e-mails and phone numbers), keeping its record. -> bytes freed."""
    freed = 0
    d = PAGES / domain
    if d.is_dir():
        for f in d.iterdir():
            if f.name != RECORD and f.is_file():
                freed += f.stat().st_size
                f.unlink()
    return freed


def _vat_tag(vat: str) -> str:
    """What a suppressed domain's record keeps of the suppressing VAT number: a tag, so the suppression lifts when the
    number leaves the list. It adds nothing to the list itself, which holds the number."""
    return hashlib.sha256(f"roofwatch-suppress:{vat}".encode()).hexdigest()[:16]


def suppressed_row(domain: str) -> dict:
    return {k: "" for k in _RECORD_KEYS} | {"domain": domain, "website": "", "status": "suppressed", "page_name": None,
                                            "candidates": [], "pages": [], "tls_unverified": False}


def domain_record(domain: str, website: str, retry_failed: bool = False, keep_pages: bool = False,
                  tags: frozenset = frozenset()) -> dict:
    """The crawl result for one domain: from its record when there is one (no network), else crawled and recorded.
    A domain showing a suppressed VAT number (its tag is in `tags`) keeps only a tag-only 'suppressed' record."""
    if not_own(urlsplit(website).hostname):
        return crawl(domain, website, retry_failed)  # no request, nothing to record
    rf = PAGES / domain / RECORD
    rec = json.loads(rf.read_text()) if rf.exists() else None
    if rec is not None and rec["status"] == "suppressed" and rec.get("vat_tag") in tags:
        return suppressed_row(domain)
    if rec is None or rec["status"] == "suppressed" or (retry_failed and rec["status"] == "fetch_failed"):
        rec = {k: v for k, v in crawl(domain, website, retry_failed).items() if k in _RECORD_KEYS}
        _write_atomic(rf, json.dumps({"domain": domain, **rec}, indent=1, ensure_ascii=False).encode())
    hit = bool(rec.get("vat")) and _vat_tag(rec["vat"]) in tags
    if not keep_pages or hit:
        purge_pages(domain)
    if hit:  # forget what was read: the record keeps only that the domain is suppressed
        _write_atomic(rf, _suppressed_record(_vat_tag(rec["vat"])))
        return suppressed_row(domain)
    return {**{k: rec.get(k) for k in _RECORD_KEYS}, "domain": domain, "website": website}


def _suppressed_record(tag: str) -> bytes:
    return json.dumps({"status": "suppressed", "vat_tag": tag}, indent=1).encode()


def is_suppressed(domain: str, sdom) -> bool:
    """On the suppression list, or a subdomain of a listed domain: hop.example covers shop.hop.example."""
    return any(domain == s or domain.endswith("." + s) for s in sdom)


def apply_suppression(sdom, svat) -> tuple[set[str], set[str]]:
    """Bring every cached domain in line with the suppression list, listed in this run's input or not.
    -> (suppressed domains, tags of the suppressed VAT numbers).

    A domain on the list (or under one), and a domain whose record shows a suppressed VAT number, keep a tag-only record
    (no VAT number, name or URL): they stay known as suppressed for as long as the list says so, even after their listing
    has left roof_occupants.csv. A listed domain's own VAT number counts as suppressed too (the same person's other
    websites); its tag carries that across runs. A tag-only record that no longer matches the list is removed, and the
    domain is read again if it is listed."""
    tags = {_vat_tag(v) for v in svat}
    folders = sorted(d for d in PAGES.iterdir() if d.is_dir()) if PAGES.is_dir() else []
    recs = {d.name: json.loads((d / RECORD).read_text()) if (d / RECORD).exists() else {} for d in folders}
    tag_of = {n: _vat_tag(r["vat"]) if r.get("vat") else r.get("vat_tag", "") for n, r in recs.items()}
    tags |= {tag_of[n] for n in recs if tag_of[n] and is_suppressed(n, sdom)}
    gone = set()
    for n, r in recs.items():
        if is_suppressed(n, sdom) or tag_of[n] in tags:
            if r != {"status": "suppressed", "vat_tag": tag_of[n]}:
                purge_pages(n)
                _write_atomic(PAGES / n / RECORD, _suppressed_record(tag_of[n]))
            gone.add(n)
        elif r.get("status") == "suppressed":  # taken off the list
            shutil.rmtree(PAGES / n)
    return gone | set(sdom), tags


def drop_vies(tags: set[str]):
    """Delete the cached VIES answers of suppressed VAT numbers."""
    if tags and VIES_DIR.is_dir():
        for f in VIES_DIR.glob("*.json"):
            if _vat_tag(f.stem) in tags:
                f.unlink()


def crawl_safe(item, retry_failed=False, keep_pages=False, tags=frozenset()):
    try:
        return domain_record(*item, retry_failed, keep_pages, tags)
    except Exception as e:  # one odd site must not stop the run; nothing is recorded, so the next run tries again
        traceback.print_exc(limit=3)
        if not keep_pages:
            purge_pages(item[0])  # the pages fetched before the error hold e-mails and phone numbers too
        return {"domain": item[0], "website": item[1], "status": "fetch_failed", "vat": "", "page_name": None, "rea": "",
                "source_url": "", "fetched_on": "", "reason": f"error {type(e).__name__}", "tls_unverified": False}


def tidy_cache(keep_pages: bool = False) -> tuple[int, int]:
    """Drop page files left in any domain folder (they hold contact details) and the folders left empty, unless
    --keep-pages; always drop whole folders whose name would fail public_website (a phone-like digit run).
    -> (folders removed, bytes freed)."""
    removed = freed = 0
    if not PAGES.is_dir():
        return 0, 0
    for d in sorted(PAGES.iterdir()):
        if not d.is_dir():
            continue
        if public_website(f"http://{d.name}/") is None:
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d)
            removed += 1
        elif not keep_pages:
            freed += purge_pages(d.name)
            if not any(d.iterdir()):  # nothing recorded for it: no folder either
                d.rmdir()
                removed += 1
    return removed, freed


# ---------------------------------------------------------------- VIES

_KEEP = ("isValid", "valid", "userError", "name", "address", "requestDate", "vatNumber", "actionSucceed", "errorWrappers")


def vies_lookup(vat: str, pace: Pace, retry_failed: bool, live: bool = True) -> tuple[dict | None, bool]:
    """Cached VIES check -> ({'checked_on', 'http_status', 'payload'} or None, asked the service now).

    Backs off on MS_UNAVAILABLE, rate limits and 5xx. With live=False only the cache is read, and a cached answer is
    returned even when it would otherwise be refreshed.
    """
    f = VIES_DIR / f"{vat}.json"
    if f.exists():
        rec = json.loads(f.read_text())
        v = parse_vies(rec["payload"])
        # ask again only for failures (on request), or when the name now reads as a company but its address was dropped
        stale = v["valid"] and "address" not in rec["payload"] and entity_type(legal_form(v["name"])) == "company"
        if not live or not ((retry_failed and v["valid"] is None) or stale):
            return rec, False
    if not live:
        return None, False
    payload, status = None, None
    for attempt, backoff in enumerate((5, 20, 60, None)):
        with pace:
            global n_requests
            with _lock:
                n_requests += 1
            req = urllib.request.Request(VIES_URL.format(vat), headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    status, payload = r.status, json.loads(r.read(200_000).decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    payload = json.loads(e.read(200_000).decode("utf-8", "replace"))
                except Exception:
                    payload = None
            except Exception:
                status, payload = None, None
        p = parse_vies(payload)
        retry = p["valid"] is None and (p["error"] in VIES_TRANSIENT or status in (None, 429, 500, 502, 503, 504) or p["error"] == "no answer")
        if not retry or backoff is None:
            break
        time.sleep(backoff)
    kept = {k: payload[k] for k in _KEEP if isinstance(payload, dict) and k in payload}
    if entity_type(legal_form(parse_vies(kept)["name"])) != "company":
        kept.pop("address", None)  # a sole trader's VIES address can be a home address: do not keep it
    rec = {"checked_on": date.today().isoformat(), "http_status": status, "payload": kept}
    _write_atomic(f, json.dumps(rec, indent=1, ensure_ascii=False).encode())
    return rec, True


def vies_worker(q: queue.Queue, out: dict, retry_failed: bool):
    """Checks VAT numbers as the crawl finds them, one request per second. After 5 live checks in a row without an
    answer, VIES (or the Italian service) is taken to be down: the rest is read from the cache only, for the next run."""
    pace, misses = Pace(GAP), 0
    while (vat := q.get()) is not None:
        if vat in out:
            continue
        rec, asked = vies_lookup(vat, pace, retry_failed, live=misses < 5)
        out[vat] = rec
        if asked:
            misses = misses + 1 if parse_vies(rec["payload"])["valid"] is None else 0
            if misses == 5:
                print("VIES gives no answers: no more live VIES checks in this run", flush=True)


# ---------------------------------------------------------------- register

def group_websites(urls) -> list[tuple[str, str]]:
    """(domain, website) per domain, sorted. Websites pass roofwatch.urls.public_website first: a messaging link or a URL
    carrying a phone number or e-mail address is not a site and is dropped here, so it is never fetched or written.
    The representative URL is the most listed, then the shortest, then the first alphabetically (deterministic)."""
    by_dom: dict[str, Counter] = {}
    for u in urls:
        u = public_website(u.strip()) if isinstance(u, str) else None
        d = normalize_domain(u)
        if u and d:
            by_dom.setdefault(d, Counter())[u] += 1
    return sorted((d, min(c, key=lambda u: (-c[u], len(u), u))) for d, c in by_dom.items())


def _vat_key(v) -> str | None:
    """'IT 012 345 60173', '01234560173' or '12345678' (a spreadsheet dropped leading zeros) -> 11 digits, else None."""
    s = re.sub(r"[\s.\-]", "", str(v or "")).upper().removeprefix("IT")
    return s.zfill(11) if s.isdigit() and len(s) <= 11 else None


def load_suppression(path: Path | None = None) -> tuple[frozenset, frozenset]:
    """(domains, VAT numbers) to suppress, from outputs/company_suppress.csv: columns `domain` and/or `vat`, read like the
    worksheet (',' or ';', UTF-8 with or without BOM, or Windows-1252). A missing or empty file suppresses nothing. A file
    with neither column, or a non-empty cell that does not read as a domain or a VAT number, stops the run before anything
    is fetched or written: ignoring it would quietly undo an objection."""
    path = ROOT / SUPPRESS if path is None else Path(path)
    df = read_table(path)
    if df is None:
        return frozenset(), frozenset()
    cols = {str(c).strip().lower(): c for c in df.columns}
    if "domain" not in cols and "vat" not in cols:
        raise ValueError(f"{path.name} needs a 'domain' and/or 'vat' column (header read as: {', '.join(map(str, df.columns[:4]))}). "
                         f"Nothing was written.")
    found, bad = {"domain": set(), "vat": set()}, []
    for col, parse in (("domain", normalize_domain), ("vat", _vat_key)):
        for line, v in enumerate(df[cols[col]] if col in cols else [], start=2):  # line 1 is the header
            v = str(v).strip()
            if v and parse(v):
                found[col].add(parse(v))
            elif v:
                bad.append(f"line {line}, {col} {v!r}")
    if bad:
        raise ValueError(f"{path.name}: cannot read {'; '.join(bad)} (a domain like rossi.it, or a VAT number of up to 11 digits). "
                         f"Fix or clear those cells and run again. Nothing was written.")
    return frozenset(found["domain"]), frozenset(found["vat"])


def load_websites() -> tuple[Path, list[tuple[str, str]]]:
    occ, ws = ROOT / "outputs/roof_occupants.csv", ROOT / "outputs/contact_worksheet.csv"
    src, col = (occ, "website") if occ.exists() else (ws, "business_website")
    return src, group_websites(pd.read_csv(src, dtype=str, usecols=lambda c: c == col)[col].dropna())


def register_row(r: dict, vies_rec: dict | None) -> dict:
    """One register row from a domain's crawl result and its VIES record.

    legal_name is the VIES name when VIES confirms the number, else the name printed on the page. registered_office is
    the VIES address, only for a VIES-confirmed company (a sole trader's address can be a home). vies_checked_on only
    when VIES answered. tls_unverified when the source page was read without certificate verification.
    """
    if r.get("status") == "suppressed":  # after an objection: nothing but the domain and the status
        return {c: "" for c in COLS} | {"domain": r["domain"], "status": "suppressed"}
    row = {c: "" for c in COLS} | {k: r.get(k) or "" for k in ("domain", "website", "status", "vat", "rea", "source_url", "fetched_on")}
    if r.get("vat"):
        v = parse_vies(vies_rec["payload"]) if vies_rec else {"valid": None, "name": None, "address": None}
        name = v["name"] if v["valid"] and v["name"] else r.get("page_name")
        form = legal_form(name)
        et = entity_type(form)
        row.update(legal_name=name or "", legal_form=form, entity_type=et,
                   registered_office=(v["address"] or "") if v["valid"] and et == "company" else "",
                   vies_valid={True: "true", False: "false"}.get(v["valid"], ""),
                   vies_checked_on=vies_rec["checked_on"] if vies_rec and v["valid"] is not None else "",
                   tls_unverified="true" if r.get("tls_unverified") and r.get("source_url") else "")
    return row


def write_register(rows: list[dict], path: Path = OUT):
    """Atomic write (temp file, then replace), rows sorted by domain."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["domain"]))
    os.replace(tmp, path)


def run(items: list[tuple[str, str]], retry_failed: bool = False, keep_pages: bool = False, progress: bool = False,
        suppress: tuple[frozenset, frozenset] = (frozenset(), frozenset())):
    """Crawl (or read the records of) every domain, check VAT numbers on VIES alongside. -> (register rows, crawl results).
    Every suppressed domain gets a 'suppressed' row, in this run's input or not (see apply_suppression); none is crawled,
    and suppressed VAT numbers are never looked up and lose their cached VIES answers."""
    sdom, svat = suppress
    gone, tags = apply_suppression(sdom, svat)
    gone |= {d for d, _ in items if is_suppressed(d, sdom)}
    todo = [it for it in items if it[0] not in gone]
    recs, vies, q = [suppressed_row(d) for d in sorted(gone)], {}, queue.Queue()
    vt = threading.Thread(target=vies_worker, args=(q, vies, retry_failed), daemon=True)  # VIES is another host: runs alongside
    vt.start()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:  # each domain is crawled by one worker; requests are paced per site
        for i, r in enumerate(ex.map(lambda it: crawl_safe(it, retry_failed, keep_pages, tags), todo), 1):
            recs.append(r)
            if r["vat"]:
                q.put(r["vat"])
            if progress and i % 100 == 0:
                print(f"  {i}/{len(todo)} domains, {n_requests} requests", flush=True)
    q.put(None)
    vt.join()
    drop_vies(tags)
    return [register_row(r, vies.get(r["vat"]) if r["vat"] else None) for r in recs], recs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--retry-failed", action="store_true", help="crawl failed sites again and recheck unanswered VIES lookups")
    ap.add_argument("--keep-pages", action="store_true", help="keep raw page bodies in the cache (debugging; they hold contact details)")
    ap.add_argument("--limit", type=int, help="first N domains only, a trial: fills the caches, does not write the register")
    a = ap.parse_args()
    src, items = load_websites()
    suppress = load_suppression()  # before anything is fetched: a bad file stops the run here
    items = items[:a.limit] if a.limit else items
    print(f"{len(items)} domains from {src.relative_to(ROOT)} | suppression list: {len(suppress[0])} domains, "
          f"{len(suppress[1])} VAT numbers", flush=True)
    rows, recs = run(items, a.retry_failed, a.keep_pages, progress=True, suppress=suppress)
    if not a.limit:
        removed, freed = tidy_cache(a.keep_pages)
        print(f"cache tidied: {removed} folders removed, {freed} bytes of leftover page files freed", flush=True)
    if not a.limit:  # a trial must not replace the full register with a partial one
        write_register(rows)

    df = pd.DataFrame(rows, columns=COLS)
    hv = df[df.vat != ""]
    print(f"\n{OUT.relative_to(ROOT) if not a.limit else 'trial, nothing written'}: {len(df)} domains | network requests this run: {n_requests}")
    print("status:", df.status.value_counts().to_dict())
    print(f"with VAT: {len(hv)} | VIES: {hv.vies_valid.replace('', 'no answer').value_counts().to_dict()}")
    print("entity_type:", hv.entity_type.value_counts().to_dict())
    print("legal_form:", hv.legal_form.value_counts().to_dict())
    print("with legal name:", int((hv.legal_name != "").sum()), "| with REA:", int((hv.rea != "").sum()),
          "| source page read without TLS verification:", int((df.tls_unverified == "true").sum()))
    reasons = Counter(r["reason"].split(":")[0] if r["status"] == "fetch_failed" else r["reason"].split(" (")[0]
                      for r in recs if r.get("reason"))
    print("reasons:", dict(reasons.most_common()))


if __name__ == "__main__":
    main()
