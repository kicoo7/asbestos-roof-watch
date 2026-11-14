"""Tabs for the aerial map page: "Map" (the folium map) and full-page tabs such as "Grants".

The Grants tab drives the workflow grant -> eligible roofs -> owner -> contact:
- a card per scheme: status (open with days left, upcoming, or closed: prepare for the next call), what it pays, and a
  funnel over its eligible candidate roofs (to identify, owner identified, contacted, interested, not interested), with
  how sure the owner type is (confirmed by the user's own lookup, guessed from public evidence, unknown);
- "Show eligible roofs": a sortable, filterable list ranked by score (grants.roof_grant_scores), with "Show on map"
  (highlights the filtered roofs) and "Export CSV".
Text from data files is escaped in Python, and the page script builds every element with textContent. Roof links are
`#roof=<id>`: the roof panel opens the roof on hashchange, and the tab script switches to the map whenever the address
names a roof.
"""
from __future__ import annotations

import json
from datetime import date
from html import escape

from .grants import STAGES, grant_status, roof_grant_scores

CANDIDATES = ("agree", "suspected_new")  # roofs the model flags as asbestos (on ARPA's map or not)
STATUS = {"open": ("OPEN", "#1a9641"), "upcoming": ("UPCOMING", "#2b83ba"), "closed": ("Closed", "#888"), "ask": ("Ask", "#888")}

CSS = """
.rw-tabs{position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:10001;display:flex;gap:2px;background:#fff;
 border:1px solid #999;border-top:0;border-radius:0 0 6px 6px;padding:2px;font:13px sans-serif;box-shadow:0 1px 3px rgba(0,0,0,.2)}
.rw-tabs button{border:0;background:none;padding:5px 14px;cursor:pointer;border-radius:4px;font:inherit;color:#333}
.rw-tabs button[aria-selected=true]{background:#2b83ba;color:#fff}
.rw-tabpage{position:fixed;inset:0;z-index:10000;background:#f6f6f4;overflow:auto;padding:44px 16px 24px;box-sizing:border-box;
 font:14px/1.45 sans-serif;color:#222}
.rw-tabpage[hidden]{display:none}
.rw-wrap{max-width:1100px;margin:0 auto}
.rw-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.rw-card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:4px}
.rw-card h3{margin:0 0 2px;font-size:15px}
.rw-badge{display:inline-block;color:#fff;font-size:11px;font-weight:700;padding:1px 7px;border-radius:9px;margin-right:6px;vertical-align:2px}
.rw-meta{color:#555;font-size:12px}
.rw-funnel{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin:6px 0;font-size:11px;text-align:center}
.rw-funnel div{background:#f1f4f8;border-radius:5px;padding:4px 2px}
.rw-funnel b{display:block;font-size:15px}
.rw-cert{font-size:11px;color:#555}
.rw-btn{border:1px solid #2b83ba;background:#2b83ba;color:#fff;border-radius:5px;padding:5px 10px;cursor:pointer;font:13px sans-serif}
.rw-btn.rw-light{background:#fff;color:#2b83ba}
.rw-btn:disabled{opacity:.5;cursor:default}
.rw-ob{background:#fff8e1;border:1px solid #f0d78c;border-radius:8px;padding:10px 14px;margin:14px 0}
.rw-list{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin-top:16px}
.rw-list h2{margin:0 0 4px;font-size:17px}
.rw-filters{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:8px 0;font-size:12px}
.rw-filters label{white-space:nowrap}
.rw-filters select,.rw-filters input[type=search]{font:12px sans-serif;padding:2px 4px}
.rw-tablewrap{overflow-x:auto}
.rw-list table{width:100%;border-collapse:collapse;font-size:12px}
.rw-list th,.rw-list td{text-align:left;padding:4px 5px;border-top:1px solid #eee;vertical-align:top}
.rw-list th{color:#555;font-weight:600;cursor:pointer;white-space:nowrap;user-select:none}
.rw-list th[aria-sort]::after{content:" \\25BE"}
.rw-list th[aria-sort=ascending]::after{content:" \\25B4"}
.rw-k-confirmed{color:#1a9641;font-weight:700}.rw-k-guessed{color:#b8860b}.rw-k-unknown{color:#888}
.rw-mapbar{position:fixed;top:34px;left:50%;transform:translateX(-50%);z-index:10001;background:#003f5c;color:#fff;
 border-radius:6px;padding:5px 10px;font:12px sans-serif;display:flex;gap:10px;align-items:center;max-width:calc(100vw - 30px)}
.rw-mapbar button{border:0;border-radius:4px;padding:2px 8px;cursor:pointer;font:12px sans-serif}
@media (max-width:600px){.rw-tabpage{padding:44px 10px 20px}.rw-tabs button{padding:5px 10px}.rw-funnel{grid-template-columns:repeat(3,1fr)}}
"""

