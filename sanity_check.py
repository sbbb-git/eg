#!/usr/bin/env python3
"""sanity_check.py — flag les centres "stale" (pas de relevé depuis >16h).

Sortie : `escape_sanity.json` + code retour non-zéro si au moins un centre est
stale (exploitable par le workflow sanity.yml pour ouvrir une issue GitHub).

  python3 sanity_check.py            # seuil 16h
  python3 sanity_check.py --hours 24
"""
from __future__ import annotations

import argparse
import glob
from datetime import datetime, timezone

from safestore import read_json, write_json

DATA_GLOB = "escape_data/*_data.json"
OUT_FILE = "escape_sanity.json"


def parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=16.0)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    files = sorted(glob.glob(DATA_GLOB))
    fresh, stale, broken = [], [], []

    for f in files:
        store = read_json(f, {}) or {}
        label = store.get("label", f)
        last = parse_iso((store.get("_meta") or {}).get("last_scrape", ""))
        if last is None:
            broken.append({"label": label, "file": f, "reason": "no last_scrape"})
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_h = (now - last).total_seconds() / 3600
        rec = {"label": label, "age_hours": round(age_h, 1),
               "last_scrape": last.isoformat(timespec="seconds")}
        (stale if age_h > args.hours else fresh).append(rec)

    report = {
        "generated": now.isoformat(timespec="seconds"),
        "threshold_hours": args.hours,
        "n_total": len(files), "n_fresh": len(fresh),
        "n_stale": len(stale), "n_broken": len(broken),
        "stale": sorted(stale, key=lambda r: -r["age_hours"]),
        "broken": broken,
    }
    write_json(OUT_FILE, report)

    print(f"[sanity] total={len(files)} fresh={len(fresh)} "
          f"stale={len(stale)} broken={len(broken)} (seuil {args.hours}h)")
    for r in report["stale"]:
        print(f"  STALE {r['age_hours']}h  {r['label']}")
    for r in broken:
        print(f"  BROKEN {r['label']} ({r['reason']})")

    return 1 if (stale or broken) else 0


if __name__ == "__main__":
    raise SystemExit(main())
