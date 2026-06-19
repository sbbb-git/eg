#!/usr/bin/env python3
"""escape_merge_escapers.py — complète l'annuaire IDF avec the-escapers.com.

escapegame.fr (sitemap) donne 153 venues ; the-escapers.com en référence ~256
en IDF. On fusionne les manquants dans escape_idf_directory.json (dédup par slug
normalisé), en taguant la source et en flaggant les entrées qui ressemblent à
des lieux non-escape (musées, one-off events) pour pouvoir les filtrer.

Les venues ajoutées n'ont pas encore de coords (pages SPA) -> map plus tard ;
elles apparaissent dans la TABLE de l'annuaire (objectif : exhaustivité).
"""
from __future__ import annotations

import re
from urllib.request import Request, urlopen

from safestore import read_json, write_json

SITEMAP = "https://www.the-escapers.com/sitemap.xml"
DIRECTORY = "escape_idf_directory.json"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"

DEPT = {"75": "Paris", "77": "Seine-et-Marne", "78": "Yvelines", "91": "Essonne",
        "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis", "94": "Val-de-Marne",
        "95": "Val-d'Oise"}
# commune -> dept (repris de escape_idf_directory)
COMMUNES = {
    "paris": "75", "boulogne-billancourt": "92", "issy-les-moulineaux": "92",
    "clichy": "92", "levallois-perret": "92", "neuilly-sur-seine": "92",
    "courbevoie": "92", "puteaux": "92", "nanterre": "92", "rueil-malmaison": "92",
    "asnieres-sur-seine": "92", "colombes": "92", "la-garenne-colombes": "92",
    "suresnes": "92", "malakoff": "92", "montrouge": "92", "vanves": "92",
    "meudon": "92", "clamart": "92", "antony": "92", "sceaux": "92",
    "chatillon": "92", "bagneux": "92",
    "montreuil": "93", "saint-denis": "93", "saint-ouen": "93",
    "aubervilliers": "93", "pantin": "93", "bondy": "93", "bagnolet": "93",
    "les-lilas": "93", "romainville": "93", "noisy-le-grand": "93", "rosny-sous-bois": "93",
    "vincennes": "94", "saint-mande": "94", "creteil": "94", "maisons-alfort": "94",
    "charenton-le-pont": "94", "ivry-sur-seine": "94", "vitry-sur-seine": "94",
    "cachan": "94", "gentilly": "94", "le-kremlin-bicetre": "94",
    "fontenay-sous-bois": "94", "nogent-sur-marne": "94", "champigny-sur-marne": "94",
    "saint-maur-des-fosses": "94", "bry-sur-marne": "94", "arcueil": "94",
    "versailles": "78", "saint-germain-en-laye": "78", "poissy": "78",
    "sartrouville": "78", "velizy-villacoublay": "78",
    "argenteuil": "95", "cergy": "95", "pontoise": "95",
    "massy": "91", "palaiseau": "91", "evry": "91", "corbeil-essonnes": "91",
    "savigny-sur-orge": "91", "melun": "77", "meaux": "77", "chelles": "77",
    "torcy": "77", "serris": "77", "noisiel": "77",
}
# mots indiquant un lieu non-escape (musée, événementiel, institutionnel)
NOISE = re.compile(r"musee|bibliotheque|universite|ecole-normale|ligue|archives|"
                   r"conciergerie|monnaie|cirque|bateaux|parlons-en|psl|"
                   r"maison-de-la|departementales|prod$|spectacles", re.I)


def fetch(url: str) -> str:
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=25) as r:
            return r.read(10_000_000).decode("utf-8", "replace")
    except Exception:
        return ""


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main() -> int:
    sm = fetch(SITEMAP)
    locs = re.findall(r"<loc>([^<]+)</loc>", sm)
    te = []
    for l in locs:
        m = re.match(r"https://www\.the-escapers\.com/escape-game/([a-z0-9\-]+)/([a-z0-9\-]+)$", l)
        if m and m.group(1) in COMMUNES:
            te.append((m.group(1), m.group(2), l))
    print(f"[merge] the-escapers IDF: {len(te)} venues")

    directory = read_json(DIRECTORY, {}) or {}
    venues = directory.get("venues", [])
    existing = {norm(v["slug"]) for v in venues}
    added = 0
    for city, slug, url in te:
        if norm(slug) in existing:
            continue
        existing.add(norm(slug))
        dept = COMMUNES.get(city, "")
        venues.append({
            "slug": slug, "city": city, "url": url,
            "name": slug.replace("-", " ").title(),
            "cp": "", "dept": dept, "dept_name": DEPT.get(dept, ""),
            "lat": None, "lon": None,
            "covered_4escape": False, "company_4escape": None,
            "rooms": [], "n_rooms": None,
            "source": "the-escapers",
            "likely_event": bool(NOISE.search(slug)),
        })
        added += 1

    for v in venues:
        v.setdefault("source", "escapegame.fr")
        v.setdefault("likely_event", False)

    real = [v for v in venues if not v.get("likely_event")]
    by_dept: dict = {}
    for v in real:
        by_dept[v.get("dept") or "?"] = by_dept.get(v.get("dept") or "?", 0) + 1
    directory["venues"] = venues
    directory.setdefault("_meta", {}).update({
        "n_venues": len(venues), "n_real": len(real),
        "n_likely_event": sum(1 for v in venues if v.get("likely_event")),
        "sources": ["escapegame.fr", "the-escapers.com"],
        "by_dept_real": by_dept,
    })
    write_json(DIRECTORY, directory)
    print(f"[merge] +{added} venues -> {len(venues)} total "
          f"({len(real)} escape games réels, {len(venues)-len(real)} lieux/events filtrables)")
    print(f"[merge] par département (réels): {by_dept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
