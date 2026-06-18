#!/usr/bin/env python3
"""escape_geocode.py — géocode les adresses des enseignes (4escape ne remplit
pas le CP : geo=[null,null]). Utilise Nominatim/OpenStreetMap (1 req/s, cache
idempotent). Écrit lat/lon/cp dans le catalogue + propage aux salles.

Respecte la politesse Nominatim : User-Agent descriptif, max 1 req/s, et on ne
re-géocode jamais une enseigne déjà résolue (cache `escape_geo_cache.json`).
"""
from __future__ import annotations

import time
import urllib.parse as up
from urllib.request import Request, urlopen

from safestore import read_json, write_json

CATALOG_FILE = "escape_4escape_catalog.json"
CACHE_FILE = "escape_geo_cache.json"
UA = "escape-idf-observatory/1.0 (benchmark research)"


def geocode(query: str):
    q = up.urlencode({"q": query, "format": "json", "addressdetails": 1, "limit": 1})
    req = Request("https://nominatim.openstreetmap.org/search?" + q,
                  headers={"User-Agent": UA, "Accept-Language": "fr"})
    try:
        import json
        data = json.loads(urlopen(req, timeout=15).read().decode("utf-8", "replace"))
    except Exception:
        return None
    if not data:
        return None
    a = data[0]
    return {"lat": float(a["lat"]), "lon": float(a["lon"]),
            "cp": (a.get("address", {}) or {}).get("postcode", "")}


def main() -> int:
    catalog = read_json(CATALOG_FILE, {}) or {}
    companies = catalog.get("companies", {})
    rooms = catalog.get("rooms", {})
    cache = read_json(CACHE_FILE, {}) or {}
    done = 0
    for comp, info in companies.items():
        if info.get("lat") and info.get("cp"):
            continue
        street = info.get("street", "").strip()
        if not street:
            continue
        city = info.get("city") or "Paris"
        key = f"{street}, {city}, France"
        geo = cache.get(key)
        if geo is None:
            geo = geocode(key) or geocode(f"{street}, Paris, France")
            cache[key] = geo or {}
            write_json(CACHE_FILE, cache)
            time.sleep(1.1)  # politesse Nominatim
        if geo:
            info.update({"lat": geo["lat"], "lon": geo["lon"],
                         "cp": geo.get("cp") or info.get("cp", "")})
            for rid, r in rooms.items():
                if r.get("company") == comp:
                    r["lat"], r["lon"] = geo["lat"], geo["lon"]
                    if geo.get("cp"):
                        r["cp"] = geo["cp"]
            done += 1
            print(f"[geo] {info.get('org_name', comp)[:28]:28} {geo.get('cp','?'):6} "
                  f"{geo['lat']:.4f},{geo['lon']:.4f}")
        else:
            print(f"[geo] {comp}: échec géocodage ({street})")
    write_json(CATALOG_FILE, catalog)
    print(f"[geo] {done} enseignes géocodées.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
