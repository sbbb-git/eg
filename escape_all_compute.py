#!/usr/bin/env python3
"""escape_all_compute.py — agrège escape_data/4escape_all/*.json -> escape_all_data.json.

Deux mesures d'occupation complémentaires :
  - fill_now (enseignes "open" : booking-data-json accessible) = booked / total
    sur la fenêtre J..J+7. Occupation RÉELLE instantanée (pas besoin d'attendre).
  - occ_resolved (logique padel) = reserve / (reserve + libre_fin) sur les
    créneaux passés/tranchés. Se renforce à chaque relevé.
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
    hh = int(h[:2])
    return ("10-13" if hh < 13 else "13-16" if hh < 16 else "16-19"
            if hh < 19 else "19-21" if hh < 21 else "21-24")


def pct(a: int, b: int) -> float:
    return round(100 * a / b, 1) if b else 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def per_player(grid: dict) -> float | None:
    """Prix indicatif/joueur : grille {nb:montant}. Si le montant ressemble à un
    total équipe (montant > 60), on divise par le nb de joueurs."""
    if not grid:
        return None
    vals = []
    for nb, amt in grid.items():
        nb = int(nb)
        vals.append(round(amt / nb, 1) if amt > 60 and nb else amt)
    return round(sorted(vals)[len(vals) // 2], 1) if vals else None


def main() -> int:
    geo = read_json(GEO_FILE, {}) or {}
    files = sorted(glob.glob(DATA_GLOB))
    today = datetime.utcnow().date()
    horizon = today + timedelta(days=7)

    centres, all_prices = [], []
    hm = defaultdict(lambda: [0, 0])            # (ji,bucket) -> [booked, total] (open, fenêtre)
    rooms = defaultdict(lambda: {"booked": 0, "total": 0, "centre": "", "cp": "",
                                 "theme": "", "prix": None, "difficulty": None})
    themes = defaultdict(int)
    n_sess_tot = n_open = 0

    for f in files:
        st = read_json(f, {}) or {}
        sess = st.get("sessions", {})
        if not sess:
            continue
        sess = list(sess.values()) if isinstance(sess, dict) else sess
        roommeta = st.get("rooms", {})
        locked = st.get("_meta", {}).get("prices_locked", True)
        if not locked:
            n_open += 1
        n_sess_tot += len(sess)
        c_book = c_tot = 0                        # fenêtre J..J+7 (open)
        r_res = r_resolved = 0                    # padel (passés)
        cprices = []
        for s in sess:
            try:
                d = datetime.strptime(s["date"], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                continue
            rid = s.get("room_id")
            rmeta = roommeta.get(rid, {})
            theme = rmeta.get("theme", "aventure")
            pp = per_player(s.get("prix") or {})
            if pp:
                cprices.append(pp); all_prices.append(pp)
            themes[theme] += 1
            ji, bk = d.weekday(), bucket_of(s.get("heure", "12:00"))
            # occupation réelle (open) sur la fenêtre
            if not locked and today <= d <= horizon:
                c_tot += 1
                hm[(ji, bk)][1] += 1
                if s.get("booked"):
                    c_book += 1
                    hm[(ji, bk)][0] += 1
                rk = f"{st.get('org_name', st.get('company'))}|{rmeta.get('name', rid)}"
                rr = rooms[rk]
                rr.update(centre=st.get("org_name", st.get("company")), cp=st.get("cp", ""),
                          theme=theme, prix=pp, difficulty=rmeta.get("difficulty"))
                rr["total"] += 1
                rr["booked"] += 1 if s.get("booked") else 0
            # padel : créneaux tranchés
            if s.get("statut") in ("reserve", "libre_fin"):
                r_resolved += 1
                if s["statut"] == "reserve":
                    r_res += 1

        cp = st.get("cp", "")
        coords = geo.get(cp) or geo.get("_dept_centroids", {}).get(cp[:2]) or [48.8566, 2.3522]
        centres.append({
            "label": st.get("org_name", st.get("company")), "company": st.get("company"),
            "cp": cp, "website": st.get("website", ""), "platform": "4escape",
            "n_rooms": len(roommeta), "n_sessions": len(sess),
            "open": not locked,
            "fill_now": pct(c_book, c_tot), "n_booked": c_book, "n_slots_window": c_tot,
            "occ_resolved": pct(r_res, r_resolved), "n_resolved": r_resolved,
            "prix_median": round(sorted(cprices)[len(cprices) // 2], 1) if cprices else None,
            "lat": coords[0], "lon": coords[1],
        })

    tot_book = sum(c["n_booked"] for c in centres)
    tot_win = sum(c["n_slots_window"] for c in centres)
    kpis = {
        "n_centres": len(centres), "n_open": n_open, "n_rooms": len(rooms),
        "n_sessions": n_sess_tot,
        "fill_moyenne": pct(tot_book, tot_win),
        "prix_median_joueur": round(sorted(all_prices)[len(all_prices) // 2], 1) if all_prices else None,
    }
    heatmap = {"jours": JOURS, "buckets": BUCKETS,
               "matrix": [[pct(*hm[(ji, bk)]) for bk in BUCKETS] for ji in range(7)]}
    top_rooms = sorted(
        [{"room": k.split("|", 1)[1], "centre": r["centre"], "cp": r["cp"], "theme": r["theme"],
          "prix": r["prix"], "fill": pct(r["booked"], r["total"]), "n": r["total"]}
         for k, r in rooms.items() if r["total"] >= 5],
        key=lambda x: x["fill"], reverse=True)
    out = {
        "_meta": {"generated": now_iso(), "n_files": len(files), "source": "4escape (réseau IDF)",
                  "window": "J..J+7"},
        "kpis": kpis,
        "centres": sorted(centres, key=lambda c: (c["fill_now"], c["occ_resolved"]), reverse=True),
        "map_points": [{"label": c["label"], "lat": c["lat"], "lon": c["lon"], "cp": c["cp"],
                        "fill": c["fill_now"], "open": c["open"], "n_rooms": c["n_rooms"]}
                       for c in centres],
        "heatmap": heatmap,
        "top_rooms": top_rooms[:25],
        "themes": dict(sorted(themes.items(), key=lambda x: -x[1])),
        "prix_distribution": all_prices,
    }
    write_json(OUT_FILE, out)
    print(f"[compute-all] {len(centres)} centres ({n_open} open), {len(rooms)} rooms, "
          f"{n_sess_tot} sessions | fill moy {kpis['fill_moyenne']}% | "
          f"prix médian {kpis['prix_median_joueur']}€/j")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
