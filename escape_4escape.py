#!/usr/bin/env python3
"""escape_4escape.py — source PRIMAIRE : annuaire escapegame.fr + API 4escape.

Découverte (post-analyse) :
  - escapegame.fr est l'annuaire ; chaque salle est une "card" portant
    data-room-id="<company>/<mongoid>", data-room-name, data-company-name,
    data-theme, data-levels, data-players, data-domain.
  - L'API publique de dispo (proxy escapegame) :
        GET https://availability.4escape.io/egfr/upcoming/<company>/<mongoid>
    -> renvoie UNIQUEMENT le PROCHAIN créneau disponible :
        {id, room, date:"YYYY-MM-DD", time:"15h50", available:true, book-url:"..."}
  - book-url -> <company>.4escape.io/redirect... => plateforme de résa sous-jacente.

Conséquence sur l'architecture (cf. ANALYSE_SCRAPING.md) :
  on ne voit pas tout le calendrier, seulement le "prochain créneau libre".
  On RECONSTRUIT la demande par OBSERVATION RÉPÉTÉE (append-only) :
    - lead_days = (prochaine date libre - aujourd'hui)  -> proxy de demande
      (plus c'est loin, plus la salle est bookée).
    - disparition du "prochain créneau" entre 2 relevés -> il a été réservé.
  Chaque run AJOUTE une observation par salle : l'historique se construit et
  ne se perd jamais (max history). Migration Supabase à venir : même schéma.

Usage :
  python3 escape_4escape.py --harvest            # (re)construit le catalogue
  python3 escape_4escape.py --scrape             # 1 relevé upcoming/salle
  python3 escape_4escape.py --harvest --scrape   # les deux
  python3 escape_4escape.py --cities paris,clichy,montreuil
"""
from __future__ import annotations

import argparse
import html
import json
import re
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from safestore import read_json, write_json

DIR_BASE = "https://www.escapegame.fr"
CATALOG_FILE = "escape_4escape_catalog.json"
OBS_DIR = "escape_data/4escape"          # 1 fichier par company (append-only)
UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0")

