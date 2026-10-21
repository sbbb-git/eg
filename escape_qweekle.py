#!/usr/bin/env python3
"""escape_qweekle.py — parcs de loisirs enfants réservés via Qweekle.

Enseignes suivies (demande : Palomano, Octopia, Rêvopolie) :
  - Palomano (17 parcs) et Octopia : ANCIENNE boutique Qweekle
    (<org>.qweekle.com/shop/<parc>). Elle ne publie pas le nombre de places :
    elle liste seulement les créneaux réservables pour N participants. On
    retrouve les places restantes EXACTES de chaque créneau par dichotomie sur N
    (une requête = « quels créneaux acceptent encore N personnes ? », partagée
    par tous les créneaux de la page).
  - Rêvopolie : NOUVELLE boutique Qweekle (eshop-v3, *.qweekle.shop) avec une
    API JSON qui donne directement les places libres et la jauge du créneau.

Toutes les entrées d'un parc (enfant, adulte, bébé…) puisent dans la même jauge
par créneau (vérifié : 15 enfants + 15 adultes = 30 enfants). On mesure donc UNE
jauge par parc, via l'entrée enfant, et on relève au passage les prix enfant et
adulte de chaque créneau (heures creuses comprises).

Store : escape_data/4escape_all/qweekle__<enseigne>.json, même format que les
enseignes 4escape (centres / rooms / sessions), poussé par supa_sync.py. Les
parcs sont marqués salle.theme = "parc" et session.groupby = "parc" : le
dashboard les sépare des escape games (onglet « Parcs enfants »).

Mesures par créneau :
  places_restantes : places encore réservables (calcul de la billetterie, qui
                     tient compte de la jauge du parc sur toute la visite) ;
  places_init      : capacité de référence = jauge publiée (eshop-v3) ou plus
                     haut niveau de places libres jamais observé (ancienne boutique) ;
  places_vendues   : places_init - places_restantes (taux de remplissage).

  python3 escape_qweekle.py [--days 14] [--only palomano]
"""
from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import re
import time
import urllib.parse as up
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from escape_4escape_all import OBS_DIR, norm_ville, now_iso, paris_now
from safestore import read_json, write_json

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
HORIZON_JOURS = 14
GEL_MIN = 90          # un créneau qui disparaît < 90 min avant son début : fin de vente, pas complet
WORKERS = 3           # parcs en parallèle sur un même hôte (politesse)


def _p(adresse: str, cp: str, ville: str, nom: str) -> dict:
    return {"nom": nom, "adresse": adresse, "cp": cp, "ville": norm_ville(ville)}


