#!/usr/bin/env python3
"""escape_prices_site.py — extraction best-effort des grilles de prix sur les
SITES des enseignes dont l'API 4escape booking-data-json est verrouillée (401).

Stratégie (par ordre de fiabilité) :
  1) grille "N joueurs ... XX€" -> mapping joueur->prix (fiable).
  2) prix "/personne|/joueur" -> ensemble de prix /pers (min/max, pas de mapping).
On ne garde que des prix /pers plausibles (15-90€). Marqué prix_source="site".

Idempotent : on ne touche pas une enseigne déjà tarifée par l'API.
  python3 escape_prices_site.py
"""
from __future__ import annotations

import re
import time
from urllib.request import Request, urlopen

from safestore import read_json, write_json

CATALOG = "escape_4escape_catalog.json"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
PATHS = ["", "tarifs/", "tarifs", "reserver/", "reservation/", "nos-tarifs/",
         "prix/", "tarif/", "reservations/"]
MIN_PP, MAX_PP = 15, 90   # bornes plausibles d'un prix /pers escape room


def fetch(url: str, tmo: int = 10) -> str:
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=tmo) as r:
            return r.read(900_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def extract_grid(html: str) -> tuple[dict | None, list[int]]:
    """Retourne (grille joueur->prix | None, liste prix/pers plausibles)."""
    grid = {}
    for n, p in re.findall(r'(\d)\s*(?:à\s*\d\s*)?joueurs?[^0-9€]{0,40}?(\d{2,3})\s*€', html, re.I):
        pp = int(p)
        if MIN_PP <= pp <= MAX_PP and 1 <= int(n) <= 9:
            grid[n] = pp
    per_pers = sorted({int(x) for x in
                       re.findall(r'(\d{2,3})\s*€\s*(?:/|par|par\s+)?\s*(?:joueur|personne|pers)\b', html, re.I)
                       if MIN_PP <= int(x) <= MAX_PP})
    return (grid or None), per_pers


def main() -> int:
    catalog = read_json(CATALOG, {}) or {}
    companies = catalog.get("companies", {})
    rooms = catalog.get("rooms", {})
    done = 0
    for comp, info in companies.items():
        if info.get("prices_status") == "ok" or not info.get("website"):
            continue
        if info.get("prix_min") and info.get("prix_source") == "site":
            continue  # déjà fait
        site = info["website"].rstrip("/") + "/"
        grid, per_pers = None, []
        for p in PATHS:
            g, pp = extract_grid(fetch(site + p))
            if g and len(g) >= 2:
                grid, per_pers = g, pp
                break
            if pp and not per_pers:
                per_pers = pp
            time.sleep(0.3)
        vals = list(grid.values()) if grid else per_pers
        if not vals:
            info["prices_status"] = info.get("prices_status", "no_site_price")
            continue
        info["prix_min"], info["prix_max"] = min(vals), max(vals)
        info["prix_source"] = "site"
        if grid:
            info["prix_grille"] = grid
        for rid, r in rooms.items():
            if r.get("company") == comp and not r.get("prix_min"):
                r["prix_min"], r["prix_max"] = info["prix_min"], info["prix_max"]
                r["prix_source"] = "site"
                if grid:
                    r["prix_grille"] = grid
        done += 1
        print(f"[prix-site] {info.get('org_name', comp)[:26]:26} "
              f"{info['prix_min']}-{info['prix_max']}€ {'grille' if grid else '(min/max)'}")
    catalog.setdefault("_meta", {})["prices_site_updated"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat(timespec="seconds")
    write_json(CATALOG, catalog)
    print(f"[prix-site] {done} enseignes tarifées via leur site.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
