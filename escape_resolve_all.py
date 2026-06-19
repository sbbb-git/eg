#!/usr/bin/env python3
"""escape_resolve_all.py — résolution de masse des 256 escape games IDF.

2 étages, RESUMABLE (cache escape_sites_cache.json) :
  1) pour chaque venue the-escapers (sitemap), fetch la fiche -> extrait le
     SITE OFFICIEL (payload SSR).
  2) fetch le site officiel (home + pages résa) -> détecte la PLATEFORME de
     réservation + extrait une grille de PRIX.

Écrit dans l'annuaire (website/platform/prix_min/prix_max) par slug normalisé.
Relançable : ne re-fetch pas ce qui est déjà en cache.

  python3 escape_resolve_all.py            # tout
  python3 escape_resolve_all.py 40         # limite à 40 nouvelles résolutions
"""
from __future__ import annotations

import re
import sys
import time
from urllib.request import Request, urlopen

from safestore import read_json, write_json

SITEMAP = "https://www.the-escapers.com/sitemap.xml"
CACHE = "escape_sites_cache.json"
DIRECTORY = "escape_idf_directory.json"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
PATHS = ["", "reservation/", "tarifs/", "reserver/", "booking/"]
MIN_PP, MAX_PP = 14, 99

COMMUNES = read_json("_communes.json", None) or {}  # optionnel
NOISE = ("the-escapers", "google", "facebook", "instagram", "cloudinary", "youtube",
         "gstatic", "schema", "w3.org", "jsdelivr", "static.", "discord", "x.com",
         "twitter", "apple", "linkedin", "tiktok", "whatsapp", "tripadvisor",
         "onesignal", "bit.ly", "goo.gl", "maps.", "gravatar", "wp.com", "paypal",
         "stripe", "googletagmanager", "doubleclick", "sentry", "cookiebot")
SIGS = [
    ("4escape", r"4escape\.io|4escape\.app"),
    ("bookeo", r"bookeo"),
    ("smeetz", r"smeetz"),
    ("anybuddy", r"anybuddy"),
    ("guidap", r"guidap"),
    ("wechamber", r"wechamber"),
    ("regiondo", r"regiondo"),
    ("xola", r"xola"),
    ("fareharbor", r"fareharbor"),
    ("yurplan", r"yurplan"),
    ("weezevent", r"weezevent"),
    ("placeminute", r"placeminute"),
    ("bsport", r"bsport\.io"),
    ("mindbody", r"mindbody|healcode"),
    ("supersaas", r"supersaas"),
    ("simplybook", r"simplybook"),
    ("planyo", r"planyo"),
    ("balsamiq", r"bookines"),
    ("woocommerce", r"woocommerce|wc-ajax|add-to-cart"),
    ("wordpress", r"wp-content|wp-json"),
]


def fetch(url: str, tmo: int = 12, cap: int = 1_500_000) -> str:
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=tmo) as r:
            return r.read(cap).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return ""


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def pick_site(slug: str, html: str) -> str | None:
    doms = sorted(set(re.findall(r"https?://([a-z0-9.\-]+\.[a-z]{2,})(?:[/\"]|$)", html)))
    cand = [d for d in doms if not any(n in d for n in NOISE)]
    if not cand:
        return None
    ns = norm(slug)
    best, bs = None, -1
    for d in cand:
        root = norm(d.split(":")[0])
        sc = sum(1 for tok in slug.split("-") if len(tok) > 2 and tok in root)
        if ns[:6] and ns[:6] in root:
            sc += 3
        if root.startswith("www") and sc > 0:
            sc += 0
        if sc > bs:
            bs, best = sc, d
    return "https://" + best if best else None


def detect_platform(html: str) -> str:
    for name, rx in SIGS:
        if re.search(rx, html, re.I):
            return name
    return "custom"


