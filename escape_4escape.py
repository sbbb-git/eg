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
import urllib.parse as up
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from safestore import read_json, write_json

DIR_BASE = "https://www.escapegame.fr"
CATALOG_FILE = "escape_4escape_catalog.json"
OBS_DIR = "escape_data/4escape"          # 1 fichier par company (append-only)
UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0")

# Villes IDF présentes sur escapegame.fr (extensible). Paris = gros du volume
# 4escape ; les communes de couronne ont surtout des venues non-4escape (track
# par-site), mais on les harvest quand même pour capter les rares cards 4escape.
IDF_CITIES_DEFAULT = [
    "paris", "clichy", "montreuil", "nanterre", "courbevoie", "malakoff",
    "meudon", "arcueil", "argenteuil", "bondy", "bry-sur-marne",
    "la-garenne-colombes", "boulogne-billancourt", "issy-les-moulineaux",
    "saint-maur-des-fosses", "vincennes", "creteil", "versailles",
    "saint-denis", "asnieres-sur-seine", "levallois-perret", "neuilly-sur-seine",
    "ivry-sur-seine", "puteaux", "rueil-malmaison", "cergy", "massy",
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
    page = http(f"{DIR_BASE}/{city}/")
    if not page:
        return []
    tags = re.findall(r'<[^>]*data-domain="https://availability\.4escape\.io[^>]*>', page)
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
# ENRICHISSEMENT autonome : <company>.4escape.io/api/public/settings
#   Endpoint UNIVERSEL (pas de 401) qui renvoie le catalogue complet :
#   organization (nom, adresse->CP, tel, site, URL de résa) + rooms (nom,
#   durée, joueurs min/max, difficulté, description, website_url). Permet de
#   géolocaliser (CP) et de DÉCOUVRIR seul les sites/URL de réservation.
# ---------------------------------------------------------------------------

def _items(v):
    return list(v.values()) if isinstance(v, dict) else (v or [])


def fetch_settings(company: str):
    base = f"https://{company}.4escape.io"
    backoff = 3
    for attempt in range(3):
        data = http(base + "/api/public/settings", as_json=True)
        if isinstance(data, dict) and data.get("success"):
            return data
        if attempt < 2:
            time.sleep(backoff); backoff *= 2
    return None


def enrich_from_settings(catalog: dict) -> dict:
    rooms = catalog.get("rooms", {})
    companies = catalog.get("companies", {})
    stats = {"ok": 0, "ko": 0, "rooms_enriched": 0}
    for comp in sorted(companies):
        s = fetch_settings(comp)
        if not s:
            stats["ko"] += 1
            time.sleep(0.5)
            continue
        stats["ok"] += 1
        org = s.get("organization", {}) or {}
        addr = org.get("address", {}) or {}
        cp = (addr.get("zipcode") or "").strip()
        companies[comp].update({
            "org_name": (org.get("display_name") or org.get("name") or "").strip(),
            "website": (org.get("website") or "").strip(),
            "phone": re.sub(r"\s+", " ", (org.get("phone") or "")).strip(),
            "cp": cp, "city": (addr.get("city") or "").strip(),
            "street": (addr.get("street") or "").strip(),
        })
        # index des rooms settings par _id et par nom normalisé
        by_id, by_name = {}, {}
        for r in _items(s.get("rooms")):
            meta = {
                "duration": r.get("duration"),
                "min_players": r.get("minimum_players"),
                "max_players": r.get("maximum_players"),
                "default_players": r.get("default_players"),
                "difficulty": r.get("difficulty"),
                "description": (r.get("short_description") or r.get("description") or "")[:240],
                "reservation_url": r.get("widget_public_url") or r.get("website_url") or org.get("website"),
            }
            if r.get("_id"):
                by_id[r["_id"]] = meta
            if r.get("name"):
                by_name[re.sub(r"\W+", "", r["name"].lower())] = meta
        for room_id, rec in rooms.items():
            if rec.get("company") != comp:
                continue
            mongoid = room_id.split("/", 1)[1]
            meta = by_id.get(mongoid) or by_name.get(re.sub(r"\W+", "", (rec.get("room_name") or "").lower()))
            if meta:
                rec.update({k: v for k, v in meta.items() if v is not None})
                stats["rooms_enriched"] += 1
            if cp and not rec.get("cp"):
                rec["cp"] = cp
        print(f"[enrich] {companies[comp].get('org_name', comp)[:28]:28} "
              f"CP {cp or '?'} · {org.get('website','')[:34]}")
        time.sleep(0.5)
    catalog["_meta"]["enriched"] = now_iso()
    write_json(CATALOG_FILE, catalog)
    print(f"[enrich] enseignes OK={stats['ok']} KO={stats['ko']} | "
          f"salles enrichies={stats['rooms_enriched']}")
    return catalog


# ---------------------------------------------------------------------------
# PRIX + planning : <company>.4escape.io/booking-data-json (POST)
#   -> renvoie le PLANNING THÉORIQUE complet (tous créneaux) + GRILLE DE PRIX
#      par nombre de joueurs. PAS l'occupation (créneaux réservés non retirés).
#   Certaines enseignes renvoient 401 (API verrouillée) -> on skippe proprement.
# ---------------------------------------------------------------------------

def post_booking_data(company: str, date_str: str, view: int = 1):
    base = f"https://{company}.4escape.io"
    body = up.urlencode({"UID": uuid.uuid4().hex, "date": date_str,
                         "viewDuration": view}).encode()
    backoff = 3
    for attempt in range(3):
        try:
            req = Request(base + "/booking-data-json", data=body, headers={
                "User-Agent": UA, "Accept": "application/json, */*",
                "X-Requested-With": "XMLHttpRequest", "Origin": base,
                "Referer": base + "/", "Content-Type": "application/x-www-form-urlencoded"})
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read(4_000_000).decode("utf-8", "replace"))
        except HTTPError as e:
            return {"_error": e.code}        # 401/403/404 : inutile de réessayer
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            if attempt < 2:
                time.sleep(backoff); backoff *= 2
    return {"_error": "fail"}


