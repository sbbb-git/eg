#!/usr/bin/env python3
"""escape_4escape_all.py — scrape 4escape À GRANDE ÉCHELLE (tout le réseau IDF).

Découverte (juin 2026) : le widget moderne widgets.4escape.app expose, sur le
SOUS-DOMAINE de chaque enseigne <company>.4escape.io, des endpoints publics :
  - GET  /api/public/boot        -> valide que la company existe (success+baseURL)
  - GET  /api/public/settings    -> catalogue (rooms : durée, joueurs, difficulté)
  - POST /api/public/availability/upcoming {date}   -> TOUS les créneaux LIBRES
        de la semaine par room  (marche même si booking-data-json est 401)
  - POST /booking-data-json {date,viewDuration}      -> planning THÉORIQUE complet
        + grille de PRIX (souvent verrouillé en 401 -> on dégrade proprement)

=> remplissage = 1 - (créneaux libres / créneaux théoriques).
   Si booking verrouillé : on garde au moins #libres + lead-time (proxy demande).

La company se DEVINE depuis le site officiel (SLD) puis se VALIDE via /boot.
Sites candidats : resolver (escape_sites_cache.json) + annuaire + catalogue
existant. Observations APPEND-ONLY par enseigne (escape_data/4escape_all/).
Resumable + idempotent.

  python3 escape_4escape_all.py --discover   # (re)trouve les companies 4escape
  python3 escape_4escape_all.py --scrape     # 1 relevé remplissage/prix
  python3 escape_4escape_all.py              # discover + scrape
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse as up
import uuid
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from safestore import read_json, write_json

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
COMPANIES_FILE = "escape_4escape_companies.json"
OBS_DIR = "escape_data/4escape_all"
CACHE = "escape_sites_cache.json"
DIRECTORY = "escape_idf_directory.json"
LEGACY_CATALOG = "escape_4escape_catalog.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def api(comp: str, path: str, post: dict | None = None, tmo: int = 15):
    url = f"https://{comp}.4escape.io{path}"
    h = {"User-Agent": UA, "Accept": "application/json",
         "Origin": f"https://{comp}.4escape.io", "Referer": f"https://{comp}.4escape.io/"}
    try:
        if post is not None:
            req = Request(url, data=json.dumps(post).encode(),
                          headers={**h, "Content-Type": "application/json"})
        else:
            req = Request(url, headers=h)
        with urlopen(req, timeout=tmo) as r:
            return json.loads(r.read(4_000_000).decode("utf-8", "replace"))
    except HTTPError as e:
        return {"_err": e.code}
    except Exception as e:
        return {"_err": str(e)[:40]}


def sld(website: str) -> str | None:
    m = re.search(r"https?://(?:www\.)?([a-z0-9\-]+)\.[a-z.]{2,}", website or "", re.I)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------------------
# DISCOVERY : SLD candidats -> /boot -> companies 4escape validées
# ---------------------------------------------------------------------------

def candidate_companies() -> dict[str, str]:
    """slug-or-name -> website, depuis toutes les sources disponibles."""
    cands: dict[str, str] = {}
    cache = read_json(CACHE, {}) or {}
    for slug, e in (cache.get("venues") or {}).items():
        if e.get("website"):
            cands[slug] = e["website"]
    directory = read_json(DIRECTORY, {}) or {}
    for v in directory.get("venues", []):
        if v.get("website"):
            cands.setdefault(v["slug"], v["website"])
    return cands


def discover() -> dict:
    store = read_json(COMPANIES_FILE, {}) or {}
    store.setdefault("companies", {})   # comp -> {website, org_name, validated, ...}
    store.setdefault("tried", {})       # sld -> bool (évite re-test)
    # seed : companies historiques (les 16) déjà connues 4escape
    legacy = read_json(LEGACY_CATALOG, {}) or {}
    for comp in (legacy.get("companies") or {}):
        store["companies"].setdefault(comp, {"source": "escapegame.fr"})
        store["tried"][comp] = True

    cands = candidate_companies()
    new = 0
    for slug, website in cands.items():
        cand = sld(website)
        if not cand or store["tried"].get(cand) or cand in store["companies"]:
            continue
        store["tried"][cand] = True
        d = api(cand, "/api/public/boot", tmo=10)
        if isinstance(d, dict) and d.get("success"):
            store["companies"][cand] = {"website": website, "slug": slug,
                                        "source": "resolver", "found": now_iso()}
            new += 1
            print(f"[discover] ✓ {cand:24} <- {website[:40]}")
        time.sleep(0.15)
    store["_meta"] = {"updated": now_iso(), "n_companies": len(store["companies"]),
                      "n_tried": len(store["tried"])}
    write_json(COMPANIES_FILE, store)
    print(f"[discover] +{new} -> {len(store['companies'])} companies 4escape "
          f"({len(store['tried'])} SLD testés)")
    return store


# ---------------------------------------------------------------------------
# SCRAPE : settings + availability/upcoming + booking-data-json
# ---------------------------------------------------------------------------

def _items(v):
    return list(v.values()) if isinstance(v, dict) else (v or [])


THEME_KW = [
    ("horreur", r"horreur|horror|peur|terreur|zombie|asile|psychiatr|exorc|hant[ée]|gore|épouvante|epouvante|cauchemar|saw|insomni|paranormal|spirit|d[ée]mon"),
    ("enquête", r"enqu[êe]te|d[ée]tective|crime|meurtre|police|disparition|braquage|casse|prison|[ée]vasion|alcatraz|mafia|cluedo|sherlock|tueur|s[ée]questr|interpol|fbi"),
    ("fantastique", r"fantastique|magie|magique|sorcier|wizard|potion|dragon|conte|l[ée]gende|alice|narnia|m[ée]di[ée]val|elfe|f[ée]e|mythe|olympe|excalibur"),
    ("science-fiction", r"espace|spatial|science|labo|laborat|nucl[ée]aire|virus|futur|robot|alien|mars|station|cyber|matrix|temps|time|apocalyp|quantique|exp[ée]rience"),
    ("aventure", r"aventure|tr[ée]sor|pirate|jungle|temple|[ée]gypte|tombe|momie|indiana|far.?west|western|safari|braquage|or|gold|qu[êe]te"),
]


def classify_theme(name: str, desc: str) -> str:
    t = f"{name} {desc}".lower()
    for theme, rx in THEME_KW:
        if re.search(rx, t):
            return theme
    return "aventure"


def _grid(prices: dict) -> dict:
    """prices 4escape : clé = nb de joueurs, amount_charged = prix/joueur (cents)."""
    g = {}
    for k, p in (prices or {}).items():
        if str(k).isdigit() and isinstance(p, dict) and p.get("amount_charged"):
            g[int(k)] = round(p["amount_charged"] / 100, 2)
    return g


def scrape_company(comp: str, date_str: str) -> dict:
    s = api(comp, "/api/public/settings")
    if not (isinstance(s, dict) and s.get("success")):
        return {"_err": s.get("_err") if isinstance(s, dict) else "no-settings"}
    org = s.get("organization", {}) or {}
    addr = org.get("address", {}) or {}
    rooms_meta = {}
    for r in _items(s.get("rooms")):
        if r.get("_id"):
            name = (r.get("display_name") or r.get("name") or "").strip()
            desc = (r.get("short_description") or r.get("description") or "")
            rooms_meta[r["_id"]] = {
                "name": name, "duration": r.get("duration"),
                "min_players": r.get("minimum_players"), "max_players": r.get("maximum_players"),
                "difficulty": r.get("difficulty"), "theme": classify_theme(name, desc),
            }
    sessions: dict[str, dict] = {}
    # 1) source AUTORITAIRE : booking-data-json (tous les créneaux + booked + prix)
    bd = _booking(comp, date_str, view=7)
    locked = not (isinstance(bd, dict) and bd.get("results"))
    if not locked:
        for slot in bd["results"]:
            rid, start, end = slot.get("roomId"), slot.get("start"), slot.get("end")
            if not (rid and start):
                continue
            duree = None
            if end:
                try:
                    duree = int((datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
                                 - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
                except ValueError:
                    pass
            booked = bool(slot.get("booked"))
            disabled = bool(slot.get("disabled"))
            sessions[f"{start[:10]}T{start[11:16]}|{rid}"] = {
                "date": start[:10], "heure": start[11:16], "duree_minutes": duree,
                "room_id": rid, "prix": _grid(slot.get("prices")),
                "remaining_players": slot.get("remainingPlayers"),
                "booked": booked, "dispo": (not booked and not disabled)}
    else:
        # 2) fallback verrouillé : seulement les créneaux LIBRES via upcoming
        up_data = api(comp, "/api/public/availability/upcoming",
                      post={"date": date_str, "period": "week"})
        res = up_data.get("results") if isinstance(up_data, dict) else None
        for rid, slots in (res.items() if isinstance(res, dict) else []):
            for sl in (slots if isinstance(slots, list) else []):
                start = sl.get("start")
                if not start:
                    continue
                sessions[f"{start[:10]}T{start[11:16]}|{rid}"] = {
                    "date": start[:10], "heure": start[11:16], "duree_minutes": None,
                    "room_id": rid, "prix": {}, "remaining_players": None,
                    "booked": False, "dispo": True}
    return {
        "org_name": (org.get("display_name") or org.get("name") or comp).strip(),
        "cp": (addr.get("zipcode") or "").strip(), "city": (addr.get("city") or "").strip(),
        "website": (org.get("website") or "").strip(),
        "prices_locked": locked, "rooms": rooms_meta, "sessions": sessions,
    }


def _booking(comp: str, date_str: str, view: int = 7):
    body = up.urlencode({"UID": uuid.uuid4().hex, "date": date_str, "viewDuration": view}).encode()
    url = f"https://{comp}.4escape.io/booking-data-json"
    try:
        req = Request(url, data=body, headers={
            "User-Agent": UA, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
            "Origin": f"https://{comp}.4escape.io", "Referer": f"https://{comp}.4escape.io/",
            "Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(req, timeout=30) as r:
            return json.loads(r.read(4_000_000).decode("utf-8", "replace"))
    except HTTPError as e:
        return {"_err": e.code}
    except Exception as e:
        return {"_err": str(e)[:40]}


def paris_now() -> datetime:
    """Heure de Paris (CEST en juin = UTC+2). Suffisant pour trancher les slots."""
    return datetime.utcnow() + timedelta(hours=2)


def _slot_dt(sess: dict):
    try:
        return datetime.strptime(f"{sess['date']} {sess['heure']}", "%Y-%m-%d %H:%M")
    except (ValueError, KeyError):
        return None


def reconcile(st: dict, current: dict, locked: bool) -> None:
    """Fusion idempotente + reconstitution du statut (héritage logique padel) :
       - slot 'booked' (donnée ouverte)         -> reserve
       - slot futur DISPARU de la liste libre    -> reserve (verrouillé)
       - slot passé resté dispo, jamais booké     -> libre_fin
    """
    now = now_iso()
    pnow = paris_now()
    horizon = pnow + timedelta(days=7)
    for key, sess in current.items():
        ex = st["sessions"].get(key)
        if ex is None:
            ex = {**sess, "premier_vu": now, "statut": None}
        else:
            ex.update({k: sess[k] for k in ("prix", "duree_minutes", "remaining_players",
                                            "booked", "dispo")})
        ex["dernier_vu"] = now
        ex["releve"] = now
        if sess.get("booked"):
            ex["statut"] = "reserve"
        st["sessions"][key] = ex
    # passes sur l'historique : disparition (verrouillé) + fin de vie
    for key, ex in st["sessions"].items():
        if ex.get("statut") in ("reserve", "libre_fin"):
            continue
        dt = _slot_dt(ex)
        if dt is None:
            continue
        if dt < pnow:                                   # créneau passé -> statut final
            ex["statut"] = "reserve" if ex.get("booked") else ("libre_fin" if ex.get("dispo") else None)
        elif locked and dt <= horizon and key not in current and ex.get("dispo"):
            ex["statut"] = "reserve"                    # libre puis disparu = réservé
            ex["dispo"] = False


def scrape(limit: int = 0) -> None:
    store = read_json(COMPANIES_FILE, {}) or {}
    comps = sorted(store.get("companies", {}))
    if limit:
        comps = comps[:limit]
    date_str = paris_now().strftime("%Y-%m-%d")
    ok = fail = n_sess = 0
    for i, comp in enumerate(comps, 1):
        obs = scrape_company(comp, date_str)
        if "_err" in obs:
            fail += 1
            time.sleep(0.3)
            continue
        ok += 1
        path = f"{OBS_DIR}/{comp}.json"
        st = read_json(path, {}) or {"company": comp, "rooms": {}, "sessions": {}}
        st.setdefault("sessions", {})
        st.update({k: obs[k] for k in ("org_name", "cp", "city", "website")})
        for rid, meta in obs["rooms"].items():
            st["rooms"][rid] = meta
        reconcile(st, obs["sessions"], obs["prices_locked"])
        st["_meta"] = {"last_scrape": now_iso(), "n_rooms": len(st["rooms"]),
                       "n_sessions": len(st["sessions"]), "prices_locked": obs["prices_locked"]}
        n_sess += len(obs["sessions"])
        write_json(path, st)
        if i % 10 == 0:
            print(f"[scrape] {i}/{len(comps)} … {comp} "
                  f"({len(obs['rooms'])}r/{len(obs['sessions'])}s, "
                  f"{'locked' if obs['prices_locked'] else 'open'})")
        time.sleep(0.4)
    print(f"[scrape] OK={ok} KO={fail} / {len(comps)} companies | {n_sess} sessions ce run")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--scrape", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not (args.discover or args.scrape):
        args.discover = args.scrape = True
    if args.discover:
        discover()
    if args.scrape:
        scrape(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
