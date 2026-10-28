#!/usr/bin/env python3
"""supa_purge.py — garde la base Supabase légère.

Les sessions de plus de RETENTION_JOURS (35 : le dashboard lit 30 jours de ventes passées
pour les escape games, 14 pour les parcs) sont ARCHIVÉES dans archive/sessions_AAAA-MM.csv.gz
(versionné dans le dépôt, rien n'est perdu) puis SUPPRIMÉES de la base, jour par jour.
supa_sync.py ne les renvoie plus (même rétention).

  SUPABASE_URL=… SUPABASE_KEY=… python3 supa_purge.py [--jours 35]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import time
from datetime import date, timedelta
from urllib.request import Request, urlopen

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY", "")
COLS = ["salle_id", "date", "heure", "duree_minutes", "nb_joueurs_min", "nb_joueurs_max", "prix_total",
        "prix_total_moyen", "dispo", "booked", "statut", "premier_vu", "dernier_vu", "releve", "prive", "groupby",
        "places_max", "places_restantes", "places_init", "places_vendues"]


def _req(method: str, path: str, headers: dict | None = None):
    for i in range(4):
        try:
            return urlopen(Request(f"{URL}/rest/v1/{path}", method=method,
                                   headers={"apikey": KEY, "Authorization": "Bearer " + KEY, **(headers or {})}),
                           timeout=120).read()
        except Exception as e:
            if i == 3:
                raise
            print(f"[purge] nouvelle tentative ({e})")
            time.sleep(5 * (i + 1))


def lire(d1: str, d2: str) -> list[dict]:
    out, f = [], 0
    while True:
        b = json.loads(_req("GET", f"sessions?select={','.join(COLS)}&date=gte.{d1}&date=lt.{d2}"
                                   f"&order=date,salle_id,heure", {"Range": f"{f}-{f + 999}"}) or b"[]")
        out += b
        if len(b) < 1000:
            return out
        f += 1000


def archiver(mois: str, rows: list[dict]) -> int:
    os.makedirs("archive", exist_ok=True)
    p = f"archive/sessions_{mois}.csv.gz"
    tout = {}
    if os.path.exists(p):
        with gzip.open(p, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                tout[(r["salle_id"], r["date"], r["heure"])] = r
    for r in rows:
        r = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("" if v is None else v))
             for k, v in r.items()}
        tout[(r["salle_id"], r["date"], r["heure"])] = r
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS, extrasaction="ignore")
    w.writeheader()
    for k in sorted(tout):
        w.writerow(tout[k])
    with open(p, "wb") as fh:                          # mtime=0 : fichier identique si rien ne change
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=9) as gz:
            gz.write(buf.getvalue().encode())
    return len(tout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jours", type=int, default=int(os.environ.get("SUPA_RETENTION_JOURS", "35")))
    args = ap.parse_args()
    if not (URL and KEY):
        print("[purge] SUPABASE_URL / SUPABASE_KEY manquants — abandon.")
        return 1
    limite = date.today() - timedelta(days=args.jours)
    premiere = json.loads(_req("GET", "sessions?select=date&order=date.asc&limit=1"))
    if not premiere or premiere[0]["date"] >= limite.isoformat():
        print(f"[purge] rien avant le {limite} — base déjà dans la rétention")
        return 0
    d = date.fromisoformat(premiere[0]["date"]).replace(day=1)
    total = 0
    while d < limite:
        fin = min((d + timedelta(days=32)).replace(day=1), limite)
        rows = lire(d.isoformat(), fin.isoformat())
        if rows:
            n = archiver(d.strftime("%Y-%m"), rows)
            # suppression jour par jour : une requête courte ne dépasse jamais le délai de la clé
            j = d
            while j < fin:
                _req("DELETE", f"sessions?date=eq.{j.isoformat()}", {"Prefer": "return=minimal"})
                j += timedelta(days=1)
            total += len(rows)
            print(f"[purge] {d:%Y-%m} : {len(rows)} sessions archivées ({n} dans l'archive du mois) puis supprimées")
        d = fin
    print(f"[purge] {total} sessions antérieures au {limite} archivées et supprimées")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
