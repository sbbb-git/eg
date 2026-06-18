#!/usr/bin/env python3
"""escape_idf_compute.py — agrège tous les escape_data/*_data.json en un
store unifié `escape_idf_data.json` consommé par le dashboard.

Définition du taux d'occupation : sur les créneaux *résolus* (passés ou
réservés), occupation = reserve / (reserve + libre_fin). Les créneaux futurs
encore "libre" ne comptent pas comme "raté" mais alimentent la demande live.
"""
from __future__ import annotations

import glob
import os
from collections import defaultdict
from datetime import datetime, timezone

from safestore import read_json, write_json

DATA_GLOB = "escape_data/*_data.json"
GEO_FILE = "escape_geo_cp.json"
OUT_FILE = "escape_idf_data.json"
JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
HEURE_BUCKETS = ["10-13", "13-16", "16-19", "19-21", "21-24"]


def bucket_of(heure: str) -> str:
    h = int(heure[:2])
    if h < 13:
        return "10-13"
    if h < 16:
        return "13-16"
    if h < 19:
        return "16-19"
    if h < 21:
        return "19-21"
    return "21-24"


def occ_rate(reserved: int, resolved: int) -> float:
    return round(100 * reserved / resolved, 1) if resolved else 0.0


def main() -> int:
    geo = read_json(GEO_FILE, {}) or {}
    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        print(f"[compute] aucun fichier {DATA_GLOB}")
        write_json(OUT_FILE, {"_meta": {"generated": _now(), "empty": True},
                              "kpis": {}, "centres": [], "map_points": [],
                              "heatmap": {}, "top_rooms": [], "monthly": {}})
        return 0

    now = datetime.now()
    today = now.date()
    centres = []
    all_sessions = []
    # heatmap accumulators: (jour_idx, bucket) -> [reserved, resolved]
    hm = defaultdict(lambda: [0, 0])
    # top créneaux: (jour, bucket) -> [reserved, resolved]
    top_slots = defaultdict(lambda: [0, 0])
    # rooms: room_key -> stats
    rooms = defaultdict(lambda: {"reserved": 0, "resolved": 0, "centre": "",
                                 "cp": "", "prix_min": 999, "theme": ""})
    # monthly: centre -> month -> [reserved, resolved]
    monthly = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    for f in files:
        store = read_json(f, {}) or {}
        sessions = store.get("sessions", [])
        if not sessions:
            continue
        c_reserved = c_resolved = 0
        c_future_reserved = 0
        prices = []
        for s in sessions:
            all_sessions.append(s)
            d = datetime.strptime(s["date"], "%Y-%m-%d")
            ji = d.weekday()
            bk = bucket_of(s["heure"])
            prices.append(s.get("prix_min", 0))
            reserved = s.get("statut") == "reserve"
            # Occupation = mesurée uniquement sur les créneaux passés (issue
            # finale connue : reserve vs libre_fin). Les réservations sur des
            # créneaux futurs comptent comme "demande live", pas dans le taux.
            past = d.date() < today
            if past:
                c_resolved += 1
                hm[(ji, bk)][1] += 1
                top_slots[(JOURS[ji], bk)][1] += 1
                month = s["date"][:7]
                monthly[store["label"]][month][1] += 1
                if reserved:
                    c_reserved += 1
                    hm[(ji, bk)][0] += 1
                    top_slots[(JOURS[ji], bk)][0] += 1
                    monthly[store["label"]][month][0] += 1
            elif reserved:
                c_future_reserved += 1
            rk = f"{store['label']}|{s['room_name']}"
            rr = rooms[rk]
            rr["centre"] = store["label"]
            rr["cp"] = s.get("cp", "")
            rr["theme"] = s.get("theme", "")
            rr["prix_min"] = min(rr["prix_min"], s.get("prix_min", 999))
            if past:
                rr["resolved"] += 1
                if reserved:
                    rr["reserved"] += 1

        rate = occ_rate(c_reserved, c_resolved)
        coords = geo.get(store["cp"]) or geo.get("_dept_centroids", {}).get(store["cp"][:2]) or [48.8566, 2.3522]
        centres.append({
            "label": store["label"], "slug": store.get("slug"), "cp": store["cp"],
            "platform": store.get("platform"), "url": store.get("url"),
            "n_rooms": len(store.get("rooms", [])),
            "n_sessions": len(sessions),
            "n_resolved": c_resolved,
            "occ_rate": rate,
            "live_demand": c_future_reserved,
            "prix_median": round(sorted(prices)[len(prices) // 2], 1) if prices else 0,
            "lat": coords[0], "lon": coords[1],
        })

    # KPIs globaux
    tot_resolved = sum(c["n_resolved"] for c in centres)
    tot_reserved = sum(int(c["occ_rate"] / 100 * c["n_resolved"]) for c in centres)
    kpis = {
        "n_centres": len(centres),
        "n_rooms": len(rooms),
        "n_sessions": len(all_sessions),
        "occ_moyenne": occ_rate(tot_reserved, tot_resolved),
        "prix_median_global": round(
            sorted([c["prix_median"] for c in centres])[len(centres) // 2], 1) if centres else 0,
    }

    # heatmap matrix [jour][bucket] = %occ
    heatmap = {"jours": JOURS, "buckets": HEURE_BUCKETS, "matrix": []}
    for ji in range(7):
        row = []
        for bk in HEURE_BUCKETS:
            r, t = hm[(ji, bk)]
            row.append(occ_rate(r, t))
        heatmap["matrix"].append(row)

    # top rooms (>= 8 créneaux résolus pour être significatif)
    top_rooms = []
    for rk, rr in rooms.items():
        if rr["resolved"] >= 8:
            top_rooms.append({
                "room": rk.split("|", 1)[1], "centre": rr["centre"], "cp": rr["cp"],
                "theme": rr["theme"], "prix_min": rr["prix_min"],
                "occ_rate": occ_rate(rr["reserved"], rr["resolved"]),
                "n_resolved": rr["resolved"],
            })
    top_rooms.sort(key=lambda x: x["occ_rate"], reverse=True)

    # top créneaux jour×tranche
    top_creneaux = sorted(
        [{"jour": k[0], "tranche": k[1], "occ_rate": occ_rate(v[0], v[1]), "n": v[1]}
         for k, v in top_slots.items() if v[1] >= 5],
        key=lambda x: x["occ_rate"], reverse=True)

    # monthly evolution
    monthly_out = {}
    for centre, mdict in monthly.items():
        monthly_out[centre] = {m: occ_rate(v[0], v[1]) for m, v in sorted(mdict.items())}

    out = {
        "_meta": {"generated": _now(), "n_files": len(files),
                  "window": "J-3 .. J+30"},
        "kpis": kpis,
        "centres": sorted(centres, key=lambda c: c["occ_rate"], reverse=True),
        "map_points": [{"label": c["label"], "lat": c["lat"], "lon": c["lon"],
                        "occ_rate": c["occ_rate"], "cp": c["cp"],
                        "n_rooms": c["n_rooms"]} for c in centres],
        "heatmap": heatmap,
        "top_rooms": top_rooms[:20],
        "top_creneaux": top_creneaux[:15],
        "monthly": monthly_out,
    }
    write_json(OUT_FILE, out)
    print(f"[compute] {len(centres)} centres, {len(rooms)} rooms, "
          f"{len(all_sessions)} sessions -> {OUT_FILE} "
          f"(occ moy {kpis['occ_moyenne']}%)")
    return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
