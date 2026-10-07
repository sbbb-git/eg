#!/usr/bin/env python3
"""escape_4escape_all.py — scrape 4escape À GRANDE ÉCHELLE (tout le réseau IDF).

Découverte (juin 2026) : le widget moderne widgets.4escape.app expose, sur le
SOUS-DOMAINE de chaque enseigne <company>.4escape.io, des endpoints publics :
  - GET  /api/public/boot        -> valide que la company existe (success+baseURL)
  - GET  /api/public/settings    -> catalogue (rooms : durée, joueurs, difficulté)
  - POST /api/public/availability/upcoming {date}   -> TOUS les créneaux LIBRES
        de la semaine par room  (marche même si booking-data-json est 401)
  - POST /booking-data-json {date,viewDuration}      -> planning THÉORIQUE complet
        + grille de PRIX (souvent verrouillé en 401 -> on dégrade proprement)

=> remplissage = 1 - (créneaux libres / créneaux théoriques).
   Si booking verrouillé : on garde au moins #libres + lead-time (proxy demande).

La company se DEVINE depuis le site officiel (SLD) puis se VALIDE via /boot.
Sites candidats : resolver (escape_sites_cache.json) + annuaire + catalogue
existant. Observations APPEND-ONLY par enseigne (escape_data/4escape_all/).
Resumable + idempotent.

  python3 escape_4escape_all.py --discover   # (re)trouve les companies 4escape
  python3 escape_4escape_all.py --scrape     # 1 relevé remplissage/prix
  python3 escape_4escape_all.py              # discover + scrape
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse as up
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from safestore import read_json, write_json

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
COMPANIES_FILE = "escape_4escape_companies.json"
OBS_DIR = "escape_data/4escape_all"
CACHE = "escape_sites_cache.json"
DIRECTORY = "escape_idf_directory.json"
LEGACY_CATALOG = "escape_4escape_catalog.json"
PARIS = ZoneInfo("Europe/Paris")
WORKERS = 6            # enseignes scrapées en parallèle (sous-domaines distincts)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch_json(req: Request, tmo: int, tries: int = 3):
    """GET/POST -> JSON. Lecture intégrale (aucun plafond de taille).
    Renvoie {"_err": code_http, "_kind": "http"} si le serveur refuse (réponse
    définitive), ou {"_err": ..., "_kind": "error"} après `tries` échecs réseau /
    JSON illisible (incident passager : à NE PAS confondre avec un refus)."""
    last = ""
    for i in range(tries):
        try:
            with urlopen(req, timeout=tmo) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except HTTPError as e:
            return {"_err": e.code, "_kind": "http"}
        except Exception as e:                 # réseau, timeout, JSON tronqué
            last = str(e)[:80]
            time.sleep(2 * (i + 1))
    return {"_err": last, "_kind": "error"}


def api(comp: str, path: str, post: dict | None = None, tmo: int = 30):
    url = f"https://{comp}.4escape.io{path}"
    h = {"User-Agent": UA, "Accept": "application/json",
         "Origin": f"https://{comp}.4escape.io", "Referer": f"https://{comp}.4escape.io/"}
    if post is not None:
        req = Request(url, data=json.dumps(post).encode(),
                      headers={**h, "Content-Type": "application/json"})
    else:
        req = Request(url, headers=h)
    return _fetch_json(req, tmo)


def sld(website: str) -> str | None:
    m = re.search(r"https?://(?:www\.)?([a-z0-9\-]+)\.[a-z.]{2,}", website or "", re.I)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------------------
# DISCOVERY : SLD candidats -> /boot -> companies 4escape validées
# ---------------------------------------------------------------------------

def _variants(website: str, slug: str) -> set[str]:
    """Candidats de sous-domaine 4escape pour une enseigne."""
    out: set[str] = set()
    s = sld(website)
    if s:
        out.add(s); out.add(s.replace("-", ""))
    if slug:
        out.add(slug); out.add(slug.replace("-", ""))
        # enlève suffixes communs (-paris, -escape, -escapegame)
        base = re.sub(r"-(paris|escape|escapegame|game|idf|\d+)$", "", slug)
        if base and base != slug:
            out.add(base)
    return {c for c in out if c and len(c) >= 3}


def candidate_companies() -> dict[str, str]:
    """sld-candidat -> website (toutes sources + variantes de slug)."""
    cands: dict[str, str] = {}
    cache = read_json(CACHE, {}) or {}
    for slug, e in (cache.get("venues") or {}).items():
        for c in _variants(e.get("website", ""), slug):
            cands.setdefault(c, e.get("website") or "")
    directory = read_json(DIRECTORY, {}) or {}
    for v in directory.get("venues", []):
        for c in _variants(v.get("website", ""), v.get("slug", "")):
            cands.setdefault(c, v.get("website") or v.get("url", ""))
    return cands


def discover() -> dict:
    store = read_json(COMPANIES_FILE, {}) or {}
    store.setdefault("companies", {})   # comp -> {website, org_name, validated, ...}
    store.setdefault("tried", {})       # sld -> bool (évite re-test)
    # seed : companies historiques (les 16) déjà connues 4escape
    legacy = read_json(LEGACY_CATALOG, {}) or {}
    for comp in (legacy.get("companies") or {}):
        store["companies"].setdefault(comp, {"source": "escapegame.fr"})
        store["tried"][comp] = True

    cands = candidate_companies()
    new = 0
    for cand, website in cands.items():
        if store["tried"].get(cand) or cand in store["companies"]:
            continue
        store["tried"][cand] = True
        d = api(cand, "/api/public/boot", tmo=7)
        if isinstance(d, dict) and d.get("success"):
            store["companies"][cand] = {"website": website,
                                        "source": "discover", "found": now_iso()}
            new += 1
            print(f"[discover] ✓ {cand:24} <- {website[:40]}")
        time.sleep(0.1)
    store["_meta"] = {"updated": now_iso(), "n_companies": len(store["companies"]),
                      "n_tried": len(store["tried"])}
    write_json(COMPANIES_FILE, store)
    print(f"[discover] +{new} -> {len(store['companies'])} companies 4escape "
          f"({len(store['tried'])} SLD testés)")
    return store


# ---------------------------------------------------------------------------
# SCRAPE : settings + availability/upcoming + booking-data-json
# ---------------------------------------------------------------------------

def _items(v):
    return list(v.values()) if isinstance(v, dict) else (v or [])


THEME_KW = [
    ("horreur", r"horreur|horror|peur|terreur|zombie|asile|psychiatr|exorc|hant[ée]|gore|épouvante|epouvante|cauchemar|saw|insomni|paranormal|spirit|d[ée]mon"),
    ("enquête", r"enqu[êe]te|d[ée]tective|crime|meurtre|police|disparition|braquage|casse|prison|[ée]vasion|alcatraz|mafia|cluedo|sherlock|tueur|s[ée]questr|interpol|fbi"),
    ("fantastique", r"fantastique|magie|magique|sorcier|wizard|potion|dragon|conte|l[ée]gende|alice|narnia|m[ée]di[ée]val|elfe|f[ée]e|mythe|olympe|excalibur"),
    ("science-fiction", r"espace|spatial|science|labo|laborat|nucl[ée]aire|virus|futur|robot|alien|mars|station|cyber|matrix|temps|time|apocalyp|quantique|exp[ée]rience"),
    ("aventure", r"aventure|tr[ée]sor|pirate|jungle|temple|[ée]gypte|tombe|momie|indiana|far.?west|western|safari|braquage|or|gold|qu[êe]te"),
]


def classify_theme(name: str, desc: str) -> str:
    t = f"{name} {desc}".lower()
    for theme, rx in THEME_KW:
        if re.search(rx, t):
            return theme
    return "aventure"


# Modes de tarif 4escape, lus entrée par entrée (une grille peut les mélanger,
# ex. forfait jusqu'à 3 joueurs puis prix par joueur) :
#   absolute / absolute-product-quantity      -> montant = prix du GROUPE
#   per-player / per-product-quantity         -> montant = prix PAR JOUEUR
PER_PLAYER_MODES = {"per-player", "per-product-quantity"}


def _grid(prices: dict) -> dict:
    """prices 4escape : clé = nb de joueurs, amount_charged en cents.
    Renvoie {nb: (montant €, mode)}."""
    g = {}
    for k, p in (prices or {}).items():
        if str(k).isdigit() and isinstance(p, dict) and p.get("amount_charged"):
            g[int(k)] = (round(p["amount_charged"] / 100, 2), p.get("mode"))
    return g


def price_block(prices: dict, rmeta: dict) -> dict:
    """PRIX TOTAL payé par taille de groupe + prix total moyen d'une session.
    Le type de tarif vient du champ `mode` de l'API. L'ancienne heuristique
    (« grille croissante = déjà un total ») lisait un tarif par joueur plat
    — 25 €, 25 €, 25 € — comme un prix de groupe : ~18 % des grilles étaient
    fausses. Elle ne sert plus que de repli quand `mode` est absent."""
    raw = _grid(prices)
    if not raw:
        return {"prix_total": {}, "prix_total_moyen": None, "prix_joueur": {}}
    ks = sorted(raw)
    if all(m for _, m in raw.values()):
        total = {k: (round(raw[k][0] * k, 2) if raw[k][1] in PER_PLAYER_MODES else raw[k][0])
                 for k in ks}
    else:
        amt = {k: raw[k][0] for k in ks}
        is_total = amt[ks[-1]] >= amt[ks[0]]
        total = {k: (amt[k] if is_total else round(amt[k] * k, 2)) for k in ks}
    joueur = {k: round(total[k] / k, 2) for k in ks}
    nmin = rmeta.get("min_players") or ks[0]
    nmax = rmeta.get("max_players") or ks[-1]
    # Prix de référence = prix pour un GROUPE TYPE de 4 joueurs (ou la taille
    # minimale / maximale de la salle si 4 sort de sa plage). La moyenne sur toute
    # la plage gonflait les grandes salles : « Un Poison Presque Parfait »
    # (2 à 40 joueurs) ressortait à 819 € pour 156 € à 4.
    valid = [k for k in ks if nmin <= k <= nmax] or ks
    cible = min(max(4, valid[0]), valid[-1])
    k_ref = min(valid, key=lambda k: (abs(k - cible), k))
    return {"prix_total": total, "prix_joueur": joueur,
            "prix_total_moyen": round(total[k_ref], 1)}


VILLE_ALIAS = {"CLICHY-LA-GARENNE": "CLICHY", "LEVALLOIS": "LEVALLOIS-PERRET"}


def norm_ville(v: str | None) -> str:
    """Forme unique d'un nom de commune : majuscules, sans accent ni code entre
    parenthèses, mots reliés par des tirets (convention INSEE)."""
    v = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", (v or "").strip())
    v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode().upper()
    v = re.sub(r"[\s\-']+", "-", v).strip("-")
    return VILLE_ALIAS.get(v, v)


def _norm_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _coords(addr: dict):
    g = (addr.get("geo") or {}).get("coordinates") or []
    return (g[1], g[0]) if len(g) == 2 else (None, None)


def scrape_company(comp: str, date_str: str) -> dict:
    """Renvoie le bloc ENSEIGNE -> CENTRES (premises) -> SALLES -> SESSIONS."""
    s = api(comp, "/api/public/settings")
    if not (isinstance(s, dict) and s.get("success")):
        return {"_err": s.get("_err") if isinstance(s, dict) else "no-settings"}
    org = s.get("organization", {}) or {}
    addr = org.get("address", {}) or {}
    org_name = (org.get("display_name") or org.get("name") or comp).strip()

    # ── CENTRES = premises (sinon 1 centre = l'adresse de l'enseigne) ──
    centres: dict[str, dict] = {}
    for p in _items(s.get("premises")):
        pid = p.get("_id")
        if not pid:
            continue
        pa = p.get("address") or {}
        lat, lon = _coords(pa)
        centres[pid] = {
            "nom": (p.get("display_name") or p.get("name") or org_name).strip(),
            "cp": (pa.get("postal_code") or "").strip(), "ville": norm_ville(pa.get("locality")),
            "adresse": (pa.get("street") or "").strip(), "lat": lat, "lon": lon}
    if not centres:
        lat, lon = _coords(addr)
        centres[comp] = {"nom": org_name, "cp": (addr.get("postal_code") or "").strip(),
                         "ville": norm_ville(addr.get("locality")),
                         "adresse": (addr.get("street") or "").strip(), "lat": lat, "lon": lon}
    default_cid = next(iter(centres))

    # ── SALLES = rooms (rattachées à leur centre via room.premise) ──
    rooms_meta: dict[str, dict] = {}
    for r in _items(s.get("rooms")):
        if not r.get("_id"):
            continue
        name = (r.get("display_name") or r.get("name") or "").strip()
        desc = (r.get("short_description") or r.get("description") or "")
        cid = r.get("premise") or default_cid
        if cid not in centres:
            cid = default_cid
        rooms_meta[r["_id"]] = {
            "name": name, "centre_id": cid, "duration": r.get("duration"),
            "min_players": r.get("minimum_players"), "max_players": r.get("maximum_players"),
            "difficulty": r.get("difficulty"), "theme": classify_theme(name, desc)}

    # ── SESSIONS ──
    sessions: dict[str, dict] = {}
    bd = _booking(comp, date_str, view=7)
    if isinstance(bd, dict) and bd.get("_kind") == "error":
        return {"_err": f"booking: {bd.get('_err')}"}
    locked = not (isinstance(bd, dict) and bd.get("results"))
    # Salles réservables mais ABSENTES du catalogue public (/settings) : l'enseigne les a
    # masquées. Sans fiche, le sync jetait tous leurs créneaux (Illucity : 100 % de ses
    # données). On crée une fiche minimale tirée des créneaux, sans inventer de nom.
    if not locked:
        for slot in bd["results"]:
            rid = slot.get("roomId")
            if rid and rid not in rooms_meta:
                rooms_meta[rid] = {"name": f"Salle non publiée · {rid[-4:]}", "centre_id": default_cid,
                                   "duration": None, "min_players": None,
                                   "max_players": slot.get("maximumPlayers"), "difficulty": None,
                                   "theme": None, "non_publiee": True}
    if not locked:
        for slot in bd["results"]:
            rid, start, end = slot.get("roomId"), slot.get("start"), slot.get("end")
            if not (rid and start):
                continue
            rmeta = rooms_meta.get(rid, {})
            duree = None
            if end:
                try:
                    duree = int((datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
                                 - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
                except ValueError:
                    pass
            booked = bool(slot.get("booked"))
            sessions[f"{start[:10]}T{start[11:16]}|{rid}"] = {
                "date": start[:10], "heure": start[11:16], "duree_minutes": duree,
                "room_id": rid, **price_block(slot.get("prices"), rmeta),
                "nb_joueurs_min": rmeta.get("min_players"), "nb_joueurs_max": rmeta.get("max_players"),
                "prive": slot.get("private"), "groupby": slot.get("booking_groupby_method"),
                "places_max": slot.get("maximumPlayers"),
                "places_restantes": slot.get("remainingPlayers"),
                "booked": booked, "dispo": (not booked and not bool(slot.get("disabled")))}
    else:
        up_data = api(comp, "/api/public/availability/upcoming",
                      post={"date": date_str, "period": "week"})
        res = up_data.get("results") if isinstance(up_data, dict) else None
        for rid, slots in (res.items() if isinstance(res, dict) else []):
            rmeta = rooms_meta.get(rid, {})
            for sl in (slots if isinstance(slots, list) else []):
                start = sl.get("start")
                if not start:
                    continue
                sessions[f"{start[:10]}T{start[11:16]}|{rid}"] = {
                    "date": start[:10], "heure": start[11:16], "duree_minutes": None,
                    "room_id": rid, "prix_total": {}, "prix_joueur": {}, "prix_total_moyen": None,
                    "nb_joueurs_min": rmeta.get("min_players"), "nb_joueurs_max": rmeta.get("max_players"),
                    "prive": None, "groupby": None, "places_max": None,
                    "places_restantes": None, "booked": False, "dispo": True}
    return {
        "enseigne_id": _norm_id(org_name), "enseigne_nom": org_name,
        "website": (org.get("website") or "").strip(),
        "centres": centres, "rooms": rooms_meta, "sessions": sessions, "prices_locked": locked,
    }


def _booking(comp: str, date_str: str, view: int = 7):
    body = up.urlencode({"UID": uuid.uuid4().hex, "date": date_str, "viewDuration": view}).encode()
    url = f"https://{comp}.4escape.io/booking-data-json"
    req = Request(url, data=body, headers={
        "User-Agent": UA, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
        "Origin": f"https://{comp}.4escape.io", "Referer": f"https://{comp}.4escape.io/",
        "Content-Type": "application/x-www-form-urlencoded"})
    return _fetch_json(req, tmo=90)


def paris_now() -> datetime:
    """Heure de Paris, naïve (comme les horaires de créneaux), heure d'été/hiver comprise."""
    return datetime.now(PARIS).replace(tzinfo=None)


