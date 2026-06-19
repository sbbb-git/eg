#!/usr/bin/env python3
"""escape_resolve_sites.py — résout les venues NON-4escape (track-2).

Pour chaque venue dont l'URL officielle est connue (`escape_venue_sites.json`,
alimenté via recherche web), détecte la PLATEFORME de réservation et extrait
une grille de prix best-effort. Écrit le résultat dans l'annuaire
`escape_idf_directory.json` (champs: website, platform, prix_min/max).

Process de scaling (les deux en parallèle) :
  1) recherche web "nom escape game commune" -> URL officielle
  2) ajouter slug->URL dans escape_venue_sites.json
  3) ce script résout plateforme + prix automatiquement

  python3 escape_resolve_sites.py
"""
from __future__ import annotations

import re
import time
from urllib.request import Request, urlopen

from safestore import read_json, write_json

SITES = "escape_venue_sites.json"
DIRECTORY = "escape_idf_directory.json"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
PATHS = ["", "reserver/", "reservation/", "tarifs/", "booking/", "reservations/", "nos-tarifs/"]

# signatures de plateforme de réservation (track-2)
SIGS = [
    ("4escape", r"4escape\.io|4escape\.app"),
    ("bsport", r"bsport\.io|[?&]company=\d+"),
    ("mindbody", r"mindbody|healcode|mb_site_id"),
    ("bookeo", r"bookeo"),
    ("anybuddy", r"anybuddy"),
    ("smeetz", r"smeetz"),
    ("guidap", r"guidap"),
    ("wechamber", r"wechamber"),
    ("regiondo", r"regiondo"),
    ("xola", r"xola"),
    ("fareharbor", r"fareharbor"),
    ("yurplan", r"yurplan"),
    ("weezevent", r"weezevent"),
    ("placeminute", r"placeminute"),
    ("woocommerce", r"woocommerce|wc-ajax"),
    ("wordpress", r"wp-content|wp-json"),
]
MIN_PP, MAX_PP = 15, 95


def fetch(url: str, tmo: int = 10) -> str:
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=tmo) as r:
            return r.read(900_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def detect_platform(html: str) -> str:
    for name, rx in SIGS:
        if re.search(rx, html, re.I):
            return name
    return "custom"


def extract_prices(html: str) -> tuple[int | None, int | None]:
    vals = {int(x) for x in re.findall(r'(\d{2,3})\s*€\s*(?:/|par)?\s*(?:joueur|personne|pers)\b', html, re.I)
            if MIN_PP <= int(x) <= MAX_PP}
    vals |= {int(p) for _, p in re.findall(r'(\d)\s*joueurs?[^0-9€]{0,40}?(\d{2,3})\s*€', html, re.I)
             if MIN_PP <= int(p) <= MAX_PP}
    return (min(vals), max(vals)) if vals else (None, None)


def main() -> int:
    seed = (read_json(SITES, {}) or {}).get("sites", {})
    directory = read_json(DIRECTORY, {}) or {}
    venues = {v["slug"]: v for v in directory.get("venues", [])}
    resolved = 0
    for slug, url in seed.items():
        base = url.rstrip("/") + "/"
        platform, pmin, pmax = "custom", None, None
        for p in PATHS:
            html = fetch(base + p)
            if not html:
                continue
            plat = detect_platform(html)
            if plat not in ("custom", "wordpress", "woocommerce"):
                platform = plat
            elif platform == "custom":
                platform = plat
            a, b = extract_prices(html)
            if a and not pmin:
                pmin, pmax = a, b
            if platform not in ("custom", "wordpress", "woocommerce") and pmin:
                break
            time.sleep(0.3)
        v = venues.get(slug)
        if v is not None:
            v["website"] = url
            v["platform"] = platform
            if pmin:
                v["prix_min"], v["prix_max"] = pmin, pmax
        resolved += 1
        print(f"[resolve] {slug:24} platform={platform:12} "
              f"prix={f'{pmin}-{pmax}€' if pmin else '—'}")
    if directory:
        directory.setdefault("_meta", {})["sites_resolved"] = sum(
            1 for v in directory.get("venues", []) if v.get("website"))
        write_json(DIRECTORY, directory)
    print(f"[resolve] {resolved} venues résolues. "
          f"Total annuaire avec site: {directory.get('_meta', {}).get('sites_resolved', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
