"""Website URL helpers shared by the occupant, company and map-panel code."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit, urlunsplit

# "websites" that are really a phone number or a private chat: wa.me/39333..., t.me/..., m.me/...
MESSAGING_HOSTS = ("wa.me", "whatsapp.com", "t.me", "telegram.me", "telegram.org", "m.me", "messenger.com", "viber.com", "signal.me",
                   "skype.com", "line.me")
# an e-mail sign (also encoded, fullwidth or spelled "(at)"), or a run of 9+ digits with the usual separators between them
_PHONE_OR_EMAIL = re.compile(r"@|\uff20|%(?:25)*40|[(\[{]at[)\]}]|\d(?:(?:[ ./+_()-]|%20)*\d){8,}", re.I)


def normalize_domain(url: str | None) -> str | None:
    """'http://www.Example.it/contatti' -> 'example.it'. The join key between a business listing and its company record.

    Lower-case host without a leading 'www.', port or path. None when there is no usable host.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    u = url.strip()
    try:
        host = urlsplit(u if "://" in u else "http://" + u).hostname
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def safe_http_url(url: str | None) -> str | None:
    """The URL if it is plain http(s) (adding http:// to a bare host), else None. Never returns javascript: or data: links."""
    if not isinstance(url, str) or not url.strip():
        return None
    u = url.strip()
    if "://" not in u:
        u = "http://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        return None
    return u


def public_website(url: str | None) -> str | None:
    """A business website that is safe to store and show, or None: the one URL filter for listings, register and panel.

    Plain http(s) only, no user info ('http://info@x.it' is an e-mail address), not a messaging host (a WhatsApp link is a
    phone number) and no phone number or e-mail address in the host. A path that carries one is cut back to the site root.
    The query string and fragment are dropped: they are tracking noise at best and can carry contact details ('?tel=030...').
    """
    u = safe_http_url(url)
    if u is None:
        return None
    parts = urlsplit(u)
    host = (parts.hostname or "").lower().rstrip(".")
    if "@" in parts.netloc or any(host == h or host.endswith("." + h) for h in MESSAGING_HOSTS):
        return None
    if _PHONE_OR_EMAIL.search(parts.netloc):
        return None
    # a contact detail in the path only (also percent-encoded): the site itself is still the business's website, keep its root
    decoded = unquote(parts.path).replace("\u00a0", " ")
    path = "/" if _PHONE_OR_EMAIL.search(parts.path) or _PHONE_OR_EMAIL.search(decoded) else parts.path
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