# Villes IDF connues sur escapegame.fr (extensible). Paris = priorité.
IDF_CITIES_DEFAULT = [
    "paris", "clichy", "montreuil", "nanterre", "courbevoie", "malakoff",
    "meudon", "arcueil", "argenteuil", "bondy", "bry-sur-marne",
    "la-garenne-colombes",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http(url: str, as_json: bool = False, timeout: int = 15, tries: int = 4):
    """GET avec retry/backoff exponentiel (3,6,12,24s)."""
    backoff = 3
    for attempt in range(tries):
        try:
            req = Request(url, headers={"User-Agent": UA,
                          "Accept": "application/json" if as_json else "*/*",
                          "Referer": DIR_BASE + "/paris/"})
            with urlopen(req, timeout=timeout) as r:
                raw = r.read(1_500_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
                return json.loads(raw) if as_json else raw
        except HTTPError as e:
            if e.code in (404, 410):
                return None
            if 400 <= e.code < 500 and e.code != 429:
                return None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass
        if attempt < tries - 1:
            time.sleep(backoff)
            backoff *= 2
    return None


# ---------------------------------------------------------------------------
# HARVEST : catalogue des salles depuis les pages annuaire
# ---------------------------------------------------------------------------

def _attrs(tag: str) -> dict:
    return dict(re.findall(r'(data-[a-z\-]+)="([^"]*)"', tag))


def harvest_city(city: str) -> list[dict]:
    html = http(f"{DIR_BASE}/{city}/")
    if not html:
        return []
    tags = re.findall(r'<[^>]*data-domain="https://availability\.4escape\.io[^>]*>', html)
    rooms = []
    for t in tags:
        a = _attrs(t)
        rid = a.get("data-room-id", "")
        if "/" not in rid:
            continue
        company = rid.split("/", 1)[0]
        rooms.append({
            "room_id": rid,
            "company": company,
            "room_name": html.unescape(a.get("data-room-name", "").strip()),
            "company_name": html.unescape(a.get("data-company-name", "").strip()),
            "theme": a.get("data-theme", "").strip(),
            "levels": a.get("data-levels", "").strip(),
            "players": a.get("data-players", "").strip(),
            "ob_label": a.get("data-ob-label", "").strip(),
            "domain": a.get("data-domain", ""),
            "city": city,
        })
    return rooms


def harvest(cities: list[str]) -> dict:
    catalog = read_json(CATALOG_FILE, {}) or {}
    rooms = catalog.get("rooms", {})
    companies = catalog.get("companies", {})
    seen_now = 0
    for city in cities:
        found = harvest_city(city)
        print(f"[harvest] {city:22} {len(found)} salles")
        for r in found:
            seen_now += 1
            prev = rooms.get(r["room_id"], {})
            r["first_seen"] = prev.get("first_seen", now_iso())
            r["last_seen"] = now_iso()
            rooms[r["room_id"]] = {**prev, **r}
            c = companies.setdefault(r["company"], {
                "company_name": r["company_name"], "cities": [], "rooms": []})
            if r["city"] not in c["cities"]:
                c["cities"].append(r["city"])
            if r["room_id"] not in c["rooms"]:
                c["rooms"].append(r["room_id"])
        time.sleep(1.0)
    out = {
        "_meta": {"updated": now_iso(), "n_rooms": len(rooms),
                  "n_companies": len(companies), "cities": cities,
                  "source": "escapegame.fr / 4escape.io"},
        "companies": companies, "rooms": rooms,
    }
    write_json(CATALOG_FILE, out)
    print(f"[harvest] total catalogue : {len(rooms)} salles, "
          f"{len(companies)} enseignes ({seen_now} vues ce run)")
    return out


# ---------------------------------------------------------------------------
# SCRAPE : 1 observation "prochain créneau libre" par salle (append-only)
# ---------------------------------------------------------------------------

def norm_time(t: str) -> str:
    m = re.match(r"(\d{1,2})h(\d{2})?", t or "")
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m else (t or "")


def scrape_upcoming(catalog: dict, limit: int = 0) -> dict:
    rooms = list(catalog.get("rooms", {}).values())
    if limit:
        rooms = rooms[:limit]
    today = datetime.now().date()
    stats = {"ok": 0, "available": 0, "none": 0, "platforms": {}}
    by_company: dict[str, dict] = {}

    for i, r in enumerate(rooms, 1):
        domain = r["domain"].rstrip("/")
        data = http(f"{domain}/upcoming/{r['room_id']}", as_json=True)
        obs = {"releve": now_iso(), "available": False, "next_date": None,
               "next_time": None, "lead_days": None, "platform": None}
        if isinstance(data, dict) and data.get("date"):
            stats["ok"] += 1
            avail = bool(data.get("available"))
            obs["available"] = avail
            obs["next_date"] = data["date"]
            obs["next_time"] = norm_time(data.get("time", ""))
            try:
                obs["lead_days"] = (datetime.strptime(data["date"], "%Y-%m-%d").date() - today).days
            except ValueError:
                pass
            burl = data.get("book-url", "")
            mm = re.search(r"https?://([a-z0-9\-]+)\.4escape\.io", burl)
            obs["platform"] = "4escape" if mm else (
                re.search(r"https?://(?:www\.)?([a-z0-9\-]+\.[a-z]+)", burl).group(1)
                if burl else None)
            stats["platforms"][obs["platform"]] = stats["platforms"].get(obs["platform"], 0) + 1
            if avail:
                stats["available"] += 1
        else:
            stats["none"] += 1

        # append-only dans le store de la company
        comp = r["company"]
        store = by_company.get(comp)
        if store is None:
            path = f"{OBS_DIR}/{comp}.json"
            store = read_json(path, {}) or {"company": comp,
                "company_name": r["company_name"], "rooms": {}}
            by_company[comp] = store
        rs = store["rooms"].setdefault(r["room_id"], {
            "room_name": r["room_name"], "theme": r["theme"],
            "players": r["players"], "levels": r["levels"],
            "city": r["city"], "observations": []})
        rs["observations"].append(obs)
        # détection "réservé" : le prochain créneau a changé/disparu vs dernier
        _mark_disappearance(rs)
        if i % 20 == 0:
            print(f"[scrape] {i}/{len(rooms)} relevés...")
        time.sleep(0.4)

    for comp, store in by_company.items():
        store["_meta"] = {"last_scrape": now_iso(),
                          "n_rooms": len(store["rooms"]),
                          "n_obs": sum(len(r["observations"]) for r in store["rooms"].values())}
        write_json(f"{OBS_DIR}/{comp}.json", store)

    print(f"[scrape] {stats['ok']} salles OK / {len(rooms)} | "
          f"{stats['available']} dispo | {stats['none']} sans créneau")
    print(f"[scrape] plateformes sous-jacentes : {stats['platforms']}")
    return stats


def _mark_disappearance(room_store: dict) -> None:
    """Marque un événement 'reserve' si le prochain créneau libre observé au
    relevé précédent a disparu (avancé) au relevé courant — sa date était
    future => il a probablement été réservé entre les deux relevés."""
    obs = room_store["observations"]
    if len(obs) < 2:
        return
    prev, cur = obs[-2], obs[-1]
    if not prev.get("next_date") or not prev.get("available"):
        return
    key_prev = (prev["next_date"], prev["next_time"])
    key_cur = (cur.get("next_date"), cur.get("next_time"))
    if key_prev != key_cur:
        # le créneau le plus proche a changé : l'ancien n'est plus le prochain
        try:
            slot_dt = datetime.strptime(f"{prev['next_date']} {prev['next_time']}", "%Y-%m-%d %H:%M")
            if slot_dt > datetime.now():
                cur["event"] = "prev_slot_reserved"
                cur["reserved_slot"] = {"date": prev["next_date"], "time": prev["next_time"]}
        except ValueError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harvest", action="store_true")
    ap.add_argument("--scrape", action="store_true")
    ap.add_argument("--cities", default=",".join(IDF_CITIES_DEFAULT))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not (args.harvest or args.scrape):
        args.harvest = args.scrape = True

    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    catalog = read_json(CATALOG_FILE, {}) or {}
    if args.harvest:
        catalog = harvest(cities)
    if args.scrape:
        if not catalog.get("rooms"):
            print("[scrape] catalogue vide — lance --harvest d'abord.")
            return 1
        scrape_upcoming(catalog, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
