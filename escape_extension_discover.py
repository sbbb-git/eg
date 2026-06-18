#!/usr/bin/env python3
"""escape_extension_discover.py — détection de plateforme de réservation.

Pour chaque URL du catalogue (`escape_extension_brands.json`), on fetch la
homepage + quelques paths courants de réservation, puis on applique une
batterie de signatures regex pour deviner la plateforme sous-jacente.

Plateformes détectées :
  - bsport        : ?company=NNNN  / app.bsport.io
  - mindbody      : mb_site_id / healcode / brandedweb.mindbodyonline
  - anybuddy      : anybuddyapp.com
  - doinsport     : doinsport.club / api.doinsport
  - clubsresa     : clubsresa
  - eversports    : eversports
  - fullcalendar  : fullcalendar (JS) — très répandu chez les escape rooms
  - timekit       : timekit.io / book.js timekit
  - wechamber     : wechamber (SPA React)
  - resaspa       : resaspa
  - wordpress     : wp-content (custom WP, à traiter case-by-case)

Sortie : `escape_extension_resolved.json` (idempotent — on merge, on ne perd
pas les résolutions précédentes, on met juste à jour `last_checked`).

Usage:
  python3 escape_extension_discover.py                # toutes les marques
  python3 escape_extension_discover.py --limit 5      # 5 premières
  python3 escape_extension_discover.py --offline      # pas de réseau (no-op)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from safestore import read_json, write_json

BRANDS_FILE = "escape_extension_brands.json"
RESOLVED_FILE = "escape_extension_resolved.json"

COMMON_PATHS = ["", "/booking", "/reserver", "/reservation", "/planning", "/calendrier"]

# UA rotation par enseigne pour limiter le blocage. On reste honnête : header
# descriptif, pas d'usurpation agressive.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# (plateforme, regex). Ordre = priorité ; première qui matche gagne pour le
# "platform" principal, mais on conserve TOUTES les correspondances en evidence.
SIGNATURES = [
    ("bsport",       re.compile(r"app\.bsport\.io|bsport\.io|[?&]company=\d+", re.I)),
    ("mindbody",     re.compile(r"mb_site_id|healcode|brandedweb\.mindbodyonline|mindbodyonline\.com", re.I)),
    ("anybuddy",     re.compile(r"anybuddyapp\.com|anybuddy\.com", re.I)),
    ("doinsport",    re.compile(r"doinsport\.club|api\.doinsport", re.I)),
    ("clubsresa",    re.compile(r"clubsresa", re.I)),
    ("eversports",   re.compile(r"eversports", re.I)),
    ("wechamber",    re.compile(r"wechamber", re.I)),
    ("resaspa",      re.compile(r"resaspa", re.I)),
    ("timekit",      re.compile(r"timekit\.io|timekit-sdk|data-timekit|book\.timekit", re.I)),
    ("fullcalendar", re.compile(r"fullcalendar|fc-event|fc-daygrid|FullCalendar\.Calendar", re.I)),
    ("classpass",    re.compile(r"classpass\.com", re.I)),
    ("wordpress",    re.compile(r"wp-content|wp-json|wordpress", re.I)),
]


def fetch(url: str, ua: str, timeout: int = 12) -> str | None:
    """GET avec retry/backoff exponentiel (3s, 6s, 12s, 24s)."""
    backoff = 3
    for attempt in range(4):
        try:
            req = Request(url, headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/json,*/*",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            })
            with urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read(600_000).decode(charset, errors="replace")
        except HTTPError as e:
            # 4xx : inutile de réessayer (sauf 429)
            if e.code != 429 and 400 <= e.code < 500:
                return None
        except (URLError, TimeoutError, OSError):
            pass
        if attempt < 3:
            time.sleep(backoff)
            backoff *= 2
    return None


def detect(html: str) -> list[str]:
    found = []
    for name, rx in SIGNATURES:
        if rx.search(html):
            found.append(name)
    return found


def discover_brand(brand: dict, ua: str, offline: bool) -> dict:
    base = brand["url"].rstrip("/")
    evidence: dict[str, list[str]] = {}
    matches: list[str] = []
    if not offline:
        for path in COMMON_PATHS:
            html = fetch(base + path, ua)
            if not html:
                continue
            for m in detect(html):
                matches.append(m)
                evidence.setdefault(m, []).append(path or "/")
            # politesse : on ne martèle pas
            time.sleep(0.8)

    # plateforme principale = première signature (par priorité) trouvée
    platform = "unknown"
    for name, _ in SIGNATURES:
        if name in matches:
            platform = name
            break
    if platform == "unknown" and not offline and not evidence:
        # rien détecté mais le site répond -> probable calendrier propriétaire
        platform = "custom_proprietary"

    return {
        "label": brand["label"],
        "url": brand["url"],
        "cp": brand["cp"],
        "type": brand["type"],
        "priorite": brand["priorite"],
        "platform": platform,
        "candidates": sorted(set(matches)),
        "evidence": {k: sorted(set(v)) for k, v in evidence.items()},
        "last_checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="limiter le nb de marques")
    ap.add_argument("--offline", action="store_true", help="pas de requêtes réseau")
    ap.add_argument("--only-unknown", action="store_true", help="ne re-tester que les unknown")
    args = ap.parse_args()

    catalog = read_json(BRANDS_FILE, {})
    brands = catalog.get("brands", [])
    if not brands:
        print(f"[discover] aucune marque dans {BRANDS_FILE}", file=sys.stderr)
        return 1

    resolved = read_json(RESOLVED_FILE, {}) or {}
    by_slug = resolved.get("resolved", {})

    todo = brands[: args.limit] if args.limit else brands
    for i, brand in enumerate(todo, 1):
        slug = slugify(brand["label"])
        if args.only_unknown and by_slug.get(slug, {}).get("platform") not in (None, "unknown"):
            continue
        ua = USER_AGENTS[i % len(USER_AGENTS)]
        print(f"[discover] ({i}/{len(todo)}) {brand['label']} ...", flush=True)
        rec = discover_brand(brand, ua, args.offline)
        rec["slug"] = slug
        by_slug[slug] = rec
        print(f"           -> {rec['platform']} {rec['candidates'] or ''}")

    out = {
        "_meta": {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(by_slug),
        },
        "resolved": by_slug,
    }
    write_json(RESOLVED_FILE, out)
    print(f"[discover] {len(by_slug)} marques -> {RESOLVED_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
