#!/usr/bin/env python3
"""parcs_comptes.py — le RÉTROSPECTIF financier des parcs enfants : comptes publiés.

Les billetteries ne montrent que l'avenir (sauf ROLLER, voir parcs_historique.py).
L'historique financier public, ce sont les comptes déposés au greffe :
  - recherche-entreprises.api.gouv.fr : dernier exercice (CA, résultat net) ;
  - data.economie.gouv.fr, jeu « ratios_inpi_bce » : TOUS les exercices déposés
    (CA, résultat net, EBE…).
Beaucoup de petites sociétés déposent des comptes CONFIDENTIELS : CA = None
(« non publié »), jamais 0 €.

Entrée : parcs_societes.json — sociétés exploitant chaque parc (rattachées par
adresse d'établissement), têtes de réseau, holdings. Recensement du 21/10/2026.
Sortie : parcs_comptes.json (publié avec le dashboard), chaque société reliée aux
centres suivis (centres) quand le parc est collecté.

  python3 parcs_comptes.py
"""
from __future__ import annotations

import json
import time
import unicodedata
import urllib.parse as up
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import escape_parcs as P
from safestore import read_json, write_json

OUT = "parcs_comptes.json"
SOCIETES = "parcs_societes.json"
API = "https://recherche-entreprises.api.gouv.fr/search?"
INPI = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/ratios_inpi_bce/records?"

# Chiffres publiés par les réseaux eux-mêmes (non audités), utiles pour caler les estimations.
DECLARATIFS = [
    {"centre": "qw-palomano-clichy", "societe": "PALOMANO CLICHY", "annee": 2024, "ca": 1_120_000,
     "visiteurs": 111_369, "source": "Palomano, déclaratif non audité"},
]


ECHECS: dict[str, int] = {}                          # échecs consécutifs par hôte


def _json(url: str):
    """GET JSON avec nouvelles tentatives ; un hôte qui échoue 3 fois de suite n'est plus
    interrogé pendant ce passage (les dernières valeurs connues sont gardées)."""
    hote = up.urlparse(url).netloc
    if ECHECS.get(hote, 0) >= 3:
        return None
    for i in range(4):
        try:
            d = json.loads(urlopen(Request(url, headers={"User-Agent": "observatoire-parcs"}), timeout=40).read())
            ECHECS[hote] = 0
            return d
        except Exception:
            time.sleep(2 * (i + 1))
    ECHECS[hote] = ECHECS.get(hote, 0) + 1
    return None


def _cle(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


VIDES = {"le", "la", "les", "de", "des", "du", "d", "l", "parc", "the", "et"}


def _mots(s: str) -> set:
    return {m for m in _cle(s).split() if m not in VIDES and len(m) > 2}


def centres_suivis() -> list[tuple[str, set, str]]:
    """[(centre_id, mots du nom d'enseigne, ville normalisée)] des parcs collectés."""
    return [(f"qw-{parc}", _mots(cfg["nom"]), _cle(info["ville"]))
            for cfg in P.ENSEIGNES.values() for parc, info in cfg["parcs"].items()]


def rattacher(ent: dict, suivis: list) -> list[str]:
    """Centres suivis exploités par la société : même ville (normalisée) ET un mot de
    l'enseigne dans le nom du parc exploité (ex. « gulli », « palomano », « ayden »)."""
    ids = []
    for pe in ent.get("parcs_exploites") or []:
        v, nom = _cle(pe.get("ville") or ""), _mots(pe.get("park") or "")
        for cid, marque, pv in suivis:
            if v and (v == pv or v.startswith(pv + " ") or pv.startswith(v + " ")) and marque & nom \
                    and cid not in ids:
                ids.append(cid)
    return ids


def finances(siren: str, connues: dict) -> tuple[dict, str]:
    """Exercices connus + ceux du jeu INPI (tous) + de l'API (dernier). Clé = date de clôture
    (ou année). CA confidentiel -> None."""
    fin = {k: dict(v) for k, v in (connues or {}).items()}
    src = []
    d = _json(INPI + up.urlencode({"where": f'siren="{siren}"', "limit": 50}))
    if d and d.get("results") is not None:
        src.append("INPI")
        for r in d["results"]:
            k = str(r.get("date_cloture_exercice") or "")[:10]
            if not k:
                continue
            ca = r.get("chiffre_d_affaires")
            fin[k] = {**fin.get(k, {}), "ca": ca if ca else None, "resultat_net": r.get("resultat_net"),
                      **({"ebe": r["ebe"]} if r.get("ebe") is not None else {})}
    d = _json(API + up.urlencode({"q": siren, "per_page": 1}))
    if d:
        src.append("registre")
        for r in d.get("results") or []:
            if r.get("siren") != siren:
                continue
            for an, v in (r.get("finances") or {}).items():
                if not any(k.startswith(an) for k in fin):    # année absente du jeu INPI
                    fin[an] = {"ca": v.get("ca") or None, "resultat_net": v.get("resultat_net")}
    for k in [k for k in fin if len(k) == 4 and any(x.startswith(k + "-") for x in fin)]:
        fin.pop(k)                                         # « 2025 » (registre) doublon de « 2025-12-31 »
    return dict(sorted(fin.items())), "+".join(src)


def main() -> int:
    base = read_json(SOCIETES, {}) or {}
    prev = {s["siren"]: s for s in (read_json(OUT, {}) or {}).get("societes", [])}
    suivis = centres_suivis()
    out, ko = [], 0
    for e in base.get("entreprises", []):
        siren = e["siren"]
        connues = {**(prev.get(siren, {}).get("finances") or {}), **(e.get("finances") or {})}
        fin, src = finances(siren, connues)
        ko += not src
        eff = e.get("effectif_tranche")
        out.append({"siren": siren, "nom": e.get("nom"), "groupe": e.get("groupe_marque"), "role": e.get("role"),
                    "parcs": [p.get("park") for p in e.get("parcs_exploites") or []],
                    "centres": rattacher(e, suivis), "creation": e.get("date_creation"), "etat": e.get("etat"),
                    "effectif": eff.get("libelle") if isinstance(eff, dict) else eff,
                    "confiance": e.get("confidence"), "finances": fin})
        time.sleep(0.3)
    write_json(OUT, {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "source": "comptes déposés (data.economie.gouv.fr ratios_inpi_bce, recherche-entreprises)",
                     "societes": out, "declaratifs": DECLARATIFS})
    print(f"[comptes] {len(out)} sociétés · {sum(1 for s in out if s['centres'])} rattachées à un parc suivi · "
          f"{ko} sans réponse des API (dernières valeurs gardées)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
