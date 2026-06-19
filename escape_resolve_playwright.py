#!/usr/bin/env python3
"""escape_resolve_playwright.py — résout les enseignes en widget 4escape « UUID »
(merciinternet/bombsquad…) dont le sous-domaine <company>.4escape.io n'est pas
devinable depuis l'URL.

Charge la page de réservation en headless, intercepte les appels réseau, capture
le host <company>.4escape.io, valide via /settings, et l'ajoute au catalogue
escape_4escape_companies.json.

  python3 escape_resolve_playwright.py URL [URL ...]
  python3 escape_resolve_playwright.py            # lit escape_pending_urls.json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

from safestore import read_json, write_json

COMPANIES_FILE = "escape_4escape_companies.json"
PENDING = "escape_pending_urls.json"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"


def capture_company(page_url: str, timeout: int = 60000) -> str | None:
    """Charge la page et renvoie le sous-domaine <company> de *.4escape.io."""
    hosts: set[str] = set()
    rx = re.compile(r"https?://([a-z0-9\-]+)\.4escape\.io")
    seen = lambda r: [hosts.add(m.group(1)) for m in [rx.search(r.url)] if m]
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--ignore-certificate-errors"])
        ctx = b.new_context(ignore_https_errors=True, locale="fr-FR")
        pg = ctx.new_page()
        pg.on("request", seen)
        pg.on("response", seen)
        try:
            pg.goto(page_url, wait_until="networkidle", timeout=timeout)
        except Exception:
            pass
        pg.wait_for_timeout(4000)
        b.close()
    hosts.discard("widgets")
    return sorted(hosts)[0] if hosts else None


def validate(company: str) -> str | None:
    """Renvoie le nom de l'enseigne si /settings répond, sinon None."""
    try:
        d = json.loads(urlopen(Request(
            f"https://{company}.4escape.io/api/public/settings",
            headers={"User-Agent": UA, "Accept": "application/json"}), timeout=15).read())
        org = d.get("organization", {}) or {}
        return (org.get("display_name") or org.get("name") or company) if d.get("success") else None
    except Exception:
        return None


def main() -> int:
    urls = sys.argv[1:] or (read_json(PENDING, []) or [])
    if not urls:
        print("Usage: escape_resolve_playwright.py URL [URL ...]"); return 1
    store = read_json(COMPANIES_FILE, {}) or {}
    store.setdefault("companies", {}); store.setdefault("tried", {})
    added = 0
    for url in urls:
        comp = capture_company(url)
        if not comp:
            print(f"[pw] ✗ aucun host 4escape capté <- {url}"); continue
        nom = validate(comp)
        if not nom:
            print(f"[pw] ✗ {comp}.4escape.io invalide <- {url}"); continue
        store["companies"][comp] = {"website": url.split("#")[0], "source": "playwright",
                                    "found": datetime.utcnow().isoformat()}
        store["tried"][comp] = True
        added += 1
        print(f"[pw] ✓ {comp:24} ({nom}) <- {url[:50]}")
    store["_meta"] = {**store.get("_meta", {}), "n_companies": len(store["companies"])}
    write_json(COMPANIES_FILE, store)
    print(f"[pw] +{added} enseignes -> {len(store['companies'])} au total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