def _slot_dt(sess: dict):
    try:
        return datetime.strptime(f"{sess['date']} {sess['heure']}", "%Y-%m-%d %H:%M")
    except (ValueError, KeyError):
        return None


def _vu_apres_debut(ex: dict, debut: datetime) -> bool:
    """Le dernier relevé de la session a-t-il eu lieu après son heure de début ?"""
    try:
        dv = datetime.fromisoformat(ex["dernier_vu"]).astimezone(PARIS).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError):
        return False
    return dv >= debut


def _suivi_places(ex: dict) -> None:
    """Places réellement vendues sur une session PARTAGÉE (vendue à la place).

    On suit les places BLOQUÉES = capacité - places libres. La référence est le
    minimum de places bloquées jamais observé ; les ventes = bloquées - référence.
      - capacité bloquée d'emblée par l'enseigne : déjà dans la référence -> non comptée ;
      - annulation : les bloquées baissent -> la référence suit, cumul net juste ;
      - hausse OU baisse de capacité : capacité et places libres bougent ensemble,
        les bloquées ne changent pas -> aucune fausse vente (l'ancienne règle comptait
        une baisse de capacité comme des ventes) ;
      - l'API renvoie parfois plus de places libres que la capacité : géré, rien n'est borné à tort.
    Salle PRIVÉE : 0 place restante veut dire « réservée », pas « 6 joueurs » ; 4escape
    masque la taille du groupe -> vendues = None (non mesurable)."""
    rest, cap = ex.get("places_restantes"), ex.get("places_max")
    if ex.get("prive") is not False or rest is None or cap is None:
        if rest is not None:
            ex["places_init"] = max(rest, ex.get("places_init") or 0)
        ex["places_vendues"] = None
        return
    bloquees = cap - rest
    ref = ex.get("places_bloquees_min")
    if ref is None:                    # session suivie avant ce correctif : reprise depuis places_init
        init = ex.get("places_init")
        ref = cap - min(init, cap) if init is not None else bloquees
    ref = min(ref, bloquees)
    ex["places_bloquees_min"] = ref
    ex["places_init"] = cap - ref
    ex["places_vendues"] = max(0, min(cap, bloquees - ref))


