#!/usr/bin/env python3
"""escape_engine_bookeo.py — engine de scraping pour les venues sur Bookeo.

Bookeo bloque les IP de datacenter ("Access from unauthorized IP address
detected"). Cet engine sort donc OBLIGATOIREMENT par un proxy résidentiel
(voir escape_proxy.py : PROXY_SERVER/PROXY_USERNAME/PROXY_PASSWORD). Sans
proxy il détecte le blocage, le signale, et n'écrit rien (no-op propre).

Flux Bookeo (widget bouton flottant) :
  page de résa  ->  <script src="bookeo.com/widget.js?a=APIKEY">
                ->  bouton injecté dans div#bookeo_position
                ->  clic -> ouverture du flux (produits -> calendrier -> créneaux)

On pilote le flux et on capture les créneaux de DEUX façons (robustesse) :
  1. interception des réponses XHR bookeo.com (JSON dispo)  ← format-agnostique
  2. lecture des boutons d'horaires rendus dans le widget   ← fallback DOM

Sortie : un fichier par venue dans escape_data/4escape_all/bookeo__<slug>.json,
au MÊME format que escape_4escape_all -> agrégé + synchronisé Supabase sans
modifier les autres scripts.

  python3 escape_engine_bookeo.py            # toutes les venues Bookeo connues
  python3 escape_engine_bookeo.py <slug>...  # venues ciblées
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

import escape_proxy
from safestore import read_json, write_json
from escape_4escape_all import classify_theme, _norm_id  # type: ignore

CACHE = "escape_sites_cache.json"
OUT_DIR = "escape_data/4escape_all"          # même dossier => agrégé/synchro auto
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
RESA_PATHS = ["/reservation/", "/reserver/", "/booking/", "/reservations/", "/"]
TIME_RX = re.compile(r"\b([01]?\d|2[0-3])[:hH]([0-5]\d)\b")
IPBLOCK_RX = re.compile(r"unauthorized IP|fraud|VPN|masquerad", re.I)


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def bookeo_venues() -> dict[str, dict]:
    """slug -> {website} pour les venues détectées Bookeo."""
    v = (read_json(CACHE, {}) or {}).get("venues", {})
    return {k: e for k, e in v.items() if e.get("platform") == "bookeo"}


def find_resa(website: str) -> tuple[str, str | None]:
    """Renvoie (url_resa, api_key). Cherche le widget.js?a=KEY sur la page."""
    base = website.rstrip("/")
    for path in RESA_PATHS:
        url = base + path
        try:
            html = urlopen(Request(url, headers={"User-Agent": UA}), timeout=15).read(800_000).decode("utf-8", "replace")
        except Exception:
            continue
        m = re.search(r"bookeo\.com/widget\.js\?a=([A-Za-z0-9]+)", html)
        if m:
            return url, m.group(1)
    return base + "/reservation/", None


# ───────────────────────── extraction des créneaux ─────────────────────────
def harvest_times_from_json(obj, out: set[tuple[str, str]], cur_date: list[str]) -> None:
    """Parcourt récursivement un JSON Bookeo et collecte (date, HH:MM) plausibles."""
    if isinstance(obj, dict):
        # une date de contexte ?
        for dk in ("date", "day", "startDate"):
            dv = obj.get(dk)
            if isinstance(dv, str) and re.match(r"\d{4}-\d{2}-\d{2}", dv):
                cur_date[0] = dv[:10]
        # un horaire de début ?
        start = obj.get("startTime") or obj.get("start") or obj.get("time")
        if isinstance(start, str):
            m = TIME_RX.search(start)
            d = start[:10] if re.match(r"\d{4}-\d{2}-\d{2}", start) else cur_date[0]
            if m and d:
                out.add((d, f"{int(m.group(1)):02d}:{m.group(2)}"))
        for v in obj.values():
            harvest_times_from_json(v, out, cur_date)
    elif isinstance(obj, list):
        for it in obj:
            harvest_times_from_json(it, out, cur_date)


def scrape_venue(pg, slug: str, website: str) -> dict | None:
    url, api_key = find_resa(website)
    captured: list = []
    blocked = {"v": False}

    def on_resp(r):
        if "bookeo.com" not in r.url or "widget.js" in r.url:
            return
        try:
            body = r.text()
        except Exception:
            return
        if IPBLOCK_RX.search(body):
            blocked["v"] = True
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            try:
                captured.append(json.loads(body))
            except Exception:
                pass

    pg.on("response", on_resp)
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=50000)
        for sel in ("text=Accepter tout", "button:has-text('Accepter')", "[id*=accept]"):
            try:
                pg.click(sel, timeout=2000)
                break
            except Exception:
                pass
        pg.wait_for_timeout(6000)
        # ouvrir le flux (bouton flottant injecté)
        for sel in ("#bookeo_position button", "#bookeo_position a", "#bookeo_position",
                    "[class*=bookeo] button", "iframe[src*=bookeo]"):
            try:
                el = pg.query_selector(sel)
                if el:
                    el.click(timeout=3000)
                    pg.wait_for_timeout(4000)
                    break
            except Exception:
                pass
        # détection blocage IP dans le DOM injecté
        pos = pg.query_selector("#bookeo_position")
        if pos and IPBLOCK_RX.search((pos.inner_text() or "")):
            blocked["v"] = True
    except Exception as e:
        print(f"  [bookeo] {slug}: nav KO {repr(e)[:60]}")

    if blocked["v"]:
        print(f"  [bookeo] {slug}: ⛔ IP bloquée par Bookeo — proxy résidentiel requis "
              f"({escape_proxy.describe()})")
        return None

    # 1) créneaux via JSON intercepté
    slots: set[tuple[str, str]] = set()
    for obj in captured:
        harvest_times_from_json(obj, slots, [None])
    # 2) fallback DOM : on balaie le calendrier sur ~7 jours
    if not slots:
        for _ in range(7):
            for fr in pg.frames:
                try:
                    txts = fr.eval_on_selector_all(
                        "button,td,a,[class*=time],[class*=slot],[class*=hour]",
                        "els=>els.map(e=>(e.textContent||'').trim()).filter(t=>t)")
                except Exception:
                    continue
                for t in txts:
                    m = TIME_RX.search(t)
                    if m and len(t) <= 12:
                        slots.add((None, f"{int(m.group(1)):02d}:{m.group(2)}"))
            # avancer d'un jour/semaine
            advanced = False
            for sel in ("[class*=next]", "button[aria-label*=uivant]", "text=>", "text=›"):
                try:
                    pg.click(sel, timeout=1500)
                    pg.wait_for_timeout(1500)
                    advanced = True
                    break
                except Exception:
                    pass
            if not advanced:
                break

    if not slots:
        print(f"  [bookeo] {slug}: aucun créneau extrait "
              f"(flux non ouvert ou format inconnu — {len(captured)} XHR JSON capturés)")
        # dump brut pour calibrer les sélecteurs lors du 1er run proxifié réel
        if captured:
            write_json(f"{OUT_DIR}/_debug_bookeo_{slug}.json", {"url": url, "raw": captured[:5]})
        return None

    return build_store(slug, website, url, slots)


def build_store(slug: str, website: str, url: str, slots: set) -> dict:
    """Assemble la structure enseigne->centre->salle->sessions (format 4escape_all)."""
    nom = slug.replace("-", " ").title()
    cid = f"bookeo:{slug}"
    rid = f"bookeo:{slug}:room"
    today = datetime.utcnow().date()
    sessions: dict[str, dict] = {}
    for d, h in slots:
        if d is None:                                   # créneau DOM sans date -> aujourd'hui
            d = today.isoformat()
        key = f"{d}T{h}|{rid}"
        sessions[key] = {
            "date": d, "heure": h, "duree_minutes": None, "room_id": rid,
            "prix_total": {}, "prix_joueur": {}, "prix_total_moyen": None,
            "nb_joueurs_min": None, "nb_joueurs_max": None, "remaining_players": None,
            "booked": False, "dispo": True}
    return {
        "company": cid, "enseigne_id": _norm_id(nom), "enseigne_nom": nom,
        "website": website, "source": "bookeo",
        "centres": {cid: {"nom": nom, "cp": "", "ville": "", "adresse": "", "lat": None, "lon": None}},
        "rooms": {rid: {"name": nom, "centre_id": cid, "duration": None,
                        "min_players": None, "max_players": None, "difficulty": None,
                        "theme": classify_theme(nom, "")}},
        "sessions": sessions, "prices_locked": True,
    }


def merge_into_store(slug: str, fresh: dict) -> int:
    """Fusion idempotente avec l'historique du venue (premier_vu/dernier_vu)."""
    path = f"{OUT_DIR}/bookeo__{slug}.json"
    st = read_json(path, {}) or {}
    st.update({k: fresh[k] for k in ("company", "enseigne_id", "enseigne_nom",
                                     "website", "source", "centres", "rooms", "prices_locked")})
    st.setdefault("sessions", {})
    now = now_iso()
    for key, sess in fresh["sessions"].items():
        ex = st["sessions"].get(key)
        if ex is None:
            ex = {**sess, "premier_vu": now, "statut": None, "seen_free": True, "seen_booked": False}
        else:
            ex.update({k: sess[k] for k in ("dispo", "booked")})
            ex["seen_free"] = True
        ex["dernier_vu"] = ex["releve"] = now
        st["sessions"][key] = ex
    st["_meta"] = {"last_scrape": now, "engine": "bookeo",
                   "n_centres": len(st["centres"]), "n_rooms": len(st["rooms"]),
                   "n_sessions": len(st["sessions"])}
    write_json(path, st)
    return len(fresh["sessions"])


