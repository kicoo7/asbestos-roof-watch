"""Tabs for the aerial map page: "Map" (the folium map) and full-page tabs such as "Grants".

All text from data files is escaped. Roof links are `#roof=<id>`: the roof panel opens the roof on hashchange, and the
tab script switches back to the map whenever the address names a roof.
"""
from __future__ import annotations

from datetime import date
from html import escape

from .grants import grant_status

CANDIDATES = ("agree", "suspected_new")  # roofs the model flags as asbestos (on ARPA's map or not)
GROUP_WORDS = {"agree": "on ARPA map, model agrees", "suspected_new": "not on ARPA map, model suspects"}
STATUS = {"open": ("OPEN", "#1a9641"), "upcoming": ("UPCOMING", "#2b83ba"), "closed": ("Closed", "#888"), "ask": ("Ask", "#888")}
TOP_N = 15

CSS = """
.rw-tabs{position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:10001;display:flex;gap:2px;background:#fff;
 border:1px solid #999;border-top:0;border-radius:0 0 6px 6px;padding:2px;font:13px sans-serif;box-shadow:0 1px 3px rgba(0,0,0,.2)}
.rw-tabs button{border:0;background:none;padding:5px 14px;cursor:pointer;border-radius:4px;font:inherit;color:#333}
.rw-tabs button[aria-selected=true]{background:#2b83ba;color:#fff}
.rw-tabpage{position:fixed;inset:0;z-index:10000;background:#f6f6f4;overflow:auto;padding:44px 16px 24px;box-sizing:border-box;
 font:14px/1.45 sans-serif;color:#222}
.rw-tabpage[hidden]{display:none}
.rw-wrap{max-width:980px;margin:0 auto}
.rw-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.rw-card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 14px}
.rw-card h3{margin:0 0 6px;font-size:15px}
.rw-badge{display:inline-block;color:#fff;font-size:11px;font-weight:700;padding:1px 7px;border-radius:9px;margin-right:6px;vertical-align:2px}
.rw-meta{color:#555;font-size:12px}
.rw-card table{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}
.rw-card td,.rw-card th{text-align:left;padding:3px 4px;border-top:1px solid #eee;vertical-align:top}
.rw-card th{color:#555;font-weight:600}
.rw-ob{background:#fff8e1;border:1px solid #f0d78c;border-radius:8px;padding:10px 14px;margin:14px 0}
@media (max-width:600px){.rw-tabpage{padding:44px 10px 20px}.rw-tabs button{padding:5px 10px}}
"""

JS = """
(function () {
  var bar = document.getElementById("rw-tabs");
  function show(tab) {
    Array.prototype.forEach.call(document.querySelectorAll(".rw-tabpage"), function (p) { p.hidden = p.id !== "rw-tab-" + tab; });
    Array.prototype.forEach.call(bar.querySelectorAll("button"), function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-tab") === tab ? "true" : "false");
    });
    try { localStorage.setItem("rw-tab", tab); } catch (e) { /* storage may be blocked */ }
  }
  bar.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-tab]");
    if (b) show(b.getAttribute("data-tab"));
  });
  // a link to a roof (#roof=<id>) always lands on the map, where the roof panel opens it
  window.addEventListener("hashchange", function () { if (/^#roof=\\d+$/.test(location.hash)) show("map"); });
  var start = "map";
  if (!/^#roof=\\d+$/.test(location.hash)) { try { start = localStorage.getItem("rw-tab") || "map"; } catch (e) { start = "map"; } }
  if (!bar.querySelector('button[data-tab="' + start + '"]')) start = "map";
  show(start);
})();
"""


def tabs_html(pages: dict[str, tuple[str, str]]) -> str:
    """Tab bar with "Map" first, then one full-page tab per entry of pages: id -> (label, inner html)."""
    buttons = ['<button type="button" data-tab="map" aria-selected="true">Map</button>']
    divs = []
    for tid, (label, inner) in pages.items():
        buttons.append(f'<button type="button" data-tab="{escape(tid, quote=True)}" aria-selected="false">{escape(label)}</button>')
        divs.append(f'<div id="rw-tab-{escape(tid, quote=True)}" class="rw-tabpage" hidden><div class="rw-wrap">{inner}</div></div>')
    return (f"<style>{CSS}</style>\n<nav id=\"rw-tabs\" class=\"rw-tabs\" role=\"tablist\">{''.join(buttons)}</nav>\n"
            + "\n".join(divs) + f"\n<script>{JS}</script>")