CHAMPS_RELEVE = ("prix_total", "prix_joueur", "prix_total_moyen", "duree_minutes",
                 "nb_joueurs_min", "nb_joueurs_max", "prive", "groupby", "places_max",
                 "booked", "dispo")


def reconcile(st: dict, current: dict, locked: bool) -> None:
    """Fusion idempotente + reconstitution du statut :
       - créneau booké APRÈS avoir été vu libre          -> reserve
       - créneau resté libre jusqu'à son début            -> libre_fin
       - compte verrouillé : libre puis disparu           -> reserve
       - compte ouvert : disparu de l'API avant son début -> retire (supprimé par l'enseigne)

    Un relevé fait APRÈS le début d'un créneau est ignoré : l'API renvoie alors
    tout créneau passé comme « désactivé », ce qui écrasait l'état réel (un créneau
    resté libre finissait sans statut au lieu de libre_fin).
    Chaque modification date la session (`maj`) pour le sync incrémental."""
    now = now_iso()
    pnow = paris_now()
    for key, sess in current.items():
        ex = st["sessions"].get(key)
        debut = _slot_dt(sess)
        if debut is not None and debut <= pnow:
            continue                   # relevé post-début : non informatif, état figé
        if ex is None:
            ex = {**sess, "premier_vu": now, "statut": None,
                  "seen_free": False, "seen_booked": False}
        else:
            ex.update({k: sess[k] for k in CHAMPS_RELEVE})
            if sess.get("places_restantes") is not None:
                ex["places_restantes"] = sess["places_restantes"]
            if ex.get("statut") == "retire":
                ex["statut"] = None    # réapparu : n'était pas supprimé
        ex["dernier_vu"] = now
        ex["releve"] = now
        if sess.get("dispo"):
            ex["seen_free"] = True
        if sess.get("booked"):
            ex["seen_booked"] = True
        # 'reserve' FIABLE : booké APRÈS avoir été vu libre (vraie transition).
        # Un créneau booké dès la 1re observation (jamais vu libre) reste 'None'
        # -> c'est peut-être une salle fermée/bloquée, pas une vraie résa.
        if sess.get("booked") and ex.get("seen_free"):
            ex["statut"] = "reserve"
        _suivi_places(ex)
        ex["maj"] = now
        st["sessions"][key] = ex
    # passes sur l'historique : fin de vie + disparitions
    fenetre = pnow + timedelta(days=6)   # sûrement couvert par la requête « 7 jours »
    for key, ex in st["sessions"].items():
        if ex.get("statut") in ("reserve", "libre_fin"):
            continue
        debut = _slot_dt(ex)
        if debut is None:
            continue
        avant = (ex.get("statut"), ex.get("dispo"))
        if debut <= pnow:                                 # créneau commencé -> statut final
            if ex.get("statut") == "retire":
                pass                                      # supprimé avant son début : reste retiré
            elif ex.get("booked"):
                ex["statut"] = "reserve"
            elif ex.get("dispo"):
                ex["statut"] = "libre_fin"
            elif ex.get("seen_free") and _vu_apres_debut(ex, debut):
                # relevé post-début d'AVANT ce correctif : « désactivé » a écrasé un
                # créneau vu libre et jamais booké -> on le rétablit en libre_fin
                ex["statut"] = "libre_fin"
                ex["dispo"] = True
            else:
                ex["statut"] = None                       # jamais vu libre : fermé / inconnu
        elif key not in current and debut <= fenetre:
            if locked:
                if ex.get("dispo"):
                    ex["statut"] = "reserve"              # libre puis disparu = réservé
                    ex["dispo"] = False
            elif ex.get("statut") != "retire":
                ex["statut"] = "retire"                   # l'enseigne a retiré le créneau
                ex["dispo"] = False
        if (ex.get("statut"), ex.get("dispo")) != avant:
            ex["maj"] = now