def extract_prices(html: str) -> tuple[int | None, int | None]:
    vals = {int(x) for x in re.findall(r"(\d{2,3})\s*€\s*(?:/|par)?\s*(?:joueur|personne|pers|/\s*pers|/\s*j)\b", html, re.I)
            if MIN_PP <= int(x) <= MAX_PP}
    vals |= {int(p) for _, p in re.findall(r"(\d)\s*joueurs?[^0-9€]{0,40}?(\d{2,3})\s*€", html, re.I)
             if MIN_PP <= int(p) <= MAX_PP}
    return (min(vals), max(vals)) if vals else (None, None)


def resolve_site(url: str) -> tuple[str, int | None, int | None]:
    base = url.rstrip("/") + "/"
    platform, pmin, pmax = "custom", None, None
    for p in PATHS:
        html = fetch(base + p)
        if not html:
            continue
        plat = detect_platform(html)
        if plat not in ("custom", "wordpress", "woocommerce"):
            platform = plat
        elif platform == "custom":
            platform = plat
        a, b = extract_prices(html)
        if a and not pmin:
            pmin, pmax = a, b
        if platform not in ("custom", "wordpress", "woocommerce"):
            break
        time.sleep(0.1)
    return platform, pmin, pmax


def main() -> int:
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    sm = fetch(SITEMAP, tmo=25, cap=10_000_000)
    locs = re.findall(r"<loc>([^<]+)</loc>", sm)
    IDF = set(read_json("escape_idf_communes.json", []) or [])
    venues = []
    for l in locs:
        m = re.match(r"https://www\.the-escapers\.com/escape-game/([a-z0-9\-]+)/([a-z0-9\-]+)$", l)
        if m and (not IDF or m.group(1) in IDF):
            venues.append((m.group(2), m.group(1), l))
    cache = read_json(CACHE, {}) or {}
    cache.setdefault("venues", {})
    done = 0
    for slug, city, te_url in venues:
        e = cache["venues"].get(slug)
        if e and e.get("stage") == 2:
            continue
        if done >= budget:
            break
        e = e or {"city": city, "te_url": te_url}
        if not e.get("website"):
            html = fetch(te_url, cap=2_500_000)
            e["website"] = pick_site(slug, html) if html else None
            e["stage"] = 1
        if e.get("website"):
            plat, pmin, pmax = resolve_site(e["website"])
            e["platform"], e["prix_min"], e["prix_max"] = plat, pmin, pmax
        e["stage"] = 2
        e["ts"] = int(time.time())
        cache["venues"][slug] = e
        done += 1
        if done % 10 == 0:
            write_json(CACHE, cache)
            print(f"[resolve-all] {done} traités… dernier: {slug} "
                  f"site={e.get('website')} plat={e.get('platform')}")
    write_json(CACHE, cache)

    # injecte dans l'annuaire (par slug normalisé)
    directory = read_json(DIRECTORY, {}) or {}
    by_norm = {}
    for slug, e in cache["venues"].items():
        by_norm[norm(slug)] = e
    n_site = n_plat = 0
    for v in directory.get("venues", []):
        e = by_norm.get(norm(v["slug"]))
        if not e:
            continue
        if e.get("website"):
            v["website"] = e["website"]
            n_site += 1
        if e.get("platform") and e["platform"] != "custom":
            v["platform"] = e["platform"]
            n_plat += 1
        if e.get("prix_min"):
            v["prix_min"], v["prix_max"] = e["prix_min"], e["prix_max"]
    from collections import Counter
    plats = Counter(e.get("platform") for e in cache["venues"].values() if e.get("stage") == 2)
    directory.setdefault("_meta", {}).update({
        "sites_resolved": n_site, "platforms_resolved": n_plat,
        "platform_breakdown": dict(plats),
    })
    write_json(DIRECTORY, directory)
    print(f"[resolve-all] cache: {sum(1 for e in cache['venues'].values() if e.get('stage')==2)} résolus | "
          f"annuaire: {n_site} sites, {n_plat} plateformes")
    print(f"[resolve-all] plateformes: {dict(plats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
