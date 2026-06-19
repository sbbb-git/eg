#!/usr/bin/env python3
"""escape_engine_generic.py — capture la disponibilité des enseignes NON-4escape
(Bookeo, WooCommerce custom, FullCalendar, etc.) via Playwright.

Le marché est fragmenté : chaque plateforme charge ses créneaux par un XHR
différent, souvent dans un widget qui vérifie le referer. On charge donc la VRAIE
page de réservation du venue, on intercepte toutes les réponses JSON, et on garde
celles qui ressemblent à de la disponibilité (date/heure + dispo/prix).

  python3 escape_engine_generic.py SITE_URL [SITE_URL ...]
"""
from __future__ import annotations

import json
import re
import sys
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

from safestore import write_json

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
RESA_PATHS = ["", "reservation/", "reserver/", "booking/", "reservations/",
              "reserver-en-ligne/", "nos-reservations/", "book/", "agenda/"]
AVAIL_HINT = re.compile(r"avail|slot|creneau|cr%C3%A9neau|session|booking|calendar|time|day|schedule|dispo", re.I)
SLOT_KEYS = ("date", "day", "start", "time", "heure", "datetime", "startTime")
STATE_KEYS = ("available", "dispo", "free", "booked", "places", "seats", "spots",
              "remaining", "capacity", "price", "prix", "amount")


def find_resa_url(site: str) -> str:
    """Cherche un lien de réservation sur la home, sinon renvoie la home."""
    base = site.rstrip("/")
    try:
        html = urlopen(Request(base + "/", headers={"User-Agent": UA}), timeout=15).read(800_000).decode("utf-8", "replace")
        m = re.search(r'href="([^"]*(?:reserv|booking|book-now|agenda)[^"]*)"', html, re.I)
        if m:
            href = m.group(1)
            return href if href.startswith("http") else base + "/" + href.lstrip("/")
    except Exception:
        pass
    return base + "/reservation/"


def looks_like_slots(obj) -> int:
    """Score : combien d'objets ressemblent à des créneaux dans ce JSON."""
    found = 0
    def walk(o, depth=0):
        nonlocal found
        if depth > 6:
            return
        if isinstance(o, dict):
            keys = {k.lower() for k in o.keys()}
            if any(k in keys for k in SLOT_KEYS) and any(k in keys for k in STATE_KEYS):
                found += 1
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:200]:
                walk(v, depth + 1)
    walk(obj)
    return found


def capture(site: str, timeout: int = 55000) -> dict:
    resa = find_resa_url(site)
    captured = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--ignore-certificate-errors"])
        ctx = b.new_context(ignore_https_errors=True, locale="fr-FR")
        pg = ctx.new_page()

        def on_resp(r):
            try:
                ct = r.headers.get("content-type", "")
                if "json" not in ct and not AVAIL_HINT.search(r.url):
                    return
                body = r.json()
            except Exception:
                return
            sc = looks_like_slots(body)
            if sc:
                captured.append({"url": r.url[:200], "n_slots_like": sc, "sample": body})

        pg.on("response", on_resp)
        try:
            pg.goto(resa, wait_until="networkidle", timeout=timeout)
            pg.wait_for_timeout(3500)
            for sel in ["text=Réserver", "text=réserver", "text=Book now", "[class*=book]", "[class*=reserv]"]:
                try:
                    el = pg.query_selector(sel)
                    if el:
                        el.click(timeout=2500); pg.wait_for_timeout(3500); break
                except Exception:
                    pass
        except Exception:
            pass
        b.close()
    captured.sort(key=lambda c: c["n_slots_like"], reverse=True)
    return {"site": site, "resa_url": resa, "captures": captured[:5]}


def main() -> int:
    sites = sys.argv[1:]
    if not sites:
        print("Usage: escape_engine_generic.py SITE_URL [...]"); return 1
    for site in sites:
        res = capture(site)
        best = res["captures"][0] if res["captures"] else None
        print(f"\n### {site}  (résa: {res['resa_url']})")
        if best:
            print(f"  ✓ {best['n_slots_like']} créneaux-like via {best['url'][:90]}")
            slug = re.sub(r"\W+", "_", site)
            write_json(f"escape_data/generic/{slug}.json", res)
            print(f"  -> sauvé escape_data/generic/{slug}.json")
        else:
            print("  ✗ aucune réponse JSON ressemblant à des créneaux (widget JS profond / clic requis)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