TABS_JS = """
(function () {
  var bar = document.getElementById("rw-tabs");
  function show(tab) {
    Array.prototype.forEach.call(document.querySelectorAll(".rw-tabpage"), function (p) { p.hidden = p.id !== "rw-tab-" + tab; });
    Array.prototype.forEach.call(bar.querySelectorAll("button"), function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-tab") === tab ? "true" : "false");
    });
    try { localStorage.setItem("rw-tab", tab); } catch (e) { /* storage may be blocked */ }
  }
  window.rwTabs = {show: show};
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

GRANTS_JS = """
(function () {
  var D = JSON.parse(document.getElementById("rw-grants-data").textContent);
  var G = {}; D.grants.forEach(function (g) { G[g.id] = g; });
  var GROUP = {agree: "on ARPA map, model agrees", suspected_new: "new find: not on ARPA map"};
  var box = document.getElementById("rw-grant-list");
  var state = null, overlay = null, PAGE = 200;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function add(p) { for (var i = 1; i < arguments.length; i++) if (arguments[i] != null) p.appendChild(arguments[i]); return p; }
  function opt(sel, value, label) { var o = el("option", null, label); o.value = value; sel.appendChild(o); return o; }
  function fmt(n, d) { return n == null ? "" : Number(n).toLocaleString("en-GB", {maximumFractionDigits: d || 0}); }

  function rowsFor(gid) { return D.roofs.filter(function (r) { return r.s[gid] != null; }); }

  function filtered() {
    var f = state.f, q = f.q.toLowerCase();
    return rowsFor(state.gid).filter(function (r) {
      if (!f.cert[r.k]) return false;
      if (r.a < f.minArea) return false;
      if (f.group && r.g !== f.group) return false;
      if (f.solar === "none" && !(r.v != null && r.v < 0.2)) return false;
      if (f.solar === "has" && !(r.v != null && r.v >= 0.9)) return false;
      if (f.stage && r.t !== f.stage) return false;
      if (f.town && r.c !== f.town) return false;
      if (q && [r.c, r.n, r.r, r.d].join(" ").toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }

  var COLS = [
    {key: "rank", label: "#"}, {key: "c", label: "Town"}, {key: "a", label: "Roof m2"}, {key: "p", label: "Asbestos"},
    {key: "o", label: "Owner type"}, {key: "t", label: "Stage"}, {key: "n", label: "Company on site"},
    {key: "r", label: "Parcel"}, {key: "score", label: "Score"}];

  function sorted(rows) {
    var k = state.sort, dir = state.dir, gid = state.gid;
    var val = function (r) { return k === "score" || k === "rank" ? r.s[gid].score : r[k]; };
    return rows.slice().sort(function (a, b) {
      var x = val(a), y = val(b);
      if (x == null) x = typeof y === "number" ? -Infinity : "";
      if (y == null) y = typeof x === "number" ? -Infinity : "";
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }

  function render() {
    var g = G[state.gid], rows = sorted(filtered()), wrap = box.querySelector(".rw-results");
    wrap.textContent = "";
    var info = box.querySelector(".rw-count");
    info.textContent = fmt(rows.length) + " of " + fmt(rowsFor(state.gid).length) + " eligible roofs match the filters";
    var table = el("table"), head = el("tr");
    COLS.forEach(function (c) {
      var th = el("th", null, c.label);
      if (state.sort === c.key || (c.key === "rank" && state.sort === "score")) th.setAttribute("aria-sort", state.dir < 0 ? "descending" : "ascending");
      th.onclick = function () {
        var k = c.key === "rank" ? "score" : c.key;
        if (state.sort === k) state.dir = -state.dir; else { state.sort = k; state.dir = (k === "c" || k === "o" || k === "t" || k === "n" || k === "r") ? 1 : -1; }
        render();
      };
      head.appendChild(th);
    });
    table.appendChild(head);
    rows.slice(0, state.shown).forEach(function (r, i) {
      var tr = el("tr"), link = el("a", null, r.c || "(no town)");
      link.href = "#roof=" + r.i;
      var ot = add(el("span"), el("span", "rw-k-" + r.k, r.k === "unknown" ? "unknown" : r.o), r.k === "unknown" ? null : el("span", "rw-cert", " (" + r.k + ")"));
      if (r.w) ot.title = r.w;
      [String(i + 1), link, fmt(r.a) , fmt(r.p, 2), ot, r.t, r.n || "", r.r || "", fmt(r.s[state.gid].score)].forEach(function (v) {
        var td = el("td"); if (typeof v === "string") td.textContent = v; else td.appendChild(v); tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    add(wrap, add(el("div", "rw-tablewrap"), table));
    if (rows.length > state.shown) {
      var more = el("button", "rw-btn rw-light", "Show " + Math.min(PAGE, rows.length - state.shown) + " more");
      more.onclick = function () { state.shown += PAGE; render(); };
      add(wrap, add(el("p"), more));
    }
    box.querySelector(".rw-onmap").disabled = !rows.length || !window.roofPanel || !window.roofPanel.map;
    box.querySelector(".rw-export").disabled = !rows.length;
  }

  function control(label, node) { return add(el("label", null, label + " "), node); }

  function open(gid) {
    var g = G[gid];
    state = {gid: gid, sort: "score", dir: -1, shown: PAGE,
             f: {cert: {confirmed: true, guessed: true, unknown: true}, minArea: 0, group: "", solar: "", stage: "", town: "", q: ""}};
    box.textContent = ""; box.hidden = false;
    add(box, el("h2", null, "Eligible roofs: " + g.name),
      el("p", "rw-meta", g.status === "open" ? "OPEN" + (g.days_left != null ? ": " + g.days_left + " days left" : "") + ". Act now." :
        g.status === "upcoming" ? "Opens " + g.opens + ". Prepare the list now." :
        "Closed. Prepare for the next call: " + (g.next_call || "not announced") + "."),
      el("p", "rw-meta", "Ranked by score: roof size x asbestos score x how sure the owner type is (confirmed 1, guessed 0.6, " +
        "unknown 0.25)" + (g.solar ? " x solar fit (no panels 1.3, panels already 0.3)" : "") +
        " x stage (interested 1.5, owner identified 1.1, not interested 0). Click a town to open the roof on the map."));
    var f = el("div", "rw-filters");
    ["confirmed", "guessed", "unknown"].forEach(function (k) {
      var c = el("input"); c.type = "checkbox"; c.checked = true;
      c.onchange = function () { state.f.cert[k] = c.checked; state.shown = PAGE; render(); };
      add(f, add(el("label"), c, el("span", "rw-k-" + k, " " + k + " owner")));
    });
    var area = el("select"); [[0, "any size"], [500, ">= 500 m2"], [1000, ">= 1,000 m2"], [2000, ">= 2,000 m2"], [5000, ">= 5,000 m2"]].forEach(function (a) { opt(area, a[0], a[1]); });
    area.onchange = function () { state.f.minArea = Number(area.value); state.shown = PAGE; render(); };
    var grp = el("select"); opt(grp, "", "any"); opt(grp, "agree", GROUP.agree); opt(grp, "suspected_new", GROUP.suspected_new);
    grp.onchange = function () { state.f.group = grp.value; state.shown = PAGE; render(); };
    var sol = el("select"); opt(sol, "", "any"); opt(sol, "none", "no panels yet"); opt(sol, "has", "panels already");
    sol.onchange = function () { state.f.solar = sol.value; state.shown = PAGE; render(); };
    var stg = el("select"); opt(stg, "", "any"); D.stages.forEach(function (s) { opt(stg, s, s); });
    stg.onchange = function () { state.f.stage = stg.value; state.shown = PAGE; render(); };
    var towns = {}; rowsFor(gid).forEach(function (r) { if (r.c) towns[r.c] = 1; });
    var town = el("select"); opt(town, "", "all towns"); Object.keys(towns).sort().forEach(function (t) { opt(town, t, t); });
    town.onchange = function () { state.f.town = town.value; state.shown = PAGE; render(); };
    var q = el("input"); q.type = "search"; q.placeholder = "company, address, parcel";
    q.oninput = function () { state.f.q = q.value; state.shown = PAGE; render(); };
    add(f, control("Size", area), control("Asbestos", grp), control("Solar", sol), control("Stage", stg), control("Town", town), control("Search", q));
    var onmap = el("button", "rw-btn rw-onmap", "Show on map"), exp = el("button", "rw-btn rw-light rw-export", "Export CSV");
    onmap.onclick = function () { showOnMap(sorted(filtered())); };
    exp.onclick = function () { exportCsv(sorted(filtered())); };
    add(box, f, add(el("div", "rw-filters"), el("span", "rw-count rw-meta"), onmap, exp), add(el("div", "rw-results")));
    render();
    box.scrollIntoView({behavior: "smooth", block: "start"});
  }

  function clearOverlay() {
    var map = window.roofPanel && window.roofPanel.map && window.roofPanel.map();
    if (overlay && map) map.removeLayer(overlay);
    overlay = null;
    var b = document.getElementById("rw-mapbar"); if (b) b.remove();
  }

  function showOnMap(rows) {
    var map = window.roofPanel && window.roofPanel.map && window.roofPanel.map();
    if (!map) return;
    clearOverlay();
    var feats = rows.map(function (r) { return window.roofPanel.feature(r.i); }).filter(Boolean);
    overlay = L.geoJSON({type: "FeatureCollection", features: feats}, {interactive: false,
      style: {color: "#00e5ff", weight: 4, opacity: 1, fillColor: "#00e5ff", fillOpacity: 0.25}}).addTo(map);
    var bar = el("div", "rw-mapbar"); bar.id = "rw-mapbar";
    var back = el("button", null, "Back to list"), clr = el("button", null, "Clear");
    back.onclick = function () { window.rwTabs.show("grants"); };
    clr.onclick = clearOverlay;
    add(bar, el("span", null, "Highlighting " + fmt(feats.length) + " roofs eligible for " + G[state.gid].name), back, clr);
    document.body.appendChild(bar);
    window.rwTabs.show("map");
    if (feats.length) map.fitBounds(overlay.getBounds(), {maxZoom: 17, padding: [30, 30]});
  }

  function csvCell(v) { v = v == null ? "" : String(v); return /[",\\n;]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }

  function exportCsv(rows) {
    var gid = state.gid, head = ["rank", "roof_id", "town", "province", "roof_m2", "asbestos_score", "solar_panels_score", "owner_type",
      "owner_type_certainty", "owner_type_evidence", "stage", "company_on_site", "parcel_ref", "foglio", "particella", "address",
      "asbestos_result", "score", "grant", "grant_status"];
    var lines = [head.join(",")];
    rows.forEach(function (r, i) {
      lines.push([i + 1, r.i, r.c, r.pr, r.a, r.p, r.v, r.k === "unknown" ? "" : r.o, r.k, r.w, r.t, r.n, r.r, r.fo, r.pa, r.d,
        GROUP[r.g] || r.g, r.s[gid].score, G[gid].name, G[gid].status].map(csvCell).join(","));
    });
    var blob = new Blob(["\\ufeff" + lines.join("\\n")], {type: "text/csv;charset=utf-8"});
    var a = el("a"); a.href = URL.createObjectURL(blob); a.download = "eligible_roofs_" + gid + "_" + D.today + ".csv";
    document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-show]");
    if (b) open(b.getAttribute("data-show"));
  });
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
            + "\n".join(divs) + f"\n<script>{TABS_JS}</script>")


def _json_for_html(obj) -> str:
    """JSON safe inside <script type="application/json">: no '<', '>' or '&' can close the tag or start markup."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def grants_tab(grants: dict, today: date, roofs: list[dict]) -> str:
    """The Grants page. roofs: dicts with roof_id, comune, area_m2, group, p, p_pv, owner_type (guess), confirmed_type,
    why, stage, dnc and optionally company, parcel_ref, foglio, particella, provincia, address.
    Only candidates (CANDIDATES) that are not do-not-contact are counted, listed and embedded."""
    cand = [r for r in roofs if r.get("group") in CANDIDATES and not r.get("dnc") and r.get("stage") != "do not contact"]
    data_roofs = []
    for r in cand:
        scores = roof_grant_scores(r, grants)
        if not scores:
            continue
        cert = next(iter(scores.values()))["certainty"]
        otype = r.get("confirmed_type") or r.get("owner_type") or "unknown"
        data_roofs.append({"i": int(r["roof_id"]), "c": r.get("comune") or "", "pr": r.get("provincia") or "",
                           "a": round(float(r.get("area_m2") or 0)), "p": r.get("p"), "v": r.get("p_pv"), "g": r.get("group"),
                           "o": otype, "k": cert, "w": r.get("why") or ("confirmed by your lookup" if r.get("confirmed_type") else ""),
                           "t": r.get("stage") or "to identify", "n": r.get("company") or "", "r": r.get("parcel_ref") or "",
                           "fo": r.get("foglio") or "", "pa": r.get("particella") or "", "d": r.get("address") or "",
                           "s": scores})
    gl = []
    for g in grants["grants"]:
        st = grant_status(g, today)
        days = (date.fromisoformat(g["closes"]) - today).days if st == "open" and g.get("closes") else None
        gl.append({"id": g["id"], "name": g["name"], "status": st, "opens": g.get("opens"), "closes": g.get("closes"),
                   "next_call": g.get("next_call", ""), "solar": bool(g.get("solar")), "days_left": days})

    n_open = sum(x["status"] == "open" for x in gl)
    out = [f"<h1 style='margin:0 0 4px'>Asbestos removal grants in {escape(grants.get('region', ''))}</h1>",
           f"<p class='rw-meta'>Start here: pick a scheme, then work through its eligible roofs (owner lookup, contact) before "
           f"the deadline. {n_open} open on {today.isoformat()} · grant data checked on {escape(grants.get('checked_on', ''))}. "
           f"{escape(grants.get('note', ''))}</p>"]
    if grants.get("obligations"):
        out.append(f"<div class='rw-ob'><b>Owner obligations</b><br>{escape(grants['obligations'])}</div>")
    order = {"open": 0, "upcoming": 1, "ask": 2, "closed": 3}
    cards = []
    for g, meta in sorted(zip(grants["grants"], gl), key=lambda t: order[t[1]["status"]]):
        st = meta["status"]
        word, colour = STATUS[st]
        elig = [r for r in data_roofs if g["id"] in r["s"]]
        stages = {s: sum(r["t"] == s for r in elig) for s in STAGES}
        cert = {k: sum(r["k"] == k for r in elig) for k in ("confirmed", "guessed", "unknown")}
        if st == "open":
            when = f"Until {g['closes']}" + (f" ({meta['days_left']} days left)" if meta["days_left"] is not None else "")
            when += f": {g.get('close_meaning')}" if g.get("close_meaning") else ""
            action = "Act now"
        elif st == "upcoming":
            when, action = f"Opens {g['opens']}", "Prepare the list now"
        elif st == "closed":
            when = f"Closed {g.get('closes') or ''}" + (f": {g.get('close_meaning')}" if g.get("close_meaning") else "")
            action = f"Prepare for the next call. Next: {g.get('next_call') or 'not announced'}"
        else:
            when, action = g.get("next_call", ""), "Ask"
        src = "".join(f" <a href=\"{escape(u, quote=True)}\" target=\"_blank\" rel=\"noopener noreferrer\">source {i + 1}</a>"
                      for i, u in enumerate(g.get("sources") or []))
        funnel = "".join(f"<div><b>{stages[s]:,}</b>{escape(s)}</div>" for s in STAGES)
        cards.append(
            f"<div class='rw-card' data-grant=\"{escape(g['id'], quote=True)}\" data-roofs=\"{len(elig)}\">"
            f"<h3><span class='rw-badge' style='background:{colour}'>{word}</span>{escape(g['name'])}</h3>"
            f"<div class='rw-meta'>For: {escape(', '.join(g['for']))}</div><div>{escape(g['what'])}</div>"
            f"<div>{escape(when)}</div><div class='rw-meta'><b>{escape(action)}</b>{src}</div>"
            f"<div class='rw-meta'>{len(elig):,} eligible candidate roofs · owner type: confirmed {cert['confirmed']:,}, "
            f"guessed {cert['guessed']:,}, unknown {cert['unknown']:,}</div>"
            f"<div class='rw-funnel'>{funnel}</div>"
            f"<div><button type='button' class='rw-btn' data-show=\"{escape(g['id'], quote=True)}\""
            f"{' disabled' if not elig else ''}>Show eligible roofs ({len(elig):,})</button></div></div>")
    out.append(f"<div class='rw-cards'>{''.join(cards)}</div>")
    out.append("<div id='rw-grant-list' class='rw-list' hidden></div>")
    data = {"today": today.isoformat(), "stages": list(STAGES), "grants": gl, "roofs": data_roofs}
    out.append(f'<script type="application/json" id="rw-grants-data">{_json_for_html(data)}</script>')
    out.append(f"<script>{GRANTS_JS}</script>")
    return "\n".join(out)
