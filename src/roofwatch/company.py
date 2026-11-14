"""Company public record: the legal identity a business publishes on its own website, checked against EU VIES.

Italian businesses must show their VAT number (Partita IVA) on their website. This module finds it in a page,
with the legal name and form printed next to it, and normalises the answer of the EU VIES service.
Pure functions: network access lives in scripts/enrich_companies.py. Phones, emails and PEC addresses are
never extracted, and owner or director names are never looked up (see docs/CONTACTING.md).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from roofwatch.urls import normalize_domain

VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/IT/vat/{}"
# VIES userError values meaning "no answer" (not "invalid"): retry later, never report as false
VIES_TRANSIENT = {"MS_UNAVAILABLE", "MS_MAX_CONCURRENT_REQ", "GLOBAL_MAX_CONCURRENT_REQ", "SERVICE_UNAVAILABLE", "TIMEOUT",
                  "GLOBAL_MAX_CONCURRENT_REQ_TIME", "MS_MAX_CONCURRENT_REQ_TIME"}


# ---------------------------------------------------------------- Partita IVA

def valid_partita_iva(s) -> bool:
    """True for an 11-digit Partita IVA with a correct check digit (Luhn) and a plausible office code.

    Rejects all-zero serials, repeated digits and office codes outside 001-121, 888, 999 (so 12345678903 fails).
    Also rejects serials from 8000000: those are fiscal codes (codice fiscale) of bodies without a VAT number,
    e.g. associations. Only plain digits: strip 'IT', spaces and dots before calling.
    """
    if not isinstance(s, str) or not re.fullmatch(r"\d{11}", s):
        return False
    if s[:7] == "0000000" or s[0] in "89" or len(set(s)) == 1:
        return False
    office = int(s[7:10])
    if not (1 <= office <= 121 or office in (888, 999)):
        return False
    total = 0
    for i, ch in enumerate(s[:10]):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return (10 - total % 10) % 10 == int(s[10])


# ---------------------------------------------------------------- HTML -> text

_SKIP = {"script", "style", "template", "svg", "iframe", "object", "noscript"}  # not <head>: </head> is optional in HTML
_INLINE = {"a", "abbr", "b", "bdi", "bdo", "cite", "code", "em", "font", "i", "kbd", "label", "mark", "q", "s", "small",
           "span", "strong", "sub", "sup", "time", "u", "var", "wbr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.ldjson, self._ld = [], 0, [], None

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
            if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
                self._ld = []
        elif tag not in _INLINE:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag not in _INLINE and tag not in _SKIP:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            if tag == "script" and self._ld is not None:
                self.ldjson.append("".join(self._ld))
                self._ld = None
        elif tag not in _INLINE:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif not self.skip:
            self.parts.append(data)


def _squeeze(t: str) -> str:
    t = t.replace("\xa0", " ").replace("​", "").replace("\r", "\n")
    t = re.sub(r"[ \t\f\v]+", " ", t)
    return re.sub(r" ?\n[\n ]*", "\n", t).strip()


def page_text(html: str) -> str:
    """Visible text of a page: scripts, styles and comments dropped, entities decoded, whitespace collapsed.

    A schema.org vatID / taxID found in JSON-LD is appended as a 'P.IVA ...' line (with its legalName), because
    some sites publish their VAT number only there.
    """
    p = _Text()
    try:
        p.feed(html or "")
        p.close()
        text = "".join(p.parts)
    except Exception:  # pathological markup: fall back to a crude tag strip
        import html as _html
        text = _html.unescape(re.sub(r"(?is)<(script|style).*?</\1>|<[^>]+>", " ", html or ""))
    extra = []
    for blob in p.ldjson:
        names = re.findall(r'"legalName"\s*:\s*"([^"]{2,120})"', blob)
        for v in re.findall(r'"(?:vatID|taxID)"\s*:\s*"([^"]{11,20})"', blob):
            extra.append(f"{names[0] if names else ''} P.IVA {v}".strip())
    return _squeeze(text + ("\n" + "\n".join(extra) if extra else ""))


# ---------------------------------------------------------------- VAT candidates

# keyword -> evidence strength. VAT words beat fiscal-code / register words (a company's 11-digit C.F. is
# almost always its VAT number, so it is kept, but ranked lower).
_KW = [
    (3, r"partita\s*i\.?\s?v\.?\s?a\b\.?|\bp\.?\s?i\.?\s?v\.?\s?a\b\.?|\bvat\s?id\b"
        r"|\bvat\b(?:\s*(?:no|nr|n|number|code|reg(?:istration)?(?:\s*no)?)\b\.?)?|\bnumero\s+iva\b|\bcod(?:ice|\.)?\s*iva\b"),
    (2, r"\bp\.\s?i\.(?!\w)|\biva\b"),
    (1, r"\bc\.\s?f\.?(?!\w)|\bcf\b|\bcod(?:ice|\.)?\s*fisc(?:ale|\.)?|registro\s+(?:delle\s+)?imprese|\breg\.?\s*imp(?:rese|\.)?|\br\.\s?i\.(?!\w)"),
]
_KW_RE = [(s, re.compile(p, re.I)) for s, p in _KW]
_PHONE = re.compile(r"\b(?:tel|telefono|phone|fax|cell|cellulare|mobile|mob|whatsapp|numero\s+verde|call)\b", re.I)
_CREDIT = re.compile(r"realizzat[oaie]\s+(?:da|by)|realizzazione\s+(?:sito|web|siti|grafica)|sviluppat[oa]\s+da|powered\s+by|design(?:ed)?\s+by"
                     r"|developed\s+by|made\s+by|created\s+by|web\s*agency|agenzia\s+web|web\s*design|webmaster|credits?\s*[:\-]|crediti\s*[:\-]"
                     r"|sito\s+(?:web\s+)?(?:realizzato|creato|curato|sviluppato)|hosting\s+(?:by|da)|seo\s+(?:by|da)", re.I)
_CREDIT_STOP = re.compile(r"©|\bcopyright\b|\||•|tutti\s+i\s+diritti\s+riservati|all\s+rights\s+reserved", re.I)
_NUM = re.compile(r"(?<!\d)(?:IT[ \-]?)?(\d{11})(?!\d)", re.I)
_NUM_SPACED = re.compile(r"(?<!\d)(?<!\d[ .])(?:IT[ \-]?)?(\d{1,4}(?:[ .]\d{1,4}){2,7})(?!\d)(?![ .]\d)", re.I)
_BEFORE, _CREDIT_WIN = 60, 120


@dataclass
class VatCandidate:
    vat: str
    score: int        # higher = stronger evidence
    strength: int     # 3 VAT keyword, 2 'P.I.' / 'IVA', 1 C.F. or company register only
    count: int        # occurrences on the page
    credit: bool      # every occurrence sits in a web-agency credit ("realizzato da ...")
    pos: int          # offset of the best occurrence in page_text(input)


def _keyword_strength(text: str, s: int, e: int) -> int:
    seg = text[max(0, s - _BEFORE):s]
    seg = re.split(r"\d", seg)[-1]  # the label must be the nearest thing before the number: no other digits between
    ph = list(_PHONE.finditer(seg))
    if ph:
        seg = seg[ph[-1].end():]
    best = max((st for st, rx in _KW_RE if rx.search(seg)), default=0)
    after = re.match(r"\s*\(\s*([^()\d]{2,30})\)", text[e:e + 40])  # '01234567890 (P.IVA)'
    if after:
        best = max([best] + [st for st, rx in _KW_RE if rx.search(after.group(1))])
    return best


def _in_credit(text: str, s: int, prev_end: int) -> bool:
    seg = text[max(prev_end, s - _CREDIT_WIN):s]
    stops = list(_CREDIT_STOP.finditer(seg))
    if stops:
        seg = seg[stops[-1].end():]
    return bool(_CREDIT.search(seg))


def extract_vat_candidates(text: str) -> list[VatCandidate]:
    """Partita IVA numbers in a page (HTML or text), strongest evidence first.

    A number counts only if its check digit is valid AND a VAT / fiscal-code keyword labels it (the nearest text
    before it, or '(P.IVA)' right after), so phone numbers and other 11-digit strings are not picked up. A number
    shown in a web-agency credit ("realizzato da ...", "powered by ...") is kept but flagged and ranked last.
    Offsets refer to page_text(text).
    """
    t = page_text(text)
    hits = []
    for rx in (_NUM, _NUM_SPACED):
        for m in rx.finditer(t):
            num = re.sub(r"\D", "", m.group(1))
            if len(num) == 11 and valid_partita_iva(num):
                hits.append((m.start(), m.end(), num, rx is _NUM_SPACED))
    hits.sort()
    occ: dict[str, list] = {}
    prev_end = 0
    for s, e, num, spaced in hits:
        st = _keyword_strength(t, s, e)
        if st:
            occ.setdefault(num, []).append((s, st, _in_credit(t, s, prev_end), spaced))
        prev_end = e
    out = []
    for num, os_ in occ.items():
        good = [o for o in os_ if not o[2]] or os_  # judge on the non-credit occurrences when there are any
        strength = max(o[1] for o in good)
        best = min(good, key=lambda o: (-o[1], o[3], o[0]))
        score = strength * 10 + min(len(os_), 5) - (1 if all(o[3] for o in good) else 0)
        out.append(VatCandidate(num, score, strength, len(os_), all(o[2] for o in os_), best[0]))
    return sorted(out, key=lambda c: (c.credit, -c.score, c.pos))


# ---------------------------------------------------------------- legal name, form, REA

# legal form written in a page, in any case and with or without dots
_FORM_SUFFIX = re.compile(
    r"(?<![\w.])(?:"
    r"s\.?\s?c\.?\s?a\.?\s?r\.?\s?l\.?|s\.?\s?c\.?\s?r\.?\s?l\.?|s\.?\s?c\.?\s?p\.?\s?a\.?"
    r"|soc(?:\.|iet[aà]'?)?\s*coop(?:\.|erativa)?(?:\s+(?:sociale|agricola|a\s?r\.\s?l\.?|onlus))*"
    r"|s\.?\s?a\.?\s?p\.?\s?a\.?|s\.?\s?r\.?\s?l\.?(?:\s?s\.?(?!\w))?(?:\s+semplificata)?|s\.?\s?p\.?\s?a\.?|s\.?\s?n\.?\s?c\.?"
    r"|s\.?\s?a\.?\s?s\.?|s\.\s?s\.(?!\s?\d)|s\.\s?c\.|a\s?r\.\s?l\.?"
    r"|societ[aà]'?\s+(?:a\s+responsabilit[aà]'?\s+limitata(?:\s+semplificata)?|per\s+azioni|in\s+nome\s+collettivo"
    r"|in\s+accomandita\s+(?:semplice|per\s+azioni)|semplice(?:\s+agricola)?|agricola\s+semplice|cooperativa|consortile(?:\s+a\s+r\.?\s?l\.?)?)"
    r"|onlus|gmbh|ltd|llc)(?!\w)", re.I)
_FORM_PREFIX = re.compile(r"(?<!\w)(?:consorzio|cooperativa|associazione|fondazione|comune\s+di|provincia\s+di|parrocchia"
                          r"|a\.\s?s\.\s?d\.|asd)(?!\w)", re.I)
_CONNECT = {"di", "de", "del", "della", "dei", "delle", "degli", "e", "&", "d'", "f.lli", "fratelli", "&c.", "c."}
_PREFIX_WORDS = _CONNECT | {"sociale", "agricola", "agrario", "sportiva", "dilettantistica", "culturale", "onlus"}
_NAME_STOP = re.compile(r"(?i)©|copyright|\b(?:19|20)\d\d\b|[|•·:;\"“”»«()\[\]]|\s[-–—]\s|(?<=[a-zàèéìòù]{3})\.\s|"
                        r"tutti\s+i\s+diritti|all\s+rights|riservati|reserved")
_TOKEN = re.compile(r"[\w&'’.\-+]+")


def _name_before(seg: str) -> list[str]:
    cuts = list(_NAME_STOP.finditer(seg))
    if cuts:
        seg = seg[cuts[-1].end():]
    seg = seg.split("\n")[-1]
    toks, out = _TOKEN.findall(seg), []
    for tok in reversed(toks):
        low = tok.lower()
        if len(out) >= 8 or re.fullmatch(r"(?:p\.?\s?iva|vat|c\.f\.|cf|sede|via|viale|piazza|tel\.?)", low):
            break
        if tok[0].isupper() or tok[0].isdigit() or low in _CONNECT or tok.isupper():
            out.insert(0, tok)
        else:
            break
    while out and (out[0].lower() in _CONNECT or out[0] in "&.-"):
        out.pop(0)
    return out


def extract_legal_name(text: str, around) -> str | None:
    """Best-effort legal name printed near a VAT number, e.g. 'Rossi Impianti S.r.l.'. None without a legal form.

    `around` is an offset into `text` (VatCandidate.pos) or the VAT number itself.
    """
    if not isinstance(text, str):
        return None
    if isinstance(around, str):
        m = re.search(r"[ .]?".join(re.escape(ch) for ch in around), text)
        if not m:
            return None
        around = m.start()
    lo, hi = max(0, around - 200), around + 120
    win = text[lo:hi]
    rel = around - lo
    # do not cross into the next number (often another company's) or into a web-agency credit after the VAT
    nxt = [m.start() for m in re.finditer(r"\d{11}", win) if m.start() > rel + 11]
    cr = [m.start() for m in _CREDIT.finditer(win) if m.start() > rel]
    win = win[:min(nxt + cr + [len(win)])]
    best = None
    for m in _FORM_SUFFIX.finditer(win):
        name = _name_before(win[:m.start()])
        if not name:
            continue
        dist = rel - m.end() if m.end() <= rel else (m.start() - rel) * 1.5
        if best is None or dist < best[0]:
            form = m.group(0).strip()
            form = form if "." in form[:-1] else form.rstrip(".")  # 'SRL.' ends a sentence, 'S.r.l.' keeps its dot
            best = (dist, " ".join(name + [form]))
    for m in _FORM_PREFIX.finditer(win):
        rest = []
        for tok in _TOKEN.findall(re.split(r"[|•·:;\n,()]|\s[-–—]\s|(?i:p\.?\s?iva|c\.f\.|\bvia\b|\bsede\b)", win[m.end():])[0])[:6]:
            if not (tok[0].isupper() or tok[0].isdigit() or tok.lower() in _PREFIX_WORDS):
                break
            rest.append(tok)
        while rest and rest[-1].lower() in _CONNECT:
            rest.pop()
        if not rest:
            continue
        dist = abs(rel - m.start()) + 5
        if best is None or dist < best[0]:
            best = (dist, " ".join([m.group(0)] + rest))
    return re.sub(r"\s+", " ", best[1]).strip(" ,-") if best else None


def _squash(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"['’`]", "", s)
    s = re.sub(r"(?:&|\be)\s*c\.(?=\s|$)", " & co ", s)  # 'Rossi & C. S.n.c.': do not glue the C. to the form
    s = re.sub(r"\b(?:[a-z]\.){1,}[a-z]\b\.?", lambda m: m.group(0).replace(".", ""), s)  # S.r.l. -> srl
    s = re.sub(r"\b(?:[a-z]\. ?){2,}[a-z]\b\.?", lambda m: re.sub(r"[. ]", "", m.group(0)), s)  # S. r. l. -> srl (after the above: 'G. S.r.l.')
    s = re.sub(r"[^a-z0-9&]+", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "


_FORMS = [  # checked in this order; the first match wins
    ("public_body", r"^ (?:comune|provincia|regione|ministero|universita|istituto comprensivo|azienda (?:sanitaria|ospedaliera|socio sanitaria)"
                    r"|asst|ats|camera di commercio|agenzia delle) "),
    ("coop", r" (?:coop|cooperativ[aoei]|soc coop|sc|scrl|scarl|sca rl|scpa) "),
    ("consortium", r" (?:consorzio|consortile|consorzi) "),
    ("sapa", r" (?:sapa|societa in accomandita per azioni) "),
    ("srls", r" (?:srls|srl semplificata|societa (?:a )?responsabilita limitata semplificata) "),
    ("spa", r" (?:spa|societa per azioni) "),
    ("snc", r" (?:snc|societa in nome collettivo) "),
    ("sas", r" (?:sas|societa in accomandita semplice) "),
    ("srl", r" (?:srl|a rl|arl|societa (?:a )?responsabilita limitata|gmbh|ltd|llc) "),
    ("ss", r" (?:ss|societa semplice|societa semplice agricola|societa agricola semplice) "),
    ("association_or_foundation", r" (?:associazione|fondazione|onlus|asd|aps|odv|parrocchia|circolo|pro loco|ente) "),
]
_FORMS_RE = [(k, re.compile(p)) for k, p in _FORMS]
# coop vs consortium: 's.c.a r.l.' is used by both; the word 'consortile' / 'consorzio' decides
_CONSORTIUM_WORD = re.compile(r" (?:consortile|consorzio) ")


def legal_form(name) -> str:
    """'ROSSI IMPIANTI S.R.L.' -> 'srl'. One of srl, srls, spa, sapa, snc, sas, ss, coop, consortium,
    association_or_foundation, public_body, sole_trader_or_unknown (a person's name or no recognisable form)."""
    if not isinstance(name, str) or not name.strip():
        return "sole_trader_or_unknown"
    s = _squash(name)
    if _CONSORTIUM_WORD.search(s):
        return "consortium"
    for k, rx in _FORMS_RE:
        if rx.search(s):
            return k
    return "sole_trader_or_unknown"


def entity_type(form) -> str:
    """'company' for a legal entity or partnership, else 'sole_trader_or_unknown' (may be a private person)."""
    return "company" if form and form != "sole_trader_or_unknown" else "sole_trader_or_unknown"


_REA = re.compile(r"(?<![\w.])(?:R\.\s?E\.\s?A\.?|REA|Rea)(?!\w)[^\w\n]{0,6}"
                  r"(?:(?:n(?:r|um(?:ero)?)?|no)\.?\s*)?(?:(?:di|of)\s+[A-Z][a-zà-ù]+(?:\s[A-Z][a-zà-ù]+)?\s*)?[^\w\n]{0,4}"
                  r"(?:(?:n(?:r|um(?:ero)?)?|no)\.?\s*)?(?:([A-Z][A-Za-z])(?![a-z])[\s\-:./]{0,3})?(\d{4,7})(?!\d)")


def rea_number(text) -> str | None:
    """Company register (REA) number, best effort: 'REA BS-123456' -> 'BS-123456', 'R.E.A. n. 123456' -> '123456'."""
    if not isinstance(text, str):
        return None
    m = _REA.search(text)
    if not m:
        return None
    return f"{m.group(1).upper()}-{m.group(2)}" if m.group(1) else m.group(2)


# ---------------------------------------------------------------- VIES

def _vies_str(v) -> str | None:
    if not isinstance(v, str):
        return None
    v = ", ".join(p.strip() for p in v.replace("\r", "\n").split("\n") if p.strip())
    v = re.sub(r"\s+", " ", v).strip(" ,")
    return None if not v or set(v) <= {"-"} or v.upper() in ("N/A", "NA") else v


def parse_vies(payload) -> dict:
    """Normalise a VIES answer -> {valid: True | False | None, name, address (one line), error}.

    Handles the REST GET shape (isValid, userError), the POST shape (valid) and the error shape
    (actionSucceed false, errorWrappers). valid is None, never False, when the service gave no answer.
    """
    out = {"valid": None, "name": None, "address": None, "error": None}
    if not isinstance(payload, dict) or not payload:
        out["error"] = "no answer"
        return out
    wraps = payload.get("errorWrappers") or []
    if payload.get("actionSucceed") is False or wraps:
        out["error"] = (wraps[0].get("error") if wraps and isinstance(wraps[0], dict) else None) or "error"
        return out
    ue = payload.get("userError")
    v = payload.get("isValid", payload.get("valid"))
    if ue and ue not in ("VALID", "INVALID"):
        out["error"] = ue
        return out
    if v is True or (v is None and ue == "VALID"):
        out.update(valid=True, name=_vies_str(payload.get("name")), address=_vies_str(payload.get("address")))
    elif v is False or ue == "INVALID":
        out["valid"] = False
    else:
        out["error"] = "no answer"
    return out


# ---------------------------------------------------------------- pages worth a second look

_LINK_KW = [  # rank: lower = tried first
    (0, r"note\s?legali|legal|impressum|dati\s?societari|informazioni\s?(?:legali|societarie)"),
    (1, r"contatt|contact"),
    (2, r"privacy"),
    (3, r"chi\s?siamo|azienda|about|company"),
    (4, r"dove\s?siamo"),
    (5, r"credits?|crediti"),
]
_LINK_KW_RE = [(r, re.compile(p, re.I)) for r, p in _LINK_KW]
_NOT_PAGE = re.compile(r"\.(?:pdf|jpe?g|png|gif|webp|svg|zip|rar|docx?|xlsx?|pptx?|mp[34]|avi|mov|ico|css|js|xml|json)$", re.I)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.base, self.first, self._open = [], None, [], []  # first: meta-refresh and frame targets

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and a.get("href"):
            self._open.append([a["href"], [a.get("title", "")]])
        elif tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "frame" and a.get("src"):  # old framesets: the content is in the frames
            self.first.append(a["src"])
        elif tag == "meta" and a.get("http-equiv", "").lower() == "refresh":
            m = re.search(r"url\s*=\s*['\"]?([^'\";]+)", a.get("content", ""), re.I)
            if m:
                self.first.insert(0, m.group(1).strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._open:
            href, txt = self._open.pop()
            self.links.append((href, " ".join(txt)))

    def handle_data(self, data):
        if self._open:
            self._open[-1][1].append(data)


def _canon(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", p.query, ""))


def select_pages(base_url: str, html: str, max_extra: int = 2) -> list[str]:
    """Same-domain pages likely to carry the VAT number when the home page does not: legal notes, contacts,
    privacy, about, where-we-are, credits (in that order), matched on the link's URL or text. A same-domain
    meta-refresh or frameset target comes first."""
    p = _Links()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        pass
    base = urljoin(base_url, p.base) if p.base else base_url
    dom, here = normalize_domain(base_url), _canon(base_url)
    ranked = {}
    cands = [(h, "", -1) for h in p.first] + [(h, t, None) for h, t in p.links]
    for i, (href, txt, rank) in enumerate(cands):
        href = href.strip()
        if not href or href.startswith("#") or re.match(r"(?i)(?:mailto|tel|javascript|data|sms|whatsapp|callto):", href):
            continue
        url = urljoin(base, href)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or normalize_domain(url) != dom or _NOT_PAGE.search(parts.path):
            continue
        if _canon(url) == here:
            continue
        if rank is None:
            hay = re.sub(r"[-_/.+%]", " ", (parts.path + " " + parts.query).lower()) + " " + re.sub(r"\s+", " ", txt.lower())
            rank = min((r for r, rx in _LINK_KW_RE if rx.search(hay)), default=None)
            if rank is None:
                continue
        key = _canon(url)
        if key not in ranked or (rank, i) < ranked[key][:2]:
            ranked[key] = (rank, i, urlunsplit(parts._replace(netloc=parts.netloc.lower(), fragment="")))
    return [u for _, _, u in sorted(ranked.values())][:max_extra]
