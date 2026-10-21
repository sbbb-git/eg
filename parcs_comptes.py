#!/usr/bin/env python3
"""parcs_comptes.py — le RÉTROSPECTIF des parcs enfants : comptes annuels publiés.

Les billetteries ne montrent que les créneaux à venir (vérifié : aucune date passée
n'est servie). Le seul historique public est celui des comptes déposés au greffe,
exposés par l'API ouverte recherche-entreprises.api.gouv.fr (champ « finances » :
chiffre d'affaires et résultat net par exercice).

Limite : beaucoup de petites sociétés déposent des comptes CONFIDENTIELS ; l'API
renvoie alors ca = 0. On le traduit en « non publié » (None), jamais en 0 €.

Sortie : parcs_comptes.json (publié avec le dashboard).
  python3 parcs_comptes.py
"""
from __future__ import annotations

import json
import time
import urllib.parse as up
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from safestore import read_json, write_json

OUT = "parcs_comptes.json"
API = "https://recherche-entreprises.api.gouv.fr/search?"

# Société d'exploitation de chaque parc suivi (SIREN), et sociétés têtes de réseau.
SOCIETES = [
    # Palomano — sociétés d'exploitation (une par parc) et franchiseur
    {"siren": "921549382", "enseigne": "palomano", "centre": "qw-palomano-clichy"},
    {"siren": "921547675", "enseigne": "palomano", "centre": "qw-palomano-rueil"},
    {"siren": "890922024", "enseigne": "palomano", "centre": None, "role": "société historique"},
    {"siren": "925031098", "enseigne": "palomano", "centre": None, "role": "franchiseur"},
    {"siren": "935352526", "enseigne": "palomano", "centre": "qw-palomano-caen"},
    {"siren": "939545919", "enseigne": "palomano", "centre": "qw-palomano-nantes-sud"},
    {"siren": "990129314", "enseigne": "palomano", "centre": "qw-palomano-saint-quentin"},
    {"siren": "937576817", "enseigne": "palomano", "centre": "qw-palomano-angers"},
    {"siren": "988060851", "enseigne": "palomano", "centre": "qw-palomano-bordeaux"},
    {"siren": "938092798", "enseigne": "palomano", "centre": "qw-palomano-toulouse"},
    {"siren": "990999690", "enseigne": "palomano", "centre": "qw-palomano-frejus"},
    {"siren": "942670209", "enseigne": "palomano", "centre": "qw-palomano-rouen"},
    # Concurrents
    {"siren": "932887888", "enseigne": "octopia", "centre": "qw-octopia"},
    {"siren": "993431964", "enseigne": "revopolie", "centre": "qw-revopolie-parc.qweekle.shop"},
    {"siren": "519143911", "enseigne": "gulli-parc", "centre": None, "role": "La Boîte aux Enfants (holding Gulli Parc)"},
    {"siren": "512888439", "enseigne": "les-zacrocs", "centre": "qw-leszacrocs", "role": "Acrochats (exploitant)"},
    {"siren": "941877193", "enseigne": "bulles-et-billes", "centre": "qw-bullesetbilles-paris9"},
    {"siren": "939769162", "enseigne": "toumini-city", "centre": "qw-toumini-city"},
    {"siren": "928142488", "enseigne": "quand-je-serai-grand", "centre": "qw-quandjeseraigrand"},
]


# Chiffres publiés par les réseaux eux-mêmes (non audités), utiles pour caler les estimations.
DECLARATIFS = [
    {"centre": "qw-palomano-clichy", "societe": "PALOMANO CLICHY", "annee": 2024, "ca": 1_120_000,
     "visiteurs": 111_369, "source": "Palomano, déclaratif non audité"},
]


def _get(siren: str) -> dict | None:
    url = API + up.urlencode({"q": siren, "per_page": 1})
    for i in range(6):
        try:
            d = json.loads(urlopen(Request(url, headers={"User-Agent": "observatoire-parcs"}), timeout=30).read())
            res = [r for r in d.get("results") or [] if r.get("siren") == siren]
            return res[0] if res else None
        except Exception:
            time.sleep(3 * (i + 1))
    return None


def main() -> int:
    prev = {s["siren"]: s for s in (read_json(OUT, {}) or {}).get("societes", [])}
    out, ko = [], 0
    for cfg in SOCIETES:
        r = _get(cfg["siren"])
        if r is None:                                  # API injoignable : on garde le relevé précédent
            ko += 1
            if cfg["siren"] in prev:
                out.append(prev[cfg["siren"]])
            continue
        siege = r.get("siege") or {}
        fin = {an: {"ca": (v.get("ca") or None), "resultat_net": v.get("resultat_net")}
               for an, v in sorted((r.get("finances") or {}).items())}
        out.append({**cfg, "nom": r.get("nom_complet"), "creation": r.get("date_creation"),
                    "ville": siege.get("libelle_commune"), "cp": siege.get("code_postal"),
                    "naf": r.get("activite_principale"), "effectif": r.get("tranche_effectif_salarie"),
                    "etat": r.get("etat_administratif"), "finances": fin})
        time.sleep(0.4)                                # API publique : 7 req/s max
    write_json(OUT, {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "source": "recherche-entreprises.api.gouv.fr (comptes déposés)", "societes": out,
                     "declaratifs": DECLARATIFS})
    print(f"[comptes] {len(out)} sociétés · {ko} injoignable(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