def harvest_prices(catalog: dict) -> dict:
    """Pour chaque enseigne, récupère la grille de prix + le nb de créneaux
    hebdo par salle, et les attache au catalogue (idempotent)."""
    rooms = catalog.get("rooms", {})
    companies = catalog.get("companies", {})
    today = datetime.now().strftime("%Y-%m-%d")
    stats = {"ok": 0, "locked": 0, "rooms_priced": 0}
    for comp in sorted(companies):
        data = post_booking_data(comp, today, view=7)
        if not isinstance(data, dict) or "_error" in data or not data.get("results"):
            stats["locked"] += 1
            companies[comp]["prices_status"] = (
                f"locked:{data.get('_error')}" if isinstance(data, dict) else "locked")
            time.sleep(0.6)
            continue
        stats["ok"] += 1
        # grilles par roomId 4escape + planning (créneaux/semaine)
        grids: dict[str, dict] = {}
        slot_count: dict[str, int] = {}
        for s in data["results"]:
            rid = s.get("roomId")
            slot_count[rid] = slot_count.get(rid, 0) + 1
            if rid not in grids and s.get("prices"):
                grids[rid] = {pc: round(p["amount_charged"] / 100, 2)
                              for pc, p in s["prices"].items()}
        # Le mongoid escapegame.fr ne matche pas toujours le roomId 4escape, mais
        # toutes les salles d'une enseigne partagent (quasi) la même grille :
        # on attache une grille REPRÉSENTATIVE au niveau enseigne + par salle.
        # match exact prioritaire, sinon grille modale de l'enseigne.
        modal = max(grids.values(), key=lambda g: sum(g.values())) if grids else None
        companies[comp]["prix_grille"] = modal
        if modal:
            vals = [v for v in modal.values() if v]
            companies[comp]["prix_min"] = min(vals) if vals else None
            companies[comp]["prix_max"] = max(vals) if vals else None
        for room_id, r in rooms.items():
            if r.get("company") != comp:
                continue
            mongoid = room_id.split("/", 1)[1]
            grid = grids.get(mongoid, modal)
            if grid:
                vals = [v for v in grid.values() if v]
                r["prix_grille"] = grid
                r["prix_min"] = min(vals) if vals else None
                r["prix_max"] = max(vals) if vals else None
                r["prix_source"] = "exact" if mongoid in grids else "enseigne"
                r["slots_per_week"] = slot_count.get(mongoid)
                stats["rooms_priced"] += 1
        companies[comp]["prices_status"] = "ok"
        print(f"[prices] {companies[comp]['company_name'][:30]:30} "
              f"grille {modal}")
        time.sleep(0.6)
    catalog["_meta"]["prices_updated"] = now_iso()
    write_json(CATALOG_FILE, catalog)
    print(f"[prices] enseignes OK={stats['ok']} verrouillées={stats['locked']} "
          f"| salles tarifées={stats['rooms_priced']}")
    return catalog


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
    ap.add_argument("--enrich", action="store_true", help="catalogue+CP+URL résa via /api/public/settings")
    ap.add_argument("--prices", action="store_true", help="grilles de prix + planning")
    ap.add_argument("--scrape", action="store_true")
    ap.add_argument("--cities", default=",".join(IDF_CITIES_DEFAULT))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not (args.harvest or args.scrape or args.prices or args.enrich):
        args.harvest = args.enrich = args.prices = args.scrape = True

    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    catalog = read_json(CATALOG_FILE, {}) or {}
    if args.harvest:
        catalog = harvest(cities)
    if args.enrich:
        if catalog.get("rooms"):
            catalog = enrich_from_settings(catalog)
        else:
            print("[enrich] catalogue vide — lance --harvest d'abord.")
    if args.prices:
        if catalog.get("rooms"):
            catalog = harvest_prices(catalog)
        else:
            print("[prices] catalogue vide — lance --harvest d'abord.")
    if args.scrape:
        if not catalog.get("rooms"):
            print("[scrape] catalogue vide — lance --harvest d'abord.")
            return 1
        scrape_upcoming(catalog, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
