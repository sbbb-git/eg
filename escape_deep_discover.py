#!/usr/bin/env python3
"""escape_deep_discover.py — découverte PROFONDE des comptes 4escape cachés.

Beaucoup d'enseignes utilisent 4escape sous un sous-domaine NON devinable
(ex. escapegameaventure.fr -> escapegameaventure-puteaux.4escape.io). La
recherche par nom les rate. Ici on charge la page de réservation en Playwright,
on intercepte l'appel <company>.4escape.io, et on ajoute le compte trouvé.

Idempotent + resumable : on ne re-teste pas un site déjà vu (pw_tried).
Conçu pour tourner en CI (workflow escape-deep-discover.yml), par lots.

  python3 escape_deep_discover.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

from safestore import read_json, write_json
import escape_proxy

COMPANIES_FILE = "escape_4escape_companies.json"
CACHE = "escape_sites_cache.json"
DIRECTORY = "escape_idf_directory.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
RESA = ["reservation/", "reserver/", "booking/", "reservations/", "reserver-en-ligne/", "book/", ""]


def sld(website: str):
    m = re.search(r"https?://(?:www\.)?([a-z0-9\-]+)\.", website or "", re.I)
    return m.group(1).lower() if m else None


def resa_url(site: str) -> str:
    base = site.rstrip("/")
    try:
        html = urlopen(Request(base + "/", headers={"User-Agent": UA}), timeout=12).read(600_000).decode("utf-8", "replace")
        m = re.search(r'href="([^"]*(?:reserv|booking|book-now)[^"]*)"', html, re.I)
        if m:
            return m.group(1) if m.group(1).startswith("http") else base + "/" + m.group(1).lstrip("/")
    except Exception:
        pass
    return base + "/reservation/"


def validate(company: str):
    try:
        d = json.loads(urlopen(Request(f"https://{company}.4escape.io/api/public/settings",
                       headers={"User-Agent": UA, "Accept": "application/json"}), timeout=15).read())
        org = d.get("organization", {}) or {}
        return (org.get("display_name") or org.get("name") or company) if d.get("success") else None
    except Exception:
        return None


def capture_4escape_host(page, url: str) -> str | None:
    hosts: set[str] = set()
    rx = re.compile(r"https?://([a-z0-9\-]+)\.4escape\.io")
    page.on("response", lambda r: [hosts.add(m.group(1)) for m in [rx.search(r.url)] if m])
    page.on("request", lambda r: [hosts.add(m.group(1)) for m in [rx.search(r.url)] if m])
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
        try:
            page.click("text=Accepter tout", timeout=2000)
        except Exception:
            pass
        page.wait_for_timeout(3500)
    except Exception:
        pass
    hosts.discard("widgets")
    return sorted(hosts)[0] if hosts else None


NOISE = re.compile(r"universite|u-paris|psl\.eu|sorbonne|ecole|mus[ée]e|biblioth|ligue|"
                   r"archives|conciergerie|monnaie|cirque|bateaux|visites-spectacles|"
                   r"\.edu|prod\.|cancer|psoriasis|parlons", re.I)


def candidates() -> dict[str, str]:
    """website -> slug, pour les venues PAS encore identifiées 4escape (hors bruit)."""
    out: dict[str, str] = {}
    directory = read_json(DIRECTORY, {}) or {}
    event_slugs = {v.get("slug") for v in directory.get("venues", []) if v.get("likely_event")}
    for slug, e in (read_json(CACHE, {}) or {}).get("venues", {}).items():
        w = e.get("website")
        if w and e.get("platform") != "4escape" and not NOISE.search(w) and slug not in event_slugs:
            out[w] = slug
    for v in directory.get("venues", []):
        w = v.get("website")
        if w and not v.get("covered_4escape") and not v.get("likely_event") and not NOISE.search(w):
            out.setdefault(w, v.get("slug", ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    store = read_json(COMPANIES_FILE, {}) or {}
    store.setdefault("companies", {})
    store.setdefault("tried", {})
    pw_tried = store.setdefault("pw_tried", {})
    known_slds = {sld(c.get("website", "")) for c in store["companies"].values()} | set(store["companies"])

    cands = {w: s for w, s in candidates().items() if w not in pw_tried and sld(w) not in known_slds}
    todo = list(cands.items())[:args.limit]
    print(f"[deep] {len(cands)} candidats restants, traite {len(todo)} ce run")
    added = 0
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--ignore-certificate-errors",
                                                    "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(ignore_https_errors=True, locale="fr-FR",
                            viewport={"width": 1366, "height": 900}, user_agent=UA,
                            proxy=escape_proxy.playwright_proxy())
        for website, slug in todo:
            pw_tried[website] = datetime.utcnow().isoformat()
            pg = ctx.new_page()
            comp = capture_4escape_host(pg, resa_url(website))
            pg.close()
            if not comp or comp in store["companies"]:
                print(f"[deep] – {website[:45]} -> {comp or 'rien'}")
                continue
            nom = validate(comp)
            if nom:
                store["companies"][comp] = {"website": website, "source": "deep-pw",
                                            "found": datetime.utcnow().isoformat()}
                added += 1
                print(f"[deep] ✓ {comp:28} ({nom}) <- {website[:40]}")
            write_json(COMPANIES_FILE, store)   # sauvegarde fréquente (résilient)
        b.close()
    store["_meta"] = {**store.get("_meta", {}), "n_companies": len(store["companies"])}
    write_json(COMPANIES_FILE, store)
    print(f"[deep] +{added} comptes 4escape cachés -> {len(store['companies'])} au total "
          f"({len(pw_tried)} sites sondés en profondeur)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
