#!/usr/bin/env python3
"""parcs_historique.py — le RÉTROSPECTIF réel des parcs sur ROLLER.

Contrairement aux autres billetteries (aucune date passée servie), l'API publique
ROLLER renvoie aussi les JOURS PASSÉS : pour chaque séance, les places restantes
FINALES. Comme ROLLER sert aussi de caisse aux parcs qui l'utilisent, ces chiffres
comprennent a priori les ventes sur place. L'historique remonte à la mise en service
du parc sur ROLLER (Palomano Clichy : juillet 2026 pour les entrées, mai 2026 pour
les anniversaires ; Palomano Alfortville : mars 2026 ; You Kids : juillet 2026).

Pour chaque parc et chaque jour passé :
  e  entrées vendues          (jauge - places restantes, présences converties en entrées)
  j  jauge du jour, en entrées
  ca CA billetterie estimé    (entrées × moyenne des prix enfant / adulte)
  c  créneaux complets        n  créneaux ouverts
  a  anniversaires réservés (si le parc les vend sur ROLLER)

Incrémental : seuls les jours absents (et les 3 derniers, encore mouvants) sont relus.
Sortie : parcs_historique.json, publié avec le dashboard.

  python3 parcs_historique.py [--depuis 2026-01-01]
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import escape_parcs as P
from escape_4escape_all import paris_now
from safestore import read_json, write_json

OUT = "parcs_historique.json"
DEBUT_MIN = date(2023, 1, 1)


class Roller:
    """Accès à l'API checkout ROLLER d'un lieu (clé, slug, cellule)."""

    def __init__(self, booking_url: str):
        import urllib.parse as up
        self.c = P.Client()
        u = up.urlparse(booking_url)
        segs = [x for x in u.path.split("/") if x]
        if u.netloc == "ecom.roller.app" and len(segs) >= 2:
            self.venue, self.slug = segs[0], segs[1]
        else:
            self.c.get(booking_url)
            self.venue = next((ck.value for ck in self.c.jar if ck.name == "Current-Venue"), None)
            self.slug = segs[0] if segs else None
        if not (self.venue and self.slug):
            raise IOError("clé de lieu ROLLER introuvable")
        self.H = {"Accept": "application/json", "X-Api-Key": self.venue, "X-Checkout-Slug": self.slug}
        self.c.get(f"https://api.roller.app/api/checkout/{self.slug}/availability?days=1", headers=self.H)
        self.cell = next((ck.value for ck in self.c.jar if ck.name == "Cell"), None)
        if self.cell:
            self.H["X-Cell-Id"] = self.cell

    def jour(self, d: date) -> list:
        url = f"https://api.roller.app/api/checkout/{self.slug}/product-availability?date={d.isoformat()}"
        if self.cell:
            url += f"&Cell={self.cell}"
        for essai in range(3):
            s, b = self.c.get(url, headers=self.H)
            if s == 200:
                return json.loads(b)
            time.sleep(3 * (essai + 1))
        raise IOError(f"{d} HTTP {s}")


def _entree(prods: list, produit: str | None):
    if produit:                                       # produit au catalogue mais sans séance ce jour : None
        return next((p for p in prods if produit in (str(p.get("id")), str(p.get("parentProductId")))
                     and p.get("sessions")), None)
    cands = [p for p in prods if p.get("type") in ("sessionpass", "session") and p.get("sessions")
             and not P.ENTREE_EXCLUE.search(p.get("name") or "")]
    return max(cands, key=lambda p: len(p["sessions"]), default=None)


def _anniversaires(prods: list, motif: str = "") -> int | None:
    """Anniversaires réservés dans la journée (estimation). Les formules (« partypackage »)
    se réservent sur des créneaux fixes et occupent la salle d'anniversaire ; un créneau
    est compté réservé quand AUCUNE formule n'y est plus réservable (salle prise).
    Palomano Clichy : 9 créneaux par jour, une salle."""
    import re
    fetes = [p for p in prods if p.get("type") == "partypackage" and p.get("sessions")
             and re.search(motif, p.get("name") or "", re.I)]
    if not fetes:
        return None
    libre: dict[str, int] = {}
    for p in fetes:
        for s in p["sessions"]:
            h = s["startTime"][:5]
            r = s.get("resourceCapacityRemaining")
            r = s.get("capacityRemaining") if r is None else r
            libre[h] = max(libre.get(h, 0), int(r or 0))
    return sum(1 for v in libre.values() if v <= 0)


def releve_jour(R: Roller, d: date, produit: str | None, jauge: int | None, presence: bool,
                anniv: str | None = None) -> dict | None:
    prods = R.jour(d)
    p = _entree(prods, produit)
    ann = _anniversaires(prods, anniv) if anniv is not None else None
    if not p or not p.get("sessions"):
        return {"a": ann} if ann else None
    ss = [s for s in p["sessions"] if s.get("startTime")]
    variantes = [(v["id"], v.get("name") or "") for v in p.get("products") or []]
    enf, adu = P.choisir_entrees(variantes)
    cout = {v["id"]: v.get("cost") for v in p.get("products") or []}
    pe, pa = cout.get(enf), cout.get(adu)
    panier = (pe + pa) / 2 if pe and pa else (pe or pa or 0)
    rest = [int(s.get("capacityRemaining") or 0) for s in ss]
    jg = jauge or max(max(rest), 1)
    f = 1.0
    if presence:
        heures = sorted(int(s["startTime"][:2]) * 60 + int(s["startTime"][3:5]) for s in ss)
        pas = Counter(b - a for a, b in zip(heures, heures[1:]) if b > a).most_common(1)
        h1, m1 = map(int, ss[0]["startTime"][:5].split(":"))
        h2, m2 = map(int, (ss[0].get("endTime") or "00:00")[:5].split(":"))
        duree = (h2 * 60 + m2) - (h1 * 60 + m1) or 120
        f = min(1.0, (pas[0][0] if pas else 30) / duree)
    # places restantes négatives = surréservation : vendues au-delà de la jauge
    e = sum(max(0, jg - r) for r in rest) * f
    out = {"e": round(e), "j": round(jg * len(ss) * f), "ca": round(e * panier),
           "c": sum(1 for r in rest if r <= 0), "n": len(ss), "pe": pe, "pa": pa}
    if ann is not None:
        out["a"] = ann
    return out


def premier_jour(R: Roller, produit: str | None, anniv: str | None) -> date | None:
    """Premier jour avec des séances : on recule de 4 semaines en 4 semaines tant que le
    parc est actif (3 jours consécutifs sondés, pour ne pas buter sur un jour de
    fermeture), puis on avance jour par jour dans le dernier intervalle."""
    def seances(d: date) -> bool:
        prods = R.jour(d)
        return bool(_entree(prods, produit) or (anniv is not None and _anniversaires(prods, anniv)))

    def actif(d: date) -> bool:
        return any(seances(d + timedelta(days=k)) for k in range(3))

    d = paris_now().date() - timedelta(days=3)
    if not actif(d):
        return None
    while d - timedelta(days=28) > DEBUT_MIN and actif(d - timedelta(days=28)):
        d -= timedelta(days=28)
    x = max(DEBUT_MIN, d - timedelta(days=28))
    while x <= d:
        if seances(x):
            return x
        x += timedelta(days=1)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depuis", help="ne pas remonter avant cette date (AAAA-MM-JJ)")
    ap.add_argument("--refaire", action="store_true", help="relire tous les jours (calcul modifié)")
    args = ap.parse_args()
    hist = read_json(OUT, {}) or {}
    parcs = hist.setdefault("parcs", {})
    today = paris_now().date()
    for eid, cfg in P.ENSEIGNES.items():
        for parc, info in cfg["parcs"].items():
            if info.get("kind", cfg.get("kind")) != "roller":
                continue
            cid = f"qw-{parc}"
            h = parcs.setdefault(cid, {"jours": {}})
            h.update({"nom": info["nom"], "enseigne": eid, "source": info["booking"]})
            presence = info.get("comptage", cfg.get("comptage")) == "presence"
            jauge = h.get("jauge")
            try:
                R = Roller(info["booking"])
                anniv = info.get("anniversaires")           # motif des formules anniversaire du parc
                if not h.get("debut"):
                    d0 = premier_jour(R, info.get("produit"), anniv)
                    h["debut"] = d0.isoformat() if d0 else None
                if not h.get("debut"):
                    continue
                d = max(date.fromisoformat(h["debut"]),
                        date.fromisoformat(args.depuis) if args.depuis else DEBUT_MIN)
                if not jauge:
                    # jauge habituelle : valeur la plus fréquente des places restantes sur
                    # les 30 prochains jours (presque vides) — 30 à Clichy, 150 à Alfortville
                    vals = Counter()
                    for k in range(14, 44, 3):
                        p = _entree(R.jour(today + timedelta(days=k)), info.get("produit"))
                        vals.update(int(s.get("capacityRemaining") or 0) for s in (p or {}).get("sessions") or [])
                    jauge = h["jauge"] = vals.most_common(1)[0][0] if vals else None
                n = 0
                while d < today:
                    k = d.isoformat()
                    if args.refaire or k not in h["jours"] or (today - d).days <= 3:
                        r = releve_jour(R, d, info.get("produit"), jauge, presence, anniv)
                        if r:
                            h["jours"][k] = r
                        n += 1
                        time.sleep(0.3)
                    d += timedelta(days=1)
                h["maj"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                print(f"[historique] {info['nom']} : depuis {h['debut']}, {len(h['jours'])} jours ({n} relus), "
                      f"jauge {jauge}, {R.c.n} requêtes")
            except Exception as e:
                print(f"[historique] ⚠ {info['nom']} : {e}")
            write_json(OUT, hist)
    hist["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hist["source"] = "API publique ROLLER (séances passées, places restantes finales)"
    write_json(OUT, hist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
