#!/usr/bin/env python3
"""escape_live_compute.py — fusionne le catalogue enrichi (rooms + prix + geo)
avec les observations live (dispo / lead_days) en `escape_live_data.json`,
consommé par le dashboard RÉEL `escape_live.html`.

100 % données réelles 4escape. L'occupation par créneau se construira avec
l'historique ; pour l'instant on expose la DEMANDE (lead_days = délai jusqu'au
prochain créneau libre) + dispo instantanée + prix + métadonnées.
"""
from __future__ import annotations

import glob
from collections import defaultdict
from datetime import datetime, timezone

from safestore import read_json, write_json

CATALOG = "escape_4escape_catalog.json"
OBS_GLOB = "escape_data/4escape/*.json"
OUT = "escape_live_data.json"


def latest_obs() -> dict:
    """room_id -> dernière observation (+ nb d'événements 'réservé')."""
    out = {}
    for f in glob.glob(OBS_GLOB):
        store = read_json(f, {}) or {}
        for rid, rs in store.get("rooms", {}).items():
            obs = rs.get("observations", [])
            if obs:
                n_res = sum(1 for o in obs if o.get("event") == "prev_slot_reserved")
                out[rid] = {**obs[-1], "n_obs": len(obs), "n_reserved": n_res}
    return out


def main() -> int:
    cat = read_json(CATALOG, {}) or {}
    rooms = cat.get("rooms", {})
    companies = cat.get("companies", {})
    obs = latest_obs()

    rooms_out = []
    by_centre = defaultdict(lambda: {"rooms": 0, "leads": [], "avail_now": 0,
                                     "prices": [], "n_obs": 0})
    for rid, r in rooms.items():
        comp = r.get("company")
        info = companies.get(comp, {})
        o = obs.get(rid, {})
        lead = o.get("lead_days")
        row = {
            "room_id": rid, "room": r.get("room_name"), "centre": info.get("org_name") or r.get("company_name"),
            "cp": r.get("cp") or info.get("cp"), "lat": r.get("lat") or info.get("lat"),
            "lon": r.get("lon") or info.get("lon"),
            "theme": r.get("theme"), "difficulty": r.get("difficulty"),
            "duration": r.get("duration"), "min_players": r.get("min_players"),
            "max_players": r.get("max_players"),
            "prix_min": r.get("prix_min"), "prix_max": r.get("prix_max"),
            "prix_grille": r.get("prix_grille"),
            "reservation_url": r.get("reservation_url") or info.get("website"),
            "available": o.get("available"), "next_date": o.get("next_date"),
            "next_time": o.get("next_time"), "lead_days": lead,
            "n_obs": o.get("n_obs", 0), "n_reserved": o.get("n_reserved", 0),
        }
        rooms_out.append(row)
        c = by_centre[row["centre"]]
        c["rooms"] += 1
        c["n_obs"] += row["n_obs"]
        if lead is not None:
            c["leads"].append(lead)
        if o.get("available") and lead == 0:
            c["avail_now"] += 1
        if r.get("prix_min"):
            c["prices"].append(r["prix_min"])

    centres = []
    for centre, c in by_centre.items():
        info = next((i for i in companies.values()
                     if (i.get("org_name") or "") == centre), {})
        lead_avg = round(sum(c["leads"]) / len(c["leads"]), 1) if c["leads"] else None
        centres.append({
            "centre": centre, "cp": info.get("cp"), "lat": info.get("lat"),
            "lon": info.get("lon"), "website": info.get("website"),
            "phone": info.get("phone"), "n_rooms": c["rooms"],
            "lead_days_avg": lead_avg,
            "demand_pct": round(100 * (c["rooms"] - c["avail_now"]) / c["rooms"], 1) if c["rooms"] else 0,
            "prix_min": min(c["prices"]) if c["prices"] else None,
            "prix_grille": info.get("prix_grille"),
        })
    centres.sort(key=lambda x: (x["lead_days_avg"] or 0), reverse=True)
    rooms_out.sort(key=lambda x: (x["lead_days"] if x["lead_days"] is not None else -1), reverse=True)

    priced = [r for r in rooms_out if r["prix_min"]]
    kpis = {
        "n_centres": len(centres), "n_rooms": len(rooms_out),
        "n_observations": sum(r["n_obs"] for r in rooms_out),
        "lead_days_moyen": round(sum(r["lead_days"] for r in rooms_out if r["lead_days"] is not None)
                                 / max(1, sum(1 for r in rooms_out if r["lead_days"] is not None)), 1),
        "prix_min_marche": min((r["prix_min"] for r in priced), default=None),
        "n_salles_tarifees": len(priced),
    }
    out = {
        "_meta": {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "source": "escapegame.fr / 4escape /api/public", "mode": "réel",
                  "note": "lead_days = délai (j) au prochain créneau libre = proxy de demande. "
                          "Occupation par créneau en cours de construction (historique)."},
        "kpis": kpis, "centres": centres, "rooms": rooms_out,
    }
    write_json(OUT, out)
    print(f"[live-compute] {len(centres)} centres, {len(rooms_out)} salles "
          f"({len(priced)} tarifées) -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
