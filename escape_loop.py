#!/usr/bin/env python3
"""escape_loop.py — relevés continus dans un seul job CI.

Pourquoi : GitHub n'exécute qu'une petite partie des déclenchements planifiés
(cron « toutes les 30 min » -> un run toutes les ~5 h en pratique). Un relevé
toutes les 5 h rate l'essentiel des réservations (créneau vu libre puis pris).
Ici, un run boucle : scrape complet (~30 s, parallèle) + envoi incrémental à
Supabase, toutes les `--every` minutes, pendant `--minutes`. Le workflow enchaîne
plusieurs blocs dans un même job (jusqu'à ~5 h 30) et sauvegarde le store entre
chaque bloc ; le run suivant, mis en file d'attente, prend le relais.

À chaque tour :
  1. recharge la liste des enseignes depuis origin/main (comptes ajoutés entre-temps
     par escape-deep-discover) ;
  2. scrape toutes les enseignes ;
  3. moteur Bookeo si un proxy résidentiel est configuré (tous les `--bookeo-every` tours) ;
  4. sync Supabase des seules sessions modifiées (complet au 1er tour si --full-first).

  python3 escape_loop.py --minutes 110 --every 10 [--full-first]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import escape_4escape_all as E


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def sh(*cmd: str) -> int:
    return subprocess.call(list(cmd))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=110)
    ap.add_argument("--every", type=float, default=10)
    ap.add_argument("--full-first", action="store_true", help="sync complet au premier tour")
    ap.add_argument("--bookeo-every", type=int, default=12)
    args = ap.parse_args()
    fin = time.time() + args.minutes * 60
    tour = 0
    proxy = bool(os.environ.get("PROXY_SERVER"))
    sync = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))
    while True:
        debut = time.time()
        tour += 1
        depuis = iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        print(f"\n===== tour {tour} — {iso(datetime.now(timezone.utc))} =====", flush=True)
        sh("git", "fetch", "-q", "origin", "main")
        sh("git", "checkout", "-q", "origin/main", "--", E.COMPANIES_FILE)
        try:
            E.scrape()
        except Exception as e:                      # un tour raté ne doit pas tuer la boucle
            print(f"[loop] scrape en échec : {e!r}", flush=True)
        if proxy and tour % args.bookeo_every == 1:
            sh(sys.executable, "escape_engine_bookeo.py")
        if sync:
            cmd = [sys.executable, "supa_sync.py"]
            if not (args.full_first and tour == 1):
                cmd += ["--since", depuis]
            if subprocess.call(cmd) != 0:
                print("[loop] sync en échec (retenté au tour suivant, en complet)", flush=True)
                args.full_first, tour = True, 0     # le prochain tour renverra tout
        reste = fin - time.time()
        attente = args.every * 60 - (time.time() - debut)
        if reste < max(attente, 0) + 90:            # pas le temps d'un tour de plus
            break
        time.sleep(max(attente, 0))
    print(f"[loop] fin du bloc après {tour} tour(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
