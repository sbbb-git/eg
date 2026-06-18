#!/usr/bin/env python3
"""escape_idf_directory.py — annuaire COMPLET des escape games d'Île-de-France.

Source : sitemap escapegame.fr (sitemap-company.xml = 3500+ venues monde). On
filtre les communes IDF, on fetch chaque fiche et on extrait nom, CP, coords,
plateforme 4escape (si présente), salles. Objectif : avoir TOUS les escape
games IDF (pas seulement les 4escape avec widget de dispo).

Sortie : escape_idf_directory.json
  { _meta, venues:[ {slug, city, url, name, cp, lat, lon, dept,
                     covered_4escape, company_4escape, rooms[], n_rooms} ] }
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from safestore import read_json, write_json

SITEMAP = "https://www.escapegame.fr/sitemap-company.xml"
OUT = "escape_idf_directory.json"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"

# Communes IDF où l'on trouve des escape games (large ; Paris = gros du volume).
IDF_COMMUNES = {
    "paris", "boulogne-billancourt", "issy-les-moulineaux", "clichy",
    "levallois-perret", "neuilly-sur-seine", "courbevoie", "puteaux",
    "nanterre", "rueil-malmaison", "asnieres-sur-seine", "colombes",
    "la-garenne-colombes", "suresnes", "malakoff", "montrouge", "vanves",
    "meudon", "clamart", "antony", "sceaux", "chatillon", "bagneux",
    "montreuil", "saint-denis", "saint-ouen", "aubervilliers", "pantin",
    "bondy", "bagnolet", "les-lilas", "romainville", "noisy-le-grand",
    "rosny-sous-bois", "vincennes", "saint-mande", "creteil", "maisons-alfort",
    "charenton-le-pont", "ivry-sur-seine", "vitry-sur-seine", "cachan",
    "gentilly", "le-kremlin-bicetre", "fontenay-sous-bois", "nogent-sur-marne",
    "champigny-sur-marne", "saint-maur-des-fosses", "bry-sur-marne", "arcueil",
    "versailles", "saint-germain-en-laye", "poissy", "sartrouville",
    "argenteuil", "cergy", "pontoise", "massy", "palaiseau", "evry",
    "corbeil-essonnes", "savigny-sur-orge", "melun", "meaux", "chelles",
    "torcy", "serris", "noisiel", "vélizy-villacoublay", "velizy-villacoublay",
}
DEPT = {"75": "Paris", "77": "Seine-et-Marne", "78": "Yvelines", "91": "Essonne",
        "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis", "94": "Val-de-Marne",
        "95": "Val-d'Oise"}

# commune -> code département (la commune est fiable car = slug d'URL annuaire).
COMMUNE_DEPT = {
    "paris": "75",
    # 92
    "boulogne-billancourt": "92", "issy-les-moulineaux": "92", "clichy": "92",
    "levallois-perret": "92", "neuilly-sur-seine": "92", "courbevoie": "92",
    "puteaux": "92", "nanterre": "92", "rueil-malmaison": "92",
    "asnieres-sur-seine": "92", "colombes": "92", "la-garenne-colombes": "92",
    "suresnes": "92", "malakoff": "92", "montrouge": "92", "vanves": "92",
    "meudon": "92", "clamart": "92", "antony": "92", "sceaux": "92",
    "chatillon": "92", "bagneux": "92",
    # 93
    "montreuil": "93", "saint-denis": "93", "saint-ouen": "93",
    "aubervilliers": "93", "pantin": "93", "bondy": "93", "bagnolet": "93",
    "les-lilas": "93", "romainville": "93", "noisy-le-grand": "93",
    "rosny-sous-bois": "93",
    # 94
    "vincennes": "94", "saint-mande": "94", "creteil": "94",
    "maisons-alfort": "94", "charenton-le-pont": "94", "ivry-sur-seine": "94",
    "vitry-sur-seine": "94", "cachan": "94", "gentilly": "94",
    "le-kremlin-bicetre": "94", "fontenay-sous-bois": "94",
    "nogent-sur-marne": "94", "champigny-sur-marne": "94",
    "saint-maur-des-fosses": "94", "bry-sur-marne": "94", "arcueil": "94",
    # 78 / 95 / 91 / 77
    "versailles": "78", "saint-germain-en-laye": "78", "poissy": "78",
    "sartrouville": "78", "velizy-villacoublay": "78", "vélizy-villacoublay": "78",
    "argenteuil": "95", "cergy": "95", "pontoise": "95",
    "massy": "91", "palaiseau": "91", "evry": "91", "corbeil-essonnes": "91",
    "savigny-sur-orge": "91", "melun": "77", "meaux": "77", "chelles": "77",
    "torcy": "77", "serris": "77", "noisiel": "77",
}


def fetch(url: str, tmo: int = 15) -> str:
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=tmo) as r:
                return r.read(4_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
        except Exception:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return ""


def parse_fiche(url: str) -> dict | None:
    slug = url.rstrip("/").split("/")[-1]
    city = url.rstrip("/").split("/")[-2]
    html = fetch(url)
    if not html:
        return {"slug": slug, "city": city, "url": url, "error": "fetch"}
    # coords fiables = lien maps de la fiche (?ll=lat,lon)
    ll = re.search(r'[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)', html)
    lat = float(ll.group(1)) if ll else None
    lon = float(ll.group(2)) if ll else None
    # nom propre = 1er segment du <title> avant " enseigne"/"|"
    title = re.search(r'<title>([^<|]+)', html)
    raw = (title.group(1).strip() if title else slug.replace("-", " ").title())
    name = re.split(r"\s+enseigne\b", raw, 1)[0].strip()
    company = sorted(set(re.findall(r'data-room-id="([a-z0-9\-]+)/', html)))
    rooms = sorted(set(re.findall(r'data-room-name="([^"]+)"', html)))
    dept = COMMUNE_DEPT.get(city, "")
    return {
        "slug": slug, "city": city, "url": url, "name": name[:80],
        "cp": "", "dept": dept, "dept_name": DEPT.get(dept, ""),
        "lat": lat, "lon": lon,
        "covered_4escape": bool(company), "company_4escape": company[0] if company else None,
        "rooms": rooms, "n_rooms": len(rooms),
    }


def reverse_cp(venues: list[dict]) -> None:
    """Reverse-geocode coords -> CP précis (arrondissement Paris inclus).
    Caché dans escape_geo_cache.json, politesse Nominatim 1 req/s."""
    import json
    cache = read_json("escape_geo_cache.json", {}) or {}
    ua = "escape-idf-observatory/1.0 (benchmark research)"
    for v in venues:
        if not v.get("lat") or v.get("cp"):
            continue
        key = f"rev:{round(v['lat'],5)},{round(v['lon'],5)}"
        geo = cache.get(key)
        if geo is None:
            try:
                url = (f"https://nominatim.openstreetmap.org/reverse?format=json"
                       f"&lat={v['lat']}&lon={v['lon']}&addressdetails=1")
                d = json.loads(urlopen(Request(url, headers={"User-Agent": ua}),
                                       timeout=15).read().decode("utf-8", "replace"))
                geo = {"cp": (d.get("address", {}) or {}).get("postcode", "")}
            except Exception:
                geo = {}
            cache[key] = geo
            write_json("escape_geo_cache.json", cache)
            time.sleep(1.1)
        if geo.get("cp"):
            v["cp"] = geo["cp"]
            if not v.get("dept"):
                v["dept"] = geo["cp"][:2]
                v["dept_name"] = DEPT.get(geo["cp"][:2], "")


def main() -> int:
    sm = fetch(SITEMAP)
    locs = re.findall(r"<loc>([^<]+)</loc>", sm)
    idf_urls = [l for l in locs
                if re.match(r"https://www\.escapegame\.fr/([a-z0-9\-]+)/", l)
                and l.split("/")[3] in IDF_COMMUNES]
    print(f"[dir] {len(locs)} venues France -> {len(idf_urls)} candidates IDF")

    venues = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for i, v in enumerate(ex.map(parse_fiche, idf_urls), 1):
            if v:
                venues.append(v)
            if i % 25 == 0:
                print(f"[dir] {i}/{len(idf_urls)} fiches traitées")

    # garde celles confirmées IDF (commune IDF connue) ou 4escape
    idf = [v for v in venues if v.get("dept") or v.get("covered_4escape")]
    print(f"[dir] reverse-geocodage CP de {sum(1 for v in idf if v.get('lat'))} venues...")
    reverse_cp(idf)
    by_dept: dict[str, int] = {}
    n_4e = 0
    for v in idf:
        by_dept[v.get("dept") or "?"] = by_dept.get(v.get("dept") or "?", 0) + 1
        n_4e += 1 if v.get("covered_4escape") else 0
    out = {
        "_meta": {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "source": "escapegame.fr sitemap-company", "n_candidates": len(idf_urls),
                  "n_venues": len(idf), "n_4escape": n_4e, "by_dept": by_dept},
        "venues": sorted(idf, key=lambda v: (v.get("dept") or "z", v.get("cp") or "")),
    }
    write_json(OUT, out)
    print(f"[dir] ANNUAIRE IDF : {len(idf)} escape games "
          f"({n_4e} sur 4escape) -> {OUT}")
    print(f"[dir] par département : {by_dept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