ENSEIGNES = {
    "palomano": {
        "nom": "Palomano", "website": "https://www.palomano.com", "kind": "legacy", "org": "palomano",
        "parcs": {
            "palomano-aix-en-provence": _p("45 boulevard Aristide Briand", "13100", "Aix-en-Provence", "Palomano Aix-en-Provence"),
            "palomano-angers": _p("21 rue du Hanipet", "49124", "Saint-Barthélemy-d'Anjou", "Palomano Angers"),
            "palomano-bordeaux": _p("Cours du Médoc", "33300", "Bordeaux", "Palomano Bordeaux"),
            "palomano-caen": _p("10 rue des Hauts de Beaulieu", "14000", "Caen", "Palomano Caen"),
            "palomano-clermont": _p("12 rue de l'Éminée", "63000", "Clermont-Ferrand", "Palomano Clermont-Ferrand"),
            "palomano-frejus": _p("10 Les Barestes", "83480", "Puget-sur-Argens", "Palomano Fréjus"),
            "palomano-grasse": _p("129 route de la Paoute", "06130", "Grasse", "Palomano Grasse"),
            "palomano-la-maillerie": _p("123 avenue le Nôtre", "59650", "Villeneuve-d'Ascq", "Palomano La Maillerie"),
            "palomano-metz": _p("4 allée des Tilleuls", "57130", "Jouy-aux-Arches", "Palomano Metz"),
            "palomano-montrouge": _p("44 avenue Aristide Briand", "92120", "Montrouge", "Palomano Montrouge"),
            "palomano-nantes-sud": _p("83 route de la Gare", "44120", "Vertou", "Palomano Nantes Sud"),
            "palomano-rouen": _p("94 rue des Martyrs de la Résistance", "76150", "Maromme", "Palomano Rouen"),
            "palomano-rueil": _p("4 bis rue de l'Arsenal", "92500", "Rueil-Malmaison", "Palomano Story Rueil"),
            "palomano-saint-quentin": _p("5 bis rue de la Petite Vallée", "02100", "Fayet", "Palomano Saint-Quentin"),
            "palomano-toulon": _p("375 avenue Saint-Just", "83130", "La Garde", "Palomano Toulon"),
            "palomano-toulouse": _p("334 avenue de Grande-Bretagne", "31300", "Toulouse", "Palomano Toulouse"),
            "palomano-val-deurope": _p("Parc commercial Clos du Chêne", "77600", "Chanteloup-en-Brie", "Palomano Val d'Europe"),
        },
    },
    "octopia": {
        "nom": "Octopia", "website": "https://octopia.qweekle.com/shop/octopia", "kind": "legacy", "org": "octopia",
        "parcs": {"octopia": _p("5 rue Émile Allez", "75017", "Paris", "Octopia")},
    },
    "revopolie": {
        "nom": "Rêvopolie", "website": "https://revopolie-parc.qweekle.shop", "kind": "v3",
        "parcs": {"revopolie-parc.qweekle.shop": _p("25 rue Desnouettes", "75015", "Paris", "Rêvopolie")},
    },
}

# Choix des produits : la catégorie « entrées famille » du parc et, dedans, l'entrée
# enfant standard (les variantes restreintes — after school, tout-petits, CSE… —
# n'ont pas tous les créneaux).
CAT_EXCLUE = re.compile(r"assistante|ecole|groupe|anniv|privat|atelier|bouh|ween|boum|boom|spectacle|"
                        r"sharks|marionnette|giftcard|ticketing|member|booking|cart|general", re.I)
CAT_PARC = re.compile(r"acces-parc|entrees?-au-parc|viens-jouer|billets-parc|entree", re.I)
ENTREE_EXCLUE = re.compile(r"after|b[ée]b[ée]|bout.?chou|1 (an )?à 2 ans|12 à 2[34] mois|pichoun|minot|bambin|"
                           r"handicap|pmr|cse|pr[ée]pay|papi|palo-?pass|mini|p.tit|assist|suppl|abonn|scolaire|"
                           r"privat|atelier|anniv|gratuit|groupe|encas", re.I)


