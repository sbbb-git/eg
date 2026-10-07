#!/usr/bin/env python3
"""supa_sync.py — pousse les relevés (escape_data/4escape_all/*.json) vers
Supabase, hiérarchie enseignes/centres/salles/sessions (upsert idempotent).

Secrets (env, JAMAIS versionnés) :
  SUPABASE_URL   = https://<ref>.supabase.co
  SUPABASE_KEY   = clé secrète (service_role / sb_secret_…)

  SUPABASE_URL=… SUPABASE_KEY=… python3 supa_sync.py                # tout le store
  SUPABASE_URL=… SUPABASE_KEY=… python3 supa_sync.py --since <ISO>  # sessions modifiées depuis

Le mode --since n'envoie que les sessions dont `maj` (posé par reconcile à chaque
changement) est postérieur : indispensable pour relever toutes les ~10 min sans
renvoyer 200 000 lignes à chaque passage.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import urllib.parse as up
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from safestore import read_json, write_json

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY", "")
GEO = read_json("escape_geo_cp.json", {}) or {}
CENTROIDS = GEO.get("_dept_centroids", {})
CHUNK = 500
GEO_CACHE = "escape_data/geo_centres.json"     # conservé par le cache CI, comme le store
_geo = read_json(GEO_CACHE, {}) or {}


def _geocode(adresse: str, ville: str, cp: str):
    """Géoplateforme IGN (successeur d'api-adresse). Résultat mis en cache, y compris
    les échecs, pour ne jamais redemander la même adresse."""
    q = " ".join(x for x in (adresse, cp, ville.replace("-", " ")) if x).strip()
    if not q or not adresse:
        return None
    if q in _geo:
        return _geo[q]
    res = None
    try:
        d = json.loads(urlopen("https://data.geopf.fr/geocodage/search?"
                               + up.urlencode({"q": q, "limit": 1}), timeout=20).read())
        f = (d.get("features") or [None])[0]
        if f and f["properties"].get("score", 0) >= 0.5:
            lon, lat = f["geometry"]["coordinates"]
            res = [lat, lon]
    except Exception:
        return None                    # incident réseau : on réessaiera au prochain passage
    _geo[q] = res
    write_json(GEO_CACHE, _geo)
    return res


def _coords(c: dict):
    if c.get("lat") and c.get("lon"):
        return c["lat"], c["lon"]
    g = _geocode(c.get("adresse") or "", c.get("ville") or "", c.get("cp") or "")
    if g:
        return g[0], g[1]
    cp = c.get("cp") or ""
    g = GEO.get(cp) or CENTROIDS.get(cp[:2]) or [None, None]
    return g[0], g[1]


def _req(method: str, path: str):
    return Request(f"{URL}/rest/v1/{path}", method=method,
                   headers={"apikey": KEY, "Authorization": "Bearer " + KEY})


def purge_enseignes_orphelines(actives: set[str]) -> None:
    """Supprime les enseignes sans aucun centre ET absentes du store courant
    (ex. 'lagazettedelutece', ancien identifiant remplacé par 'la-gazette-de-lut-ce').
    Sans centre, il n'y a ni salle ni session rattachée : rien d'autre n'est effacé."""
    ens = {e["id"] for e in json.loads(urlopen(_req("GET", "enseignes?select=id"), timeout=60).read())}
    avec = {c["enseigne_id"] for c in json.loads(urlopen(_req("GET", "centres?select=enseigne_id"), timeout=60).read())}
    orph = sorted(ens - avec - actives)
    if orph:
        ids = ",".join(f'"{i}"' for i in orph)
        urlopen(_req("DELETE", f"enseignes?id=in.({up.quote(ids)})"), timeout=60)
        print(f"[supa] enseignes orphelines supprimées : {orph}")


def upsert(table: str, rows: list[dict], on_conflict: str) -> None:
    if not rows:
        return
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        req = Request(
            f"{URL}/rest/v1/{table}?on_conflict={on_conflict}",
            data=json.dumps(chunk).encode(), method="POST",
            headers={"apikey": KEY, "Authorization": "Bearer " + KEY,
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"})
        try:
            urlopen(req, timeout=60)
        except HTTPError as e:
            print(f"[supa] {table} chunk {i}: HTTP {e.code} {e.read()[:200].decode('utf-8','replace')}")
            raise
    print(f"[supa] {table}: {len(rows)} lignes upsert")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="n'envoie que les sessions modifiées depuis cet instant ISO")
    args = ap.parse_args()
    if not (URL and KEY):
        print("[supa] SUPABASE_URL / SUPABASE_KEY manquants — abandon."); return 1
    enseignes, centres, salles, sessions = {}, {}, {}, {}
    for f in glob.glob("escape_data/4escape_all/*.json"):
        st = read_json(f, {}) or {}
        eid = st.get("enseigne_id")
        if not eid:
            continue
        enseignes[eid] = {"id": eid, "nom": st.get("enseigne_nom", eid),
                          "website": st.get("website", "")}
        for cid, c in (st.get("centres") or {}).items():
            lat, lon = _coords(c)
            centres[cid] = {"id": cid, "enseigne_id": eid, "nom": c.get("nom"),
                            "cp": c.get("cp"), "ville": c.get("ville"),
                            "adresse": c.get("adresse"), "lat": lat, "lon": lon}
        rooms = st.get("rooms", {})
        default_cid = next(iter(st.get("centres") or {}), None)
        for rid, r in rooms.items():
            cid = r.get("centre_id") or default_cid
            if cid not in centres:
                cid = default_cid
            if cid is None:
                continue
            salles[rid] = {"id": rid, "centre_id": cid, "nom": r.get("name"),
                           "theme": r.get("theme"), "difficulty": r.get("difficulty"),
                           "duree_minutes": r.get("duration"),
                           "joueurs_min": r.get("min_players"), "joueurs_max": r.get("max_players")}
        for sess in (st.get("sessions") or {}).values():
            rid = sess.get("room_id")
            if rid not in salles:
                continue
            if args.since and (sess.get("maj") or "") < args.since:
                continue
            sessions[f"{rid}|{sess['date']}|{sess['heure']}"] = {
                "salle_id": rid, "date": sess["date"], "heure": sess["heure"],
                "duree_minutes": sess.get("duree_minutes"),
                "nb_joueurs_min": sess.get("nb_joueurs_min"), "nb_joueurs_max": sess.get("nb_joueurs_max"),
                "prix_total": sess.get("prix_total") or None,
                "prix_total_moyen": sess.get("prix_total_moyen"),
                "dispo": sess.get("dispo"), "booked": sess.get("booked"), "statut": sess.get("statut"),
                "premier_vu": sess.get("premier_vu"), "dernier_vu": sess.get("dernier_vu"),
                "releve": sess.get("releve"),
                # capacité + places vendues (sessions partagées ; None si salle privée)
                "prive": sess.get("prive"), "groupby": sess.get("groupby"),
                "places_max": sess.get("places_max"),
                "places_restantes": sess.get("places_restantes"),
                "places_init": sess.get("places_init"),
                "places_vendues": sess.get("places_vendues")}

    print(f"[supa] {len(enseignes)} enseignes · {len(centres)} centres · "
          f"{len(salles)} salles · {len(sessions)} sessions")
    upsert("enseignes", list(enseignes.values()), "id")
    upsert("centres", list(centres.values()), "id")
    upsert("salles", list(salles.values()), "id")
    upsert("sessions", list(sessions.values()), "salle_id,date,heure")
    if not args.since:
        purge_enseignes_orphelines(set(enseignes))
    print("[supa] sync terminé ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
