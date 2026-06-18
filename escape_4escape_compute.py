#!/usr/bin/env python3
"""escape_4escape_compute.py — agrège les observations 4escape (append-only)
en un instantané réel `escape_4escape_live.json`.

Métriques disponibles dès le 1er relevé :
  - next_date / next_time / lead_days par salle (proxy de demande : plus le
    prochain créneau libre est loin, plus la salle est demandée)
  - availability today / weekend
Métriques qui se construisent avec l'historique (≥ qq heures de relevés) :
  - n_reserved : nb d'événements "prochain créneau réservé" détectés
  - lead_days_trend : évolution du délai au prochain créneau libre
"""
from __future__ import annotations

import glob
from datetime import datetime, timezone

from safestore import read_json, write_json

OBS_GLOB = "escape_data/4escape/*.json"
OUT = "escape_4escape_live.json"


def main() -> int:
    files = sorted(glob.glob(OBS_GLOB))
    rooms_out, centres = [], {}
    total_obs = 0
    for f in files:
        store = read_json(f, {}) or {}
        comp = store.get("company")
        cname = store.get("company_name", comp)
        c = centres.setdefault(comp, {"company": comp, "company_name": cname,
            "n_rooms": 0, "lead_days": [], "n_reserved": 0, "n_avail_now": 0})
        for rid, rs in store.get("rooms", {}).items():
            obs = rs.get("observations", [])
            total_obs += len(obs)
            if not obs:
                continue
            last = obs[-1]
            n_reserved = sum(1 for o in obs if o.get("event") == "prev_slot_reserved")
            lead = last.get("lead_days")
            rooms_out.append({
                "room_id": rid, "room": rs.get("room_name"), "centre": cname,
                "theme": rs.get("theme"), "players": rs.get("players"),
                "city": rs.get("city"),
                "available": last.get("available"),
                "next_date": last.get("next_date"), "next_time": last.get("next_time"),
                "lead_days": lead, "platform": last.get("platform"),
                "n_obs": len(obs), "n_reserved": n_reserved,
            })
            c["n_rooms"] += 1
            if lead is not None:
                c["lead_days"].append(lead)
            c["n_reserved"] += n_reserved
            if last.get("available") and lead == 0:
                c["n_avail_now"] += 1

    centres_out = []
    for c in centres.values():
        lead = c.pop("lead_days")
        c["lead_days_avg"] = round(sum(lead) / len(lead), 1) if lead else None
        # demande instantanée : % de salles sans dispo le jour même
        c["demand_pct"] = round(100 * (c["n_rooms"] - c["n_avail_now"]) / c["n_rooms"], 1) if c["n_rooms"] else 0
        centres_out.append(c)

    rooms_out.sort(key=lambda r: (r["lead_days"] if r["lead_days"] is not None else -1), reverse=True)
    centres_out.sort(key=lambda c: (c["lead_days_avg"] or 0), reverse=True)

    out = {
        "_meta": {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "n_centres": len(centres_out), "n_rooms": len(rooms_out),
                  "n_observations": total_obs, "source": "escapegame.fr / 4escape.io",
                  "note": "lead_days = délai (jours) jusqu'au prochain créneau libre = "
                          "proxy de demande. Occupation/heatmap se construisent avec l'historique."},
        "centres": centres_out,
        "rooms_top_demande": rooms_out[:40],
    }
    write_json(OUT, out)
    m = out["_meta"]
    print(f"[4escape-compute] {m['n_centres']} enseignes, {m['n_rooms']} salles, "
          f"{m['n_observations']} observations -> {OUT}")
    if rooms_out:
        print("  top demande (lead_days):")
        for r in rooms_out[:8]:
            print(f"    {r['lead_days']:>3}j  {r['centre'][:24]:24} · {r['room']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
