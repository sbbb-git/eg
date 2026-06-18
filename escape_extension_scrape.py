#!/usr/bin/env python3
"""escape_extension_scrape.py — moteur de scrape, dispatch par plateforme.

Modèle d'occupation par "disparition de créneaux" (cf. logique Anybuddy
padel) :
  - on relève à intervalle régulier les créneaux DISPONIBLES ;
  - un créneau vu libre puis qui DISPARAÎT avant son heure  -> "reserve" ;
  - un créneau resté visible jusqu'après son heure          -> "libre_fin".

Chaque marque résolue produit `escape_data/<slug>_data.json` :
  {label, slug, cp, platform, rooms:[...], sessions:[...], _meta:{...}}

Une `session` :
  {date, heure, duree_min, room_name, centre, cp, prix_min, prix_max,
   prix_grille:{"2":..,"3":..}, nb_joueurs_min, nb_joueurs_max,
   dispo, releve, statut}

Modes :
  python3 escape_extension_scrape.py --limit 5      # scrape live (résolus)
  python3 escape_extension_scrape.py --seed         # génère un historique
                                                      # plausible (offline) pour
                                                      # amorcer le dashboard
  python3 escape_extension_scrape.py --demo-one lock-academy-paris-1-chatelet

NOTE: le scrape live réel dépend de l'endpoint JSON de chaque plateforme.
Les handlers `scrape_fullcalendar` / `scrape_timekit` tentent une détection
best-effort ; à défaut ils renvoient [] (et on log) — la vraie résolution
d'endpoint se fait au fil des runs en inspectant le XHR de chaque enseigne.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from safestore import read_json, write_json

RESOLVED_FILE = "escape_extension_resolved.json"
DATA_DIR = "escape_data"
WINDOW_PAST = 3     # J-3 (fenêtre de scrape live)
WINDOW_FUTURE = 30  # J+30
SEED_PAST = 120     # profondeur d'historique simulé en mode --seed (amorçage)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Grilles de prix typiques escape room IDF (prix / pers. dégressif).
DEFAULT_PRICE_GRID = {"2": 39, "3": 33, "4": 29, "5": 27, "6": 25}
WEEKEND_SUPPLEMENT = 4  # € / pers le week-end
LATE_SUPPLEMENT = 3     # € / pers après 21h


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug_path(slug: str) -> str:
    return os.path.join(DATA_DIR, f"{slug}_data.json")


def http_get(url: str, ua: str, timeout: int = 12) -> str | None:
    """GET avec retry/backoff exponentiel (3s, 6s, 12s, 24s)."""
    backoff = 3
    for attempt in range(4):
        try:
            req = Request(url, headers={"User-Agent": ua, "Accept": "*/*",
                                        "Accept-Language": "fr-FR,fr;q=0.9"})
            with urlopen(req, timeout=timeout) as resp:
                cs = resp.headers.get_content_charset() or "utf-8"
                return resp.read(2_000_000).decode(cs, errors="replace")
        except HTTPError as e:
            if e.code != 429 and 400 <= e.code < 500:
                return None
        except (URLError, TimeoutError, OSError):
            pass
        if attempt < 3:
            time.sleep(backoff)
            backoff *= 2
    return None


# ---------------------------------------------------------------------------
# Handlers plateforme. Renvoient une liste de créneaux DISPONIBLES "bruts" :
#   {date, heure, duree_min, room_name, nb_joueurs_min, nb_joueurs_max}
# ---------------------------------------------------------------------------

def scrape_fullcalendar(rec: dict, ua: str) -> list[dict]:
    """FullCalendar expose souvent un endpoint JSON ?start=...&end=... .
    On tente quelques chemins usuels ; à défaut on renvoie []."""
    base = rec["url"].rstrip("/")
    start = (datetime.now() - timedelta(days=WINDOW_PAST)).strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=WINDOW_FUTURE)).strftime("%Y-%m-%d")
    candidates = [
        f"{base}/wp-admin/admin-ajax.php?action=get_events&start={start}&end={end}",
        f"{base}/events.json?start={start}&end={end}",
        f"{base}/api/events?start={start}&end={end}",
        f"{base}/calendar/feed?start={start}&end={end}",
    ]
    for url in candidates:
        body = http_get(url, ua)
        if not body:
            continue
        try:
            events = json.loads(body)
        except json.JSONDecodeError:
            continue
        slots = _parse_fc_events(events)
        if slots:
            return slots
    return []


def _parse_fc_events(events) -> list[dict]:
    out = []
    if isinstance(events, dict):
        events = events.get("events") or events.get("data") or []
    if not isinstance(events, list):
        return out
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start = ev.get("start") or ev.get("date")
        if not start:
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})[T ]?(\d{2}:\d{2})?", str(start))
        if not m:
            continue
        date, heure = m.group(1), m.group(2) or "00:00"
        title = ev.get("title") or ev.get("room") or "Room"
        out.append({"date": date, "heure": heure, "duree_min": 60,
                    "room_name": str(title)[:80],
                    "nb_joueurs_min": 2, "nb_joueurs_max": 6})
    return out


def scrape_timekit(rec: dict, ua: str) -> list[dict]:
    """Timekit : availability via API book.js. Best-effort, renvoie [] si KO."""
    base = rec["url"].rstrip("/")
    for url in [f"{base}/api/availability", f"{base}/availability.json"]:
        body = http_get(url, ua)
        if not body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        return _parse_fc_events(data)
    return []


PLATFORM_HANDLERS = {
    "fullcalendar": scrape_fullcalendar,
    "timekit": scrape_timekit,
    # bsport / mindbody / anybuddy / doinsport : handlers à ajouter au fil des
    # runs (chacun a son endpoint). Pour l'instant -> [] (loggé).
}


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def price_grid(date_str: str, heure: str) -> dict:
    grid = dict(DEFAULT_PRICE_GRID)
    d = datetime.strptime(date_str, "%Y-%m-%d")
    weekend = d.weekday() >= 5
    late = heure >= "21:00"
    sup = (WEEKEND_SUPPLEMENT if weekend else 0) + (LATE_SUPPLEMENT if late else 0)
    if sup:
        grid = {k: v + sup for k, v in grid.items()}
    return grid


def enrich_slot(raw: dict, rec: dict) -> dict:
    grid = price_grid(raw["date"], raw["heure"])
    prices = list(grid.values())
    return {
        "date": raw["date"],
        "heure": raw["heure"],
        "duree_min": raw.get("duree_min", 60),
        "room_name": raw["room_name"],
        "centre": rec["label"],
        "cp": rec["cp"],
        "prix_min": min(prices),
        "prix_max": max(prices),
        "prix_grille": grid,
        "nb_joueurs_min": raw.get("nb_joueurs_min", 2),
        "nb_joueurs_max": raw.get("nb_joueurs_max", 6),
        "dispo": True,
        "releve": now_iso(),
        "statut": "libre",
    }


# ---------------------------------------------------------------------------
# Reconstruction de l'occupation (disparition de créneaux)
# ---------------------------------------------------------------------------

def slot_key(s: dict) -> str:
    return f"{s['date']}|{s['heure']}|{s['room_name']}"


def reconcile(existing: list[dict], current_keys: set[str], now: datetime) -> list[dict]:
    """Met à jour le statut des sessions déjà connues en fonction des créneaux
    encore visibles (current_keys)."""
    out = []
    for s in existing:
        try:
            slot_dt = datetime.strptime(f"{s['date']} {s['heure']}", "%Y-%m-%d %H:%M")
        except ValueError:
            out.append(s)
            continue
        k = slot_key(s)
        still_visible = k in current_keys
        if still_visible:
            s["dispo"] = True
            if slot_dt < now:
                s["statut"] = "libre_fin"   # resté libre jusqu'après l'heure
            else:
                s["statut"] = "libre"
        else:
            # plus visible
            if s.get("statut") in ("reserve", "libre_fin"):
                pass  # figé
            elif slot_dt > now:
                s["dispo"] = False
                s["statut"] = "reserve"     # disparu avant l'heure -> réservé
            else:
                s["statut"] = s.get("statut", "libre_fin")
        out.append(s)
    return out


def prune_old(sessions: list[dict], days: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [s for s in sessions if s["date"] >= cutoff]


def merge_run(rec: dict, current: list[dict]) -> dict:
    """Fusionne un relevé courant dans le data.json de la marque."""
    path = slug_path(rec["slug"])
    store = read_json(path, {}) or {}
    sessions = store.get("sessions", [])
    now = datetime.now()

    by_key = {slot_key(s): s for s in sessions}
    current_keys = set()
    for raw in current:
        enriched = enrich_slot(raw, rec)
        k = slot_key(enriched)
        current_keys.add(k)
        if k not in by_key:
            by_key[k] = enriched   # nouveau créneau découvert

    merged = reconcile(list(by_key.values()), current_keys, now)
    merged = prune_old(merged)
    rooms = sorted({s["room_name"] for s in merged})

    store.update({
        "label": rec["label"], "slug": rec["slug"], "cp": rec["cp"],
        "platform": rec["platform"], "url": rec["url"],
        "rooms": rooms, "sessions": merged,
        "_meta": {"last_scrape": now_iso(), "n_sessions": len(merged),
                  "n_slots_seen": len(current_keys)},
    })
    write_json(path, store)
    return store


# ---------------------------------------------------------------------------
# Mode SEED : génère un historique plausible offline pour amorcer le dashboard
# ---------------------------------------------------------------------------

DEMO_ROOMS = {
    "default": ["Le Braquage", "Prison Break", "Le Manoir Hanté", "Station Spatiale"],
}
THEMES = ["aventure", "horreur", "enquete", "sci-fi", "aventure", "horreur"]


def seed_brand(rec: dict) -> dict:
    rng = random.Random(int(hashlib.md5(rec["slug"].encode()).hexdigest(), 16) & 0xFFFFFFFF)
    rooms = [f"{rec['label'].split(' - ')[0]} · {r}" for r in
             rng.sample(DEMO_ROOMS["default"], k=rng.randint(2, 4))]
    hours = ["11:00", "13:00", "15:00", "17:00", "19:00", "21:00", "22:30"]
    sessions = []
    today = datetime.now().date()
    # popularité intrinsèque du centre (selon priorité) — calibrée pour un
    # taux d'occupation réaliste marché escape room (~45-65%).
    base_pop = {1: 0.52, 2: 0.40, 3: 0.30}.get(rec.get("priorite", 3), 0.35)
    for d in range(-SEED_PAST, WINDOW_FUTURE + 1):
        day = today + timedelta(days=d)
        weekend = day.weekday() >= 5
        for room in rooms:
            for h in hours:
                if rng.random() > 0.82:   # tous les créneaux n'existent pas
                    continue
                hour = int(h[:2])
                # proba de réservation pondérée jour/heure
                p = base_pop
                p += 0.14 if weekend else 0
                p += 0.10 if 19 <= hour <= 22 else (-0.12 if hour < 13 else 0)
                p = max(0.04, min(0.92, p))
                date_str = day.strftime("%Y-%m-%d")
                grid = price_grid(date_str, h)
                prices = list(grid.values())
                past = day < today or (day == today and hour < datetime.now().hour)
                if past:
                    statut = "reserve" if rng.random() < p else "libre_fin"
                    dispo = statut == "libre_fin"
                else:
                    # futur : créneau visible ; on simule déjà des réservations
                    reserved = rng.random() < p * 0.6
                    statut = "reserve" if reserved else "libre"
                    dispo = not reserved
                sessions.append({
                    "date": date_str, "heure": h, "duree_min": rng.choice([60, 75, 90]),
                    "room_name": room, "centre": rec["label"], "cp": rec["cp"],
                    "prix_min": min(prices), "prix_max": max(prices), "prix_grille": grid,
                    "nb_joueurs_min": 2, "nb_joueurs_max": rng.choice([5, 6, 6, 8]),
                    "dispo": dispo, "releve": now_iso(), "statut": statut,
                    "theme": THEMES[hash(room) % len(THEMES)],
                })
    store = {
        "label": rec["label"], "slug": rec["slug"], "cp": rec["cp"],
        "platform": rec.get("platform", "seed"), "url": rec["url"],
        "rooms": sorted(set(rooms)), "sessions": sessions,
        "_meta": {"last_scrape": now_iso(), "seeded": True, "n_sessions": len(sessions)},
    }
    write_json(slug_path(rec["slug"]), store)
    return store


# ---------------------------------------------------------------------------

def load_resolved() -> list[dict]:
    resolved = read_json(RESOLVED_FILE, {}) or {}
    return list(resolved.get("resolved", {}).values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", action="store_true", help="génère un historique offline")
    ap.add_argument("--demo-one", help="seed une seule marque (slug)")
    args = ap.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)

    recs = load_resolved()
    if not recs:
        print(f"[scrape] {RESOLVED_FILE} vide — lance d'abord la discovery, "
              f"ou utilise --seed avec un resolved minimal.", file=sys.stderr)

    if args.seed or args.demo_one:
        targets = recs
        if args.demo_one:
            targets = [r for r in recs if r["slug"] == args.demo_one]
        if args.limit:
            targets = targets[: args.limit]
        for r in targets:
            store = seed_brand(r)
            print(f"[seed] {r['label']}: {store['_meta']['n_sessions']} sessions")
        print(f"[seed] terminé ({len(targets)} marques).")
        return 0

    # scrape live
    scrapable = [r for r in recs if r["platform"] in PLATFORM_HANDLERS]
    if args.limit:
        scrapable = scrapable[: args.limit]
    if not scrapable:
        print("[scrape] aucune marque sur une plateforme gérée "
              f"({sorted(PLATFORM_HANDLERS)}). Rien à scraper.")
        return 0
    for i, rec in enumerate(scrapable, 1):
        ua = USER_AGENTS[i % len(USER_AGENTS)]
        handler = PLATFORM_HANDLERS[rec["platform"]]
        print(f"[scrape] ({i}/{len(scrapable)}) {rec['label']} [{rec['platform']}]")
        try:
            current = handler(rec, ua)
        except Exception as e:  # noqa: BLE001 — un échec ne doit pas tuer le run
            print(f"         ! erreur: {e}", file=sys.stderr)
            current = []
        store = merge_run(rec, current)
        print(f"         {len(current)} créneaux relevés, "
              f"{store['_meta']['n_sessions']} sessions au total")
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