def main() -> int:
    targets = sys.argv[1:]
    venues = bookeo_venues()
    if targets:
        venues = {k: v for k, v in venues.items() if k in targets}
    if not venues:
        print("[bookeo] aucune venue Bookeo à traiter")
        return 0
    print(f"[bookeo] {len(venues)} venue(s) — {escape_proxy.describe()}")
    if not escape_proxy.enabled():
        print("[bookeo] ⚠ pas de proxy : Bookeo bloque les IP datacenter, les venues "
              "seront probablement refusées. Définir PROXY_SERVER/USERNAME/PASSWORD.")
    total = ok = 0
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--ignore-certificate-errors",
            "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(ignore_https_errors=True, locale="fr-FR",
                            viewport={"width": 1366, "height": 900}, user_agent=UA,
                            proxy=escape_proxy.playwright_proxy())
        for slug, e in venues.items():
            site = e.get("website")
            if not site:
                continue
            pg = ctx.new_page()
            try:
                fresh = scrape_venue(pg, slug, site)
            finally:
                pg.close()
            if fresh:
                n = merge_into_store(slug, fresh)
                ok += 1
                total += n
                print(f"  [bookeo] ✓ {slug}: {n} créneaux")
        b.close()
    print(f"[bookeo] terminé : {ok}/{len(venues)} venues, {total} créneaux")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