def _scrape_one(comp: str, date_str: str) -> tuple[str, str, int]:
    """Scrape + fusion d'une enseigne. Renvoie (company, statut, nb sessions relevées).
    Un échec est consigné dans le store (`_meta.erreur`) pour que la surveillance le voie."""
    path = f"{OBS_DIR}/{comp}.json"
    obs = scrape_company(comp, date_str)
    st = read_json(path, {}) or {"company": comp, "rooms": {}, "sessions": {}}
    st.setdefault("sessions", {})
    st.setdefault("rooms", {})
    meta = st.setdefault("_meta", {})
    if "_err" in obs:
        meta.update({"erreur": str(obs["_err"])[:120], "erreur_at": now_iso()})
        write_json(path, st)
        return comp, f"KO {obs['_err']}", 0
    st.update({k: obs[k] for k in ("enseigne_id", "enseigne_nom", "website", "centres")})
    for rid, m in obs["rooms"].items():
        prev = st["rooms"].get(rid, {})
        m["history"] = prev.get("history", [])           # préservé entre relevés
        m["n_releves"] = prev.get("n_releves", 0)
        st["rooms"][rid] = m
    reconcile(st, obs["sessions"], obs["prices_locked"])
    # historique par salle (free/booked du relevé courant) -> variance + confiance
    cur: dict[str, list] = {}
    for s in obs["sessions"].values():
        fb = cur.setdefault(s["room_id"], [0, 0])
        fb[0] += 1 if s.get("dispo") else 0
        fb[1] += 1 if s.get("booked") else 0
    ts = now_iso()
    for rid, m in st["rooms"].items():
        fb = cur.get(rid)
        if fb is None:
            continue
        m["history"] = (m.get("history", []) + [{"releve": ts, "free": fb[0], "booked": fb[1]}])[-60:]
        m["n_releves"] = m.get("n_releves", 0) + 1
    st["_meta"] = {"last_scrape": ts, "n_centres": len(obs["centres"]),
                   "n_rooms": len(st["rooms"]), "n_sessions": len(st["sessions"]),
                   "n_sessions_releve": len(obs["sessions"]),
                   "prices_locked": obs["prices_locked"]}
    write_json(path, st)
    return comp, "locked" if obs["prices_locked"] else "ok", len(obs["sessions"])


def scrape(limit: int = 0) -> int:
    store = read_json(COMPANIES_FILE, {}) or {}
    comps = sorted(store.get("companies", {}))
    if limit:
        comps = comps[:limit]
    date_str = paris_now().strftime("%Y-%m-%d")
    t0 = time.time()
    with ThreadPoolExecutor(WORKERS) as pool:
        res = list(pool.map(lambda c: _scrape_one(c, date_str), comps))
    ko = [(c, s) for c, s, _ in res if s.startswith("KO")]
    n = sum(x for _, _, x in res)
    for c, s in ko:
        print(f"[scrape] ⚠ {c}: {s}")
    print(f"[scrape] OK={len(res) - len(ko)} KO={len(ko)} / {len(comps)} enseignes | "
          f"{n} créneaux relevés | {time.time() - t0:.0f}s")
    return len(ko)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--scrape", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not (args.discover or args.scrape):
        args.discover = args.scrape = True
    if args.discover:
        discover()
    if args.scrape:
        scrape(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