def _fits(grant: dict, owner_type: str) -> bool:
    return owner_type in grant["for"] or "unknown" in grant["for"] or owner_type == "unknown"


def grants_tab(grants: dict, today: date, roofs: list[dict]) -> str:
    """The Grants page: a card per scheme with its status today and the candidate roofs it may fit.

    roofs: dicts with roof_id, comune, area_m2, group, owner_type, why, dnc. Only candidates (CANDIDATES) not marked
    do-not-contact are counted and listed, largest first.
    """
    cand = [r for r in roofs if r.get("group") in CANDIDATES and not r.get("dnc")]
    n_open = sum(grant_status(g, today) == "open" for g in grants["grants"])
    out = [f"<h1 style='margin:0 0 4px'>Asbestos removal grants in {escape(grants.get('region', ''))}</h1>",
           f"<p class='rw-meta'>{n_open} open on {today.isoformat()} · grant data checked on {escape(grants.get('checked_on', ''))}. "
           f"{escape(grants.get('note', ''))}</p>"]
    if grants.get("obligations"):
        out.append(f"<div class='rw-ob'><b>Owner obligations</b><br>{escape(grants['obligations'])}</div>")
    counts = {}
    for r in cand:
        counts[r["owner_type"]] = counts.get(r["owner_type"], 0) + 1
    out.append("<p class='rw-meta'>Candidate roofs by likely owner type: " + ", ".join(
        f"{escape(t)} {n}" for t, n in sorted(counts.items(), key=lambda kv: -kv[1])) +
        ". Owner type is a guess from public evidence (permits, named farm sites, businesses, company records, roof size);"
        " each call sets its own eligibility rules. Roofs with unknown owner type are listed under every scheme.</p>")
    cards = []
    order = {"open": 0, "upcoming": 1, "ask": 2, "closed": 3}
    for g in sorted(grants["grants"], key=lambda g: order[grant_status(g, today)]):
        st = grant_status(g, today)
        word, colour = STATUS[st]
        fit = sorted((r for r in cand if _fits(g, r["owner_type"])), key=lambda r: -(r.get("area_m2") or 0))
        when = (f"Until {g['closes']}" + (f": {g.get('close_meaning')}" if g.get("close_meaning") else "") if st == "open" and g.get("closes")
                else f"Opens {g['opens']}" if st == "upcoming"
                else f"Closed {g['closes']}" + (f": {g.get('close_meaning')}" if g.get("close_meaning") else "") if st == "closed" else "")
        rows = "".join(
            f"<tr><td><a href=\"#roof={int(r['roof_id'])}\">{escape(str(r.get('comune', '')))}</a></td>"
            f"<td>{int(round(r.get('area_m2') or 0)):,} m2</td><td>{escape(r['owner_type'])}"
            f"{' (' + escape(r['why']) + ')' if r.get('why') else ''}</td><td>{escape(GROUP_WORDS.get(r['group'], ''))}</td></tr>"
            for r in fit[:TOP_N])
        table = (f"<table><tr><th>Biggest matching roofs</th><th>Roof</th><th>Likely owner</th><th>Asbestos</th></tr>{rows}</table>"
                 if rows else "<p class='rw-meta'>No candidate roof fits this scheme.</p>")
        src = "".join(f" <a href=\"{escape(u, quote=True)}\" target=\"_blank\" rel=\"noopener noreferrer\">source {i + 1}</a>"
                      for i, u in enumerate(g.get("sources") or []))
        cards.append(
            f"<div class='rw-card' data-grant=\"{escape(g['id'], quote=True)}\" data-roofs=\"{len(fit)}\">"
            f"<h3><span class='rw-badge' style='background:{colour}'>{word}</span>{escape(g['name'])}</h3>"
            f"<div class='rw-meta'>For: {escape(', '.join(g['for']))}</div><p style='margin:6px 0'>{escape(g['what'])}</p>"
            f"<div>{escape(when)}</div>"
            + (f"<div class='rw-meta'>Next: {escape(g.get('next_call', ''))}</div>" if g.get("next_call") and st != "open" else "")
            + f"<div class='rw-meta'>{len(fit):,} candidate roofs may fit.{src}</div>{table}</div>")
    out.append(f"<div class='rw-cards'>{''.join(cards)}</div>")
    return "\n".join(out)
