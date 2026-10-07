#!/usr/bin/env python3
"""sanity_check.py — surveillance de bout en bout de l'observatoire.

Contrôle SUPABASE (ce que lit réellement le dashboard), donc toute la chaîne
scrape -> store -> sync -> base, plus les erreurs consignées par le scraper dans
le store (si le cache CI est disponible). Problèmes détectés :

  - aucun relevé récent dans la base (le pipeline est arrêté) ;
  - enseigne qui a des salles mais AUCUNE session à venir en base
    (ex. Monkeykwest, dont la réponse API de 12,8 Mo était tronquée : 0 session,
    sans qu'aucune alerte ne se déclenche — l'ancienne version surveillait le
    store d'un scraper abandonné) ;
  - enseigne dont le dernier relevé est trop ancien ;
  - enseigne en erreur au dernier passage du scraper.

Sortie : escape_sanity.json + code retour 1 si au moins un problème (le workflow
sanity.yml ouvre alors une issue GitHub, et la referme quand tout est rétabli).

  python3 sanity_check.py [--hours 8]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from safestore import read_json, write_json

# URL et clé PUBLIQUE (lecture seule, déjà présentes dans index.html)
URL = os.environ.get("SUPABASE_URL") or "https://nbvronwfvoksonatolcs.supabase.co"
KEY = os.environ.get("SUPABASE_READ_KEY") or "sb_publishable_5vFAbOG-hHH0s9mAoYmGyA_EG4bIQYj"
OUT_FILE = "escape_sanity.json"


def get(q: str):
    req = Request(f"{URL.rstrip('/')}/rest/v1/{q}", headers={"apikey": KEY, "Authorization": "Bearer " + KEY})
    return json.loads(urlopen(req, timeout=90).read())


def get_all(q: str) -> list:
    out, off = [], 0
    while True:
        page = get(f"{q}&limit=1000&offset={off}")
        out += page
        if len(page) < 1000:
            return out
        off += 1000


def age_h(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    return (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0, help="seuil de fraîcheur")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    ens = {e["id"]: e["nom"] for e in get("enseignes?select=id,nom")}
    cen = {c["id"]: c["enseigne_id"] for c in get("centres?select=id,enseigne_id")}
    sal = {s["id"]: cen.get(s["centre_id"]) for s in get("salles?select=id,centre_id")}
    n_salles = defaultdict(int)
    for e in sal.values():
        n_salles[e] += 1
    fut = get_all(f"sessions?select=salle_id,dernier_vu&date=gte.{today}")
    n_fut, last = defaultdict(int), defaultdict(str)
    for x in fut:
        e = sal.get(x["salle_id"])
        n_fut[e] += 1
        if (x.get("dernier_vu") or "") > last[e]:
            last[e] = x["dernier_vu"]

    problems: list[dict] = []
    glob_last = max(last.values(), default="")
    g_age = age_h(glob_last, now)
    if g_age is None or g_age > args.hours:
        problems.append({"type": "pipeline_arrete", "enseigne": "*",
                         "detail": f"dernier relevé en base il y a {g_age and round(g_age, 1)} h"})
    for eid, nom in sorted(ens.items(), key=lambda x: x[1].lower()):
        if not n_salles.get(eid):
            continue                                   # enseigne sans salle : rien à relever
        if not n_fut.get(eid):
            problems.append({"type": "aucune_session", "enseigne": nom,
                             "detail": f"{n_salles[eid]} salles mais 0 session à venir en base"})
            continue
        a = age_h(last[eid], now)
        if a is not None and a > args.hours:
            problems.append({"type": "releve_ancien", "enseigne": nom,
                             "detail": f"dernier relevé il y a {a:.1f} h"})
    # erreurs consignées par le scraper (store du cache CI, si présent)
    for f in glob.glob("escape_data/4escape_all/*.json"):
        m = (read_json(f, {}) or {}).get("_meta", {})
        if m.get("erreur") and (m.get("erreur_at") or "") > (m.get("last_scrape") or ""):
            problems.append({"type": "erreur_scraper", "enseigne": os.path.basename(f)[:-5],
                             "detail": m["erreur"]})

    report = {"generated": now.isoformat(timespec="seconds"), "threshold_hours": args.hours,
              "dernier_releve": glob_last, "n_enseignes": len(ens), "n_sessions_a_venir": len(fut),
              "n_problemes": len(problems), "problemes": problems}
    write_json(OUT_FILE, report)
    print(f"[sanity] {len(ens)} enseignes · {len(fut)} sessions à venir · dernier relevé "
          f"il y a {g_age and round(g_age, 1)} h · {len(problems)} problème(s) (seuil {args.hours} h)")
    for p in problems:
        print(f"  ⚠ [{p['type']}] {p['enseigne']} — {p['detail']}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
