#!/usr/bin/env python3
"""escape_all_compute.py — agrège les relevés en hiérarchie ENSEIGNE > CENTRE >
SALLE > SESSIONS, vers escape_all_data.json (consommé par le dashboard).

Mesures :
  - fill_now = booked / total sur la fenêtre J..J+7 (occupation réelle quand la
    billetterie est ouverte).
  - prix_moyen_session = moyenne du prix TOTAL par session.
Aucune notion de plateforme n'apparaît (volontairement).
"""
from __future__ import annotations

import glob
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from safestore import read_json, write_json

DATA_GLOB = "escape_data/4escape_all/*.json"
GEO_FILE = "escape_geo_cp.json"
OUT_FILE = "escape_all_data.json"
JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
BUCKETS = ["10-13", "13-16", "16-19", "19-21", "21-24"]


def bucket_of(h: str) -> str:
    hh = int((h or "12")[:2])
    return ("10-13" if hh < 13 else "13-16" if hh < 16 else "16-19"
            if hh < 19 else "19-21" if hh < 21 else "21-24")


def pct(a: int, b: int) -> float:
    return round(100 * a / b, 1) if b else 0.0


def med(xs: list):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[len(xs) // 2], 1) if xs else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    geo = read_json(GEO_FILE, {}) or {}
    cents = geo.get("_dept_centroids", {})
    files = sorted(glob.glob(DATA_GLOB))
    today = datetime.utcnow().date()
    horizon = today + timedelta(days=7)
    far = today + timedelta(days=4)               # seuil "fin de fenêtre" (détection salle fermée)

    # enseigne_id -> {nom, website, centres:{centre_key->centre}}
    ens: dict[str, dict] = {}
    hm = defaultdict(lambda: [0, 0])          # (ji,bucket)->[booked,total]
    themes = defaultdict(int)
    all_prices, all_sess = [], 0
    n_open_companies = 0

    def coords(c):
        if c.get("lat") and c.get("lon"):
            return c["lat"], c["lon"]
        cp = c.get("cp") or ""
        g = geo.get(cp) or cents.get(cp[:2]) or [48.8566, 2.3522]
        return g[0], g[1]

    for f in files:
        st = read_json(f, {}) or {}
        sessions = st.get("sessions", {})
        if not isinstance(sessions, dict) or not st.get("enseigne_id"):
            continue
        locked = st.get("_meta", {}).get("prices_locked", True)
        if not locked:
            n_open_companies += 1
        eid = st["enseigne_id"]
        e = ens.setdefault(eid, {"id": eid, "nom": st.get("enseigne_nom", eid),
                                 "website": st.get("website", ""), "centres": {}})
        # centres de cette company (dédup par nom+cp+adresse)
        local_centre = {}
        for cid, c in (st.get("centres") or {}).items():
            ckey = f"{c.get('nom','')}|{c.get('cp','')}|{c.get('adresse','')}".lower()
            lat, lon = coords(c)
            ce = e["centres"].setdefault(ckey, {
                "id": cid, "nom": c.get("nom") or e["nom"], "cp": c.get("cp", ""),
                "ville": c.get("ville", ""), "adresse": c.get("adresse", ""),
                "lat": lat, "lon": lon, "salles": {}})
            local_centre[cid] = ckey
        default_ckey = next(iter(local_centre.values())) if local_centre else None
        # salles
        rooms = st.get("rooms", {})
        for rid, r in rooms.items():
            ckey = local_centre.get(r.get("centre_id"), default_ckey)
            if ckey is None:
                continue
            e["centres"][ckey]["salles"].setdefault(rid, {
                "id": rid, "nom": r.get("name", rid), "theme": r.get("theme", "aventure"),
                "difficulty": r.get("difficulty"), "duree": r.get("duration"),
                "joueurs_min": r.get("min_players"), "joueurs_max": r.get("max_players"),
                "_book": 0, "_tot": 0, "_far_tot": 0, "_far_free": 0,
                "_conf": 0, "_prices": [], "n_sessions": 0})
        # sessions -> rattachées à leur salle
        for sess in sessions.values():
            all_sess += 1
            rid = sess.get("room_id")
            r = rooms.get(rid, {})
            ckey = local_centre.get(r.get("centre_id"), default_ckey)
            if ckey is None or rid not in e["centres"][ckey]["salles"]:
                continue
            sl = e["centres"][ckey]["salles"][rid]
            sl["n_sessions"] += 1
            if sess.get("statut") in ("reserve", "libre_fin"):
                sl["_conf"] += 1                       # créneau "tranché" = signal réel
            themes[sl["theme"]] += 1
            pm = sess.get("prix_total_moyen")
            if pm:
                sl["_prices"].append(pm); all_prices.append(pm)
            try:
                d = datetime.strptime(sess["date"], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                continue
            if not locked and today <= d <= horizon:
                sl["_tot"] += 1
                if d >= far:                       # fenêtre lointaine J+4..J+7
                    sl["_far_tot"] += 1
                    if sess.get("dispo") and not sess.get("booked"):
                        sl["_far_free"] += 1
                ji, bk = d.weekday(), bucket_of(sess.get("heure"))
                hm[(ji, bk)][1] += 1
                if sess.get("booked"):
                    sl["_book"] += 1
                    hm[(ji, bk)][0] += 1

    # ── consolidation hiérarchique ──
    g_book = g_tot = 0
    enseignes, map_points, top_salles = [], [], []
    for e in ens.values():
        centres_out = []
        for ce in e["centres"].values():
            salles_out, c_book, c_tot, c_prices = [], 0, 0, []
            for sl in ce["salles"].values():
                # salle "fermée/bloquée" : a des créneaux en fin de fenêtre mais
                # AUCUN libre même en J+4..J+7 -> ce n'est pas une vraie demande.
                suspect = sl["_far_tot"] >= 3 and sl["_far_free"] == 0
                fill = pct(sl["_book"], sl["_tot"])
                prix = med(sl["_prices"])
                if not suspect:                         # exclue du taux d'occupation
                    c_book += sl["_book"]; c_tot += sl["_tot"]
                    g_book += sl["_book"]; g_tot += sl["_tot"]
                if prix:
                    c_prices.append(prix)
                conf = ("élevée" if sl["_conf"] >= 8 else "moyenne" if sl["_conf"] >= 2 else "faible")
                so = {"id": sl["id"], "nom": sl["nom"], "theme": sl["theme"],
                      "difficulty": sl["difficulty"], "duree": sl["duree"],
                      "joueurs_min": sl["joueurs_min"], "joueurs_max": sl["joueurs_max"],
                      "prix_moyen_session": prix, "fill": fill, "suspect_closed": suspect,
                      "confidence": conf, "n_confirmed": sl["_conf"],
                      "n_sessions": sl["n_sessions"], "n_booked": sl["_book"]}
                salles_out.append(so)
                if sl["_tot"] >= 5 and not suspect:
                    top_salles.append({**so, "centre": ce["nom"], "enseigne": e["nom"], "cp": ce["cp"]})
            centres_out.append({
                "id": ce["id"], "nom": ce["nom"], "cp": ce["cp"], "ville": ce["ville"],
                "adresse": ce["adresse"], "lat": ce["lat"], "lon": ce["lon"],
                "n_salles": len(salles_out), "fill_now": pct(c_book, c_tot),
                "prix_moyen_session": med(c_prices),
                "salles": sorted(salles_out, key=lambda x: x["fill"], reverse=True)})
            map_points.append({"centre": ce["nom"], "enseigne": e["nom"], "lat": ce["lat"],
                               "lon": ce["lon"], "cp": ce["cp"], "fill": pct(c_book, c_tot),
                               "n_salles": len(salles_out)})
        n_salles = sum(c["n_salles"] for c in centres_out)
        e_prices = [c["prix_moyen_session"] for c in centres_out if c["prix_moyen_session"]]
        e_fill = med([c["fill_now"] for c in centres_out if c["fill_now"]]) or 0.0
        enseignes.append({
            "id": e["id"], "nom": e["nom"], "website": e["website"],
            "n_centres": len(centres_out), "n_salles": n_salles,
            "fill_now": e_fill, "prix_moyen_session": med(e_prices),
            "centres": sorted(centres_out, key=lambda x: x["fill_now"], reverse=True)})

    n_centres = sum(en["n_centres"] for en in enseignes)
    n_salles = sum(en["n_salles"] for en in enseignes)
    kpis = {
        "n_enseignes": len(enseignes), "n_centres": n_centres, "n_salles": n_salles,
        "n_sessions": all_sess, "n_open": n_open_companies,
        "fill_moyenne": pct(g_book, g_tot),
        "prix_moyen_session": med(all_prices),
    }
    heatmap = {"jours": JOURS, "buckets": BUCKETS,
               "matrix": [[pct(*hm[(ji, bk)]) for bk in BUCKETS] for ji in range(7)]}
    out = {
        "_meta": {"generated": now_iso(), "n_files": len(files), "window": "J..J+7"},
        "kpis": kpis,
        "enseignes": sorted(enseignes, key=lambda x: (x["n_salles"], x["fill_now"]), reverse=True),
        "map_points": [p for p in map_points if p["lat"]],
        "heatmap": heatmap,
        "themes": dict(sorted(themes.items(), key=lambda x: -x[1])),
        "top_salles": sorted(top_salles, key=lambda x: x["fill"], reverse=True)[:25],
        "prix_distribution": all_prices,
    }
    write_json(OUT_FILE, out)
    print(f"[compute] {len(enseignes)} enseignes · {n_centres} centres · {n_salles} salles · "
          f"{all_sess} sessions | fill {kpis['fill_moyenne']}% | "
          f"prix moyen/session {kpis['prix_moyen_session']}€")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