def choisir_entrees(produits: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """(id entrée enfant, id entrée adulte) parmi [(id, libellé)]."""
    ok = [(i, n) for i, n in produits if not ENTREE_EXCLUE.search(n)]
    enfant = next((i for i, n in ok if re.search(r"enfant|junior|acc[eè]s parc", n, re.I)), None)
    adulte = next((i for i, n in ok if re.search(r"adulte", n, re.I)), None)
    if enfant is None:
        enfant = next((i for i, n in ok if i != adulte), None) or (produits[0][0] if produits else None)
    return enfant, adulte


def _num(t: str) -> float | None:
    """« 1.664.00 » / « 15,80 » / « 1 664,00 » -> float."""
    t = t.replace("\xa0", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif t.count(".") > 1:
        a, b = t.rsplit(".", 1)
        t = a.replace(".", "") + "." + b
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


class Client:
    def __init__(self) -> None:
        self.op = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.n = 0

    def get(self, url: str, headers: dict | None = None, tries: int = 4) -> tuple[int, str]:
        """(statut HTTP, corps). Statut 0 = échec réseau après `tries` tentatives."""
        for i in range(tries):
            self.n += 1
            try:
                r = self.op.open(Request(url, headers={"User-Agent": UA, "Accept-Language": "fr",
                                                       **(headers or {})}), timeout=45)
                return r.status, r.read().decode("utf-8", "replace")
            except HTTPError as e:
                if e.code < 500:
                    return e.code, ""
            except Exception:
                pass
            time.sleep(2 * (i + 1))
        return 0, ""


# ───────────────────────────── ancienne boutique ─────────────────────────────
class Legacy:
    """Boutique <org>.qweekle.com/shop/<parc> : page « multi-activités » d'une catégorie."""

    def __init__(self, org: str, parc: str, cat: str | None) -> None:
        self.base = f"https://{org}.qweekle.com/shop/{parc}/"
        self.c = Client()
        self.cat = cat
        self.tok = None
        self.acts: list[tuple[str, str]] = []

    def _charge_cat(self, cat: str) -> bool:
        st, h = self.c.get(self.base + cat + "?lang=fr")
        if st != 200 or "multiActivity" not in h:
            return False
        tok = re.search(r'name="_token" type="hidden" value="([^"]+)"', h)
        acts = list(zip(re.findall(r'name="activities_qty\[([^\]]+)\]"', h),
                        [html.unescape(x).strip() for x in re.findall(r"<h3>([^<]+)</h3>", h)]))
        if not (tok and acts):
            return False
        self.cat, self.tok, self.acts = cat, tok.group(1), acts
        return True

    def ouvrir(self) -> bool:
        """Charge la catégorie connue, sinon la (re)détecte depuis la page « booking »."""
        if self.cat and self._charge_cat(self.cat):
            return True
        st, h = self.c.get(self.base + "booking?lang=fr")
        if st != 200:
            return False
        cats = []
        for c in re.findall(re.escape(self.base) + r"([a-z0-9\-]+)\?lang=fr", h):
            if c not in cats and not CAT_EXCLUE.search(c) and CAT_PARC.search(c):
                cats.append(c)
        cats.sort(key=lambda c: (0 if "2h" in c else 1))        # Saint-Quentin : 1 h et 2 h
        return any(self._charge_cat(c) for c in cats)

    def page(self, jour: date, act: str, qty: int):
        """Créneaux réservables pour `qty` × `act` à partir de `jour`.
        -> ({'YYYY-MM-DD HH:MM': prix}, prochaine_dispo | None), ou None si la réponse est invalide."""
        q = [("_token", self.tok), ("start_at", jour.strftime("%d/%m/%Y"))]
        q += [(f"activities_qty[{a}]", str(qty) if a == act else "0") for a, _ in self.acts]
        st, d = self.c.get(self.base + f"multi/{self.cat}/data?lang=fr&" + up.urlencode(q),
                           headers={"X-Requested-With": "XMLHttpRequest"})
        if st != 200 or not ("activity-slots" in d or "no-slots-available" in d or "update_date" in d):
            return None
        d = re.sub(r"\s+", " ", d)
        slots = {}
        for dt, inner in re.findall(r'data-slot_url="[^"]*/(\d{4}-\d\d-\d\d_\d\d:\d\d)/[^"]*"[^>]*>(.*?)</a>', d):
            pm = re.search(r"([\d.,\s]+)\s*&euro;", inner)
            slots[dt.replace("_", " ")] = _num(pm.group(1)) if pm else None
        nxt = None
        m = re.search(r'no-slots-available.*?data-update_date="(\d\d/\d\d/\d{4})"', d)
        if m:
            nxt = datetime.strptime(m.group(1), "%d/%m/%Y").date()
        return slots, nxt

    def restantes(self, jour: date, act: str, base: dict, prev: dict, leger: bool = False) -> dict:
        """Places restantes exactes de chaque créneau de `base` (créneaux réservables pour 1),
        par dichotomie partagée : on n'interroge que les seuils utiles. `prev` (relevé
        précédent) sert à borner la recherche dès le départ.
        Passage `leger` : un créneau qui accepte encore autant de personnes qu'au relevé
        précédent est réputé inchangé (une requête par valeur distincte) ; seules les
        baisses (réservations) sont re-mesurées. Les hausses (annulations, jauge élargie)
        sont rattrapées au passage complet suivant."""
        cache: dict[int, set] = {1: set(base)}

        def Q(n: int):
            if n not in cache:
                r = self.page(jour, act, n)
                if r is None:
                    raise IOError("réponse invalide")
                cache[n] = set(r[0]) & set(base)
            return cache[n]

        res: dict[str, int] = {}
        indices = prev
        if leger:
            for v in sorted({prev[k] for k in base if prev.get(k)}):
                grp = {k for k in base if prev.get(k) == v}
                res.update({k: v for k in Q(v) & grp})
            base = {k: base[k] for k in base if k not in res}     # baisses + nouveaux
            if not base:
                return res
            indices = {}                       # valeurs précédentes déjà réfutées : bissection simple
        hi = max(32, 2 * max((prev.get(k) or 0 for k in base), default=0))
        while Q(hi) and hi < 4096:
            hi *= 2

        def solve(lo: int, hi: int, sl: set) -> None:     # toutes les valeurs dans [lo, hi[
            if not sl:
                return
            if hi - lo <= 1:
                for x in sl:
                    res[x] = lo
                return
            # Seuils candidats : v et v+1 pour chaque valeur précédente v (souvent
            # inchangée : Q(v) puis Q(v+1) la confirment en 2 requêtes partagées).
            pv = sorted({t for k in sl if indices.get(k) for t in (indices[k], indices[k] + 1) if lo < t < hi})
            mid = pv[len(pv) // 2] if pv else (lo + hi) // 2
            P = Q(mid) & sl
            solve(mid, hi, P)
            solve(lo, mid, sl - P)

        solve(1, hi, set(base))
        return res


def releve_legacy(org: str, parc: str, st: dict, jours: int) -> dict:
    """-> {"slots": {'YYYY-MM-DD HH:MM': {...}}, "couverts": set(dates), "cat":…, "req": n}"""
    meta = st.setdefault("_meta", {}).setdefault("parcs", {}).setdefault(parc, {})
    L = Legacy(org, parc, meta.get("cat"))
    if not L.ouvrir():
        raise IOError("catégorie « entrées » introuvable")
    enfant, adulte = choisir_entrees(L.acts)
    rid = f"qw-{parc}-parc"
    meta["passes"] = meta.get("passes", 0) + 1
    complet = meta["passes"] % 4 == 1          # 1 passage complet sur 4, les autres légers
    prev = {f"{s['date']} {s['heure']}": s.get("places_restantes")
            for s in st.get("sessions", {}).values() if s.get("room_id") == rid}
    today = paris_now().date()
    fin = today + timedelta(days=jours - 1)
    jour, out, couverts = today, {}, set()
    while jour <= fin:
        r = L.page(jour, enfant, 1)
        if r is None:
            raise IOError(f"réponse invalide ({jour})")
        base, nxt = r
        base = {k: v for k, v in base.items() if k[:10] <= fin.isoformat()}
        if not base:
            # rien de réservable entre `jour` et la prochaine disponibilité : jours couverts, 0 place
            stop = min(nxt - timedelta(days=1), fin) if nxt else fin
            d = jour
            while d <= stop:
                couverts.add(d.isoformat())
                d += timedelta(days=1)
            if not nxt or nxt <= jour:
                break
            jour = nxt
            continue
        dates = sorted({k[:10] for k in base})
        d = jour
        while d.isoformat() <= dates[-1]:
            couverts.add(d.isoformat())
            d += timedelta(days=1)
        rest = L.restantes(jour, enfant, base, prev, leger=not complet)
        px_ad = {}
        if adulte and complet:                   # tarif adulte : relu aux passages complets
            ra = L.page(jour, adulte, 1)
            px_ad = ra[0] if ra else {}
        for k, px in base.items():
            out[k] = {"rest": rest.get(k, 0), "enfant": px, "adulte": px_ad.get(k)}
        jour = date.fromisoformat(dates[-1]) + timedelta(days=1)
    meta.update({"cat": L.cat, "entree": dict(L.acts).get(enfant), "requetes": L.c.n,
                 "mode": "complet" if complet else "leger"})
    return {"slots": out, "couverts": couverts, "source": L.base + L.cat, "req": L.c.n,
            "duree": _duree(dict(L.acts).get(enfant) or "")}


def _duree(nom: str) -> int | None:
    m = re.search(r"(\d+)\s*h", nom)
    return int(m.group(1)) * 60 if m else None


# ───────────────────────────── eshop-v3 (JSON) ─────────────────────────────
def releve_v3(host: str, st: dict, jours: int) -> dict:
    c = Client()
    H = {"Accept": "application/json", "X-Venture-Domain": host}

    def J(path: str):
        s, b = c.get(f"https://{host}{path}", headers=H)
        if s != 200:
            raise IOError(f"{path.split('?')[0]} HTTP {s}")
        d = json.loads(b)
        if not d.get("success", True):
            raise IOError(f"{path.split('?')[0]} : {str(d)[:80]}")
        return d

    prods, page = [], 1
    while page <= 10:
        d = J(f"/api/products?locale=fr&page={page}&limit=100")
        prods += d.get("data") or []
        if not d.get("hasMore"):
            break
        page += 1
    cand = [(p["id"], p.get("name") or "") for p in prods if p.get("type") == "A"]
    enfant, adulte = choisir_entrees(cand)
    if not enfant:
        raise IOError("aucune entrée enfant")
    det = J(f"/api/products/{enfant}?locale=fr")["data"]
    act = det["activity"]["id"]
    duree = det["activity"].get("duration")
    act_ad = J(f"/api/products/{adulte}?locale=fr")["data"]["activity"]["id"] if adulte else None
    today = paris_now().date()
    fin = today + timedelta(days=jours - 1)
    days = J(f"/api/bookings/days?productId={enfant}&participants=1&from={today}&to={fin}&includeFull=1")["data"]
    out, couverts = {}, set()
    for dd in days:
        if not dd.get("totalSlotsAll"):
            couverts.add(dd["date"])
            continue
        cap = round(dd["totalCapacity"] / dd["totalSlotsAll"]) if dd.get("totalCapacity") else None
        q = "participants=1&includeFull=1&includeSuspended=1&date=" + dd["date"]
        sl = J(f"/api/bookings/availabilities?productId={enfant}&activityId={act}&{q}")["data"]
        ad = {}
        if act_ad:
            ad = {s["startAt"]: s.get("price") for s in
                  J(f"/api/bookings/availabilities?productId={adulte}&activityId={act_ad}&{q}")["data"]}
        couverts.add(dd["date"])
        for s in sl:
            k = s["startAt"][:16].replace("T", " ")
            if s.get("status") == "suspended":
                continue                                   # fermé par le parc : retiré
            pa = ad.get(s["startAt"])
            out[k] = {"rest": max(0, int(s.get("available") or 0)), "cap": cap,
                      "reservable": bool(s.get("bookable")),
                      "enfant": s["price"] / 100 if s.get("price") is not None else None,
                      "adulte": pa / 100 if pa is not None else None}
    return {"slots": out, "couverts": couverts, "source": f"https://{host}", "req": c.n, "duree": duree}


# ───────────────────────────── fusion dans le store ─────────────────────────────
def jauge_habituelle(S: dict, rid: str, pnow_s: str) -> int | None:
    """Jauge habituelle d'un créneau du parc (ancienne boutique, qui ne la publie pas) :
    la valeur la plus fréquente parmi les plus hauts niveaux de places libres observés
    sur les créneaux à venir — un créneau loin dans le futur est presque vide. Ex. :
    Octopia 70, Palomano Montrouge 30. Retenue si elle concerne ≥ 25 % des créneaux."""
    vals = [ex["places_max_vu"] for ex in S.values()
            if ex.get("room_id") == rid and ex.get("places_max_vu")
            and f"{ex['date']} {ex['heure']}" > pnow_s]
    if len(vals) < 20:
        return None
    v, n = Counter(vals).most_common(1)[0]
    return v if n >= 0.25 * len(vals) else None


def fusion(st: dict, rid: str, obs: dict, legacy: bool) -> int:
    """Met à jour les sessions du parc `rid` à partir d'un relevé.
    - créneau commencé : figé (statut final reserve / libre_fin) ;
    - ancienne boutique : un créneau connu qui disparaît (jour couvert, début > 90 min)
      n'a plus de place -> 0 restante, complet ;
    - eshop-v3 : la jauge est publiée -> un créneau qui disparaît a été retiré.
    Capacité de référence (places_init) : jauge publiée (eshop-v3) ; sinon le plus haut
    entre les places libres jamais vues sur ce créneau et la jauge habituelle du parc
    (certains créneaux ont une jauge plus large, ex. matinées de vacances)."""
    now, pnow = now_iso(), paris_now()
    seuil = (pnow + timedelta(minutes=GEL_MIN)).strftime("%Y-%m-%d %H:%M")
    pnow_s = pnow.strftime("%Y-%m-%d %H:%M")
    S = st.setdefault("sessions", {})
    n = 0
    for k, o in obs["slots"].items():
        if k <= pnow_s:
            continue
        key = f"{rid}|{k[:10]}|{k[11:]}"
        ex = S.get(key) or {"room_id": rid, "date": k[:10], "heure": k[11:], "premier_vu": now,
                            "statut": None, "prive": False, "groupby": "parc", "nb_joueurs_min": 1}
        rest = o["rest"]
        prix = {k2: o[k2] for k2 in ("enfant", "adulte") if o.get(k2) is not None}
        reservable = o.get("reservable", rest > 0)
        ex.update({"duree_minutes": obs.get("duree"), "places_restantes": rest,
                   "places_max_vu": max(ex.get("places_max_vu") or 0, rest),
                   "jauge_publiee": o.get("cap"),
                   "prix_total": {**(ex.get("prix_total") or {}), **prix} or None,
                   "prix_total_moyen": prix.get("enfant", (ex.get("prix_total") or {}).get("enfant")),
                   "dispo": reservable, "booked": not reservable, "dernier_vu": now, "releve": now})
        if ex.get("statut") == "retire":
            ex["statut"] = None
        S[key] = ex
        n += 1
    jauge = jauge_habituelle(S, rid, pnow_s) if legacy else None
    for key, ex in S.items():
        if ex.get("room_id") != rid:
            continue
        k = f"{ex['date']} {ex['heure']}"
        avant = (ex.get("statut"), ex.get("dispo"), ex.get("places_restantes"), ex.get("places_init"))
        if k <= pnow_s:                                   # commencé : statut final, plus rien ne bouge
            if ex.get("statut") is None:
                ex["statut"] = "libre_fin" if ex.get("dispo") else "reserve"
        else:
            if k not in obs["slots"] and ex["date"] in obs["couverts"] and k > seuil:
                if legacy:
                    ex.update({"places_restantes": 0, "dispo": False, "booked": True,
                               "dernier_vu": now, "releve": now})
                elif ex.get("statut") != "retire":
                    ex.update({"statut": "retire", "dispo": False})
            rest = ex.get("places_restantes") or 0
            ref = max(ex.get("jauge_publiee") or 0, ex.get("places_max_vu") or 0, jauge or 0, rest)
            ex.update({"places_init": ref, "places_max": ref, "nb_joueurs_max": ref,
                       "places_vendues": ref - rest})
        if avant != (ex.get("statut"), ex.get("dispo"), ex.get("places_restantes"),
                     ex.get("places_init")) or ex.get("releve") == now:
            ex["maj"] = now
    return n


def releve_enseigne(eid: str, jours: int) -> tuple[str, str, int]:
    cfg = ENSEIGNES[eid]
    path = f"{OBS_DIR}/qweekle__{eid}.json"
    st = read_json(path, {}) or {}
    st.update({"enseigne_id": eid, "enseigne_nom": cfg["nom"], "website": cfg["website"],
               "plateforme": "qweekle", "secteur": "parc"})
    st.setdefault("sessions", {})
    rooms = st.setdefault("rooms", {})
    st["centres"] = {}
    meta = st.setdefault("_meta", {})
    t0, debut, n_total, erreurs, req = time.time(), now_iso(), 0, [], 0

    def un(parc: str):
        try:
            obs = (releve_legacy(cfg["org"], parc, st, jours) if cfg["kind"] == "legacy"
                   else releve_v3(parc, st, jours))
            return parc, obs, None
        except Exception as e:                            # un parc en échec n'arrête pas les autres
            return parc, None, f"{parc}: {e}"[:160]

    with ThreadPoolExecutor(WORKERS) as pool:
        res = list(pool.map(un, cfg["parcs"]))
    for parc, obs, err in res:
        info = cfg["parcs"][parc]
        cid, rid = f"qw-{parc}", f"qw-{parc}-parc"
        st["centres"][cid] = dict(info)
        if err:
            erreurs.append(err)
            continue
        n_total += fusion(st, rid, obs, cfg["kind"] == "legacy")
        caps = [s.get("places_init") or 0 for s in st["sessions"].values() if s.get("room_id") == rid]
        rooms[rid] = {"name": "Entrées parc", "centre_id": cid, "theme": "parc",
                      "duration": obs.get("duree"), "min_players": 1,
                      "max_players": max(caps) if caps else None, "source": obs["source"]}
        req += obs["req"]
    meta.update({"last_scrape": debut, "n_centres": len(st["centres"]), "n_rooms": len(rooms),
                 "n_sessions": len(st["sessions"]), "n_sessions_releve": n_total, "requetes": req,
                 "duree_s": round(time.time() - t0)})
    if erreurs:
        meta.update({"erreur": " | ".join(erreurs)[:300], "erreur_at": now_iso()})
    else:
        meta.pop("erreur", None)
        meta.pop("erreur_at", None)
    write_json(path, st)
    statut = "ok" if not erreurs else f"KO {len(erreurs)}/{len(cfg['parcs'])} parcs"
    return eid, statut, n_total


def scrape(jours: int = HORIZON_JOURS, only: str | None = None) -> int:
    t0 = time.time()
    ids = [e for e in ENSEIGNES if not only or e == only]
    with ThreadPoolExecutor(len(ids)) as pool:            # hôtes différents : en parallèle
        res = list(pool.map(lambda e: releve_enseigne(e, jours), ids))
    ko = 0
    for eid, statut, n in res:
        if statut != "ok":
            ko += 1
            m = (read_json(f"{OBS_DIR}/qweekle__{eid}.json", {}) or {}).get("_meta", {})
            print(f"[qweekle] ⚠ {eid}: {statut} — {m.get('erreur', '')}")
        else:
            print(f"[qweekle] {eid}: {n} créneaux")
    print(f"[qweekle] {len(res) - ko}/{len(res)} enseignes OK | {time.time() - t0:.0f}s")
    return ko


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=HORIZON_JOURS)
    ap.add_argument("--only", choices=sorted(ENSEIGNES))
    a = ap.parse_args()
    scrape(a.days, a.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
