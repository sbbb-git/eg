#!/usr/bin/env python3
"""veille.py — la VEILLE : repérer les nouveaux établissements (parcs enfants, escape games).

Sources, à chaque passage (quotidien, workflow escape-daily) :
  1. sites des réseaux suivis (palomano.com, gulli-parc.com…) : liens vers leurs boutiques
     (Qweekle, ROLLER). Une boutique Qweekle inconnue d'un réseau déjà suivi, qui vend déjà des
     entrées datées, est INTÉGRÉE AUTOMATIQUEMENT (parcs_decouverts.json, lu par escape_parcs.py) ;
     les autres boutiques nouvelles sont signalées.
  2. pages « ouvertures » des réseaux (palomano.com/ouvertures-imminentes) : parcs annoncés.
  3. registre des entreprises (recherche-entreprises.api.gouv.fr) : sociétés créées depuis moins
     d'un an au nom d'un réseau ou d'un concept de parc — souvent le premier signe d'une ouverture.
  4. annuaire des escape games d'Île-de-France (escape_idf_directory.json, refait chaque jour) :
     lieux apparus depuis le passage précédent.

Sortie : veille.json (publié avec le dashboard, onglet « Veille »). Chaque signal garde sa date de
première détection. Les nouveautés du passage sont résumées dans veille_nouveautes.md (le workflow
en fait une issue GitHub). Le premier passage fait l'inventaire initial, sans alerte.

  python3 veille.py
"""
from __future__ import annotations

import html
import json
import re
import time
import unicodedata
import urllib.parse as up
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen

import escape_parcs as P
from safestore import read_json, write_json

OUT = "veille.json"
DECOUVERTS = "parcs_decouverts.json"
RESUME = "veille_nouveautes.md"
UA = "Mozilla/5.0 (observatoire-loisirs, veille)"
SITES = {
    "palomano": ["https://www.palomano.com/", "https://www.palomano.com/parcs/", "https://www.palomano.com/parcs-sud/",
                 "https://www.palomano.com/paris/", "https://www.palomano.com/ouvertures-imminentes/"],
    "gulli-parc": ["https://gulli-parc.com/", "https://gulli-parc.com/nos-parcs/"],
    "you-kids": ["https://you-kids.fr/"],
    "royal-kids": ["https://royalkids.fr/", "https://royalkids.fr/nos-parcs/"],
    "planet-park": ["https://planet-park.com/"],
}
ANNONCE = re.compile(r"^(Palomano|Gulli Parc|You Kids|Royal Kids|Planet Park)\s+\S", re.I)
# sociétés : requêtes au registre (+ code NAF quand le mot seul est trop large)
REQUETES = [("palomano", None), ("gulli parc", None), ("you kids", None), ("royal kids", None),
            ("planet park", None), ("mini ville", "93.29Z"), ("parc de jeux", "93.29Z"),
            ("parc enfants", "93.29Z"), ("kids park", "93.29Z"), ("city kids", "93.29Z")]


def _get(url: str, tries: int = 3) -> tuple[int, str]:
    for i in range(tries):
        try:
            r = urlopen(Request(url, headers={"User-Agent": UA, "Accept-Language": "fr"}), timeout=40)
            return r.status, r.read().decode("utf-8", "replace")
        except Exception as e:
            code = getattr(e, "code", 0)
            if code and code < 500:
                return code, ""
            time.sleep(2 * (i + 1))
    return 0, ""


def _cle(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return "-".join("".join(c if c.isalnum() else " " for c in s).split())


def _texte(h: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


# ───────────────────────── boutiques connues (configuration) ─────────────────────────
def boutiques_connues() -> dict[str, str]:
    """{clé de boutique: enseigne} pour tout ce qui est déjà collecté."""
    k = {}
    for eid, cfg in P.ENSEIGNES.items():
        for pid, i in cfg["parcs"].items():
            kind = i.get("kind", cfg.get("kind"))
            if kind == "legacy":
                k[f"qweekle:{i.get('org', cfg.get('org'))}/{i.get('shop') or pid}"] = eid
            elif kind == "v3":
                k[f"v3:{i.get('host', pid)}"] = eid
            elif kind == "roller":
                u = up.urlparse(i.get("booking", ""))
                s = [x for x in u.path.split("/") if x]
                k[f"roller:{s[0]}/{s[1]}" if u.netloc == "ecom.roller.app" and len(s) >= 2 else f"roller:{u.netloc}"] = eid
    return k


def liens_boutiques(h: str) -> set[str]:
    out = set()
    for org, slug in re.findall(r"https?://([a-z0-9-]+)\.qweekle\.com/shop/([a-z0-9-]+)", h):
        out.add(f"qweekle:{org}/{slug}")
    for host in re.findall(r"https?://([a-z0-9-]+\.qweekle\.shop)", h):
        out.add(f"v3:{host}")
    for venue, slug in re.findall(r"https?://ecom\.roller\.app/([a-z0-9-]+)/([a-z0-9-]+)", h):
        out.add(f"roller:{venue}/{slug}")
    return out


def fiche_qweekle(org: str, slug: str) -> dict:
    """Nom, adresse (titre de la boutique : « Nom : adresse - 57130 Ville ») et catégorie
    d'entrées datées si la boutique en vend déjà."""
    base = f"https://{org}.qweekle.com/shop/{slug}/"
    st, h = _get(base)
    if st != 200:
        return {"ok": False, "http": st}
    t = html.unescape((re.search(r"<title>([^<]*)", h) or [None, ""])[1]).strip()
    nom, _, adr = t.partition(" : ")
    m = re.search(r"^(.*?)[\s,\-–]*\b(\d{5})\s+(.+)$", adr.strip())
    fiche = {"ok": True, "nom": re.sub(r"\s+", " ", nom).strip() or slug, "adresse": (m.group(1) if m else adr).strip(" ,-–"),
             "cp": m.group(2) if m else "", "ville": (m.group(3) if m else "").strip().upper(), "url": base}
    st, b = _get(base + "booking?lang=fr")
    cats = [c for c in dict.fromkeys(re.findall(re.escape(base) + r"([a-z0-9\-]+)\?lang=fr", b))
            if P.CAT_PARC.search(c) and not P.CAT_EXCLUE.search(c)]
    fiche["cat"] = cats[0] if cats else None
    return fiche


# ───────────────────────── sources ─────────────────────────
def source_reseaux(signal, decouverts: list) -> None:
    connus = boutiques_connues()
    org_de = {cfg.get("org"): eid for eid, cfg in P.ENSEIGNES.items() if cfg.get("org")}
    deja = {d["id"] for d in decouverts}
    for eid, pages in SITES.items():
        vus = set()
        for url in pages:
            st, h = _get(url)
            if st != 200:
                continue
            vus |= liens_boutiques(h)
            if "ouverture" in url:                       # parcs annoncés (titres de la page)
                for m in re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", h, re.S):
                    t = _texte(m)
                    if ANNONCE.match(t) and not re.search(r"nos parcs|prochains", t, re.I):
                        signal(f"annonce:{eid}:{_cle(t)}", univers="parcs", type="ouverture annoncée", titre=t,
                               marque=eid, statut="annoncé", url=url, detail="page « ouvertures imminentes » du réseau")
            time.sleep(0.5)
        for k in sorted(vus - set(connus)):
            plat, _, ref = k.partition(":")
            if plat == "qweekle":
                org, slug = ref.split("/", 1)
                if slug in deja or slug in P.ENSEIGNES.get(org_de.get(org, ""), {}).get("parcs", {}):
                    continue
                f = fiche_qweekle(org, slug)
                if not f.get("ok"):
                    continue
                cible = org_de.get(org)
                if cible and f.get("cat"):
                    decouverts.append({"enseigne": cible, "id": slug, "nom": f["nom"], "adresse": f["adresse"],
                                       "cp": f["cp"], "ville": f["ville"], "cat": f["cat"],
                                       "decouvert": date.today().isoformat(), "url": f["url"]})
                    statut = "intégré automatiquement"
                else:
                    statut = "à intégrer" if f.get("cat") else "boutique sans entrées datées"
                signal(f"boutique:{k}", univers="parcs", type="nouvelle boutique", titre=f["nom"], marque=cible or eid,
                       ville=f["ville"], cp=f["cp"], statut=statut, url=f["url"],
                       detail=f"{f['adresse']} · {'entrées datées : ' + f['cat'] if f.get('cat') else 'pas encore de vente datée'}")
            else:
                signal(f"boutique:{k}", univers="parcs", type="nouvelle boutique", titre=ref, marque=eid,
                       statut="à intégrer", url=("https://ecom.roller.app/" + ref) if plat == "roller" else "https://" + ref,
                       detail=f"billetterie {'ROLLER' if plat == 'roller' else 'Qweekle eshop'} liée depuis le site du réseau")


def source_registre(signal) -> None:
    connus = {s["siren"] for s in (read_json("parcs_societes.json", {}) or {}).get("entreprises", [])}
    depuis = (date.today() - timedelta(days=365)).isoformat()
    for q, naf in REQUETES:
        for page in (1, 2):
            p = {"q": q, "per_page": 25, "page": page, "etat_administratif": "A"}
            if naf:
                p["activite_principale"] = naf
            st, b = _get("https://recherche-entreprises.api.gouv.fr/search?" + up.urlencode(p))
            if st != 200:
                break
            res = json.loads(b).get("results") or []
            for r in res:
                cree = r.get("date_creation") or ""
                if r.get("siren") in connus or cree < depuis:
                    continue
                sg = r.get("siege") or {}
                nom = r.get("nom_complet") or r.get("nom_raison_sociale") or r["siren"]
                if not naf and _cle(q).replace("-", "") not in _cle(nom).replace("-", ""):
                    continue                                  # le nom doit contenir la marque
                signal(f"societe:{r['siren']}", univers="parcs", type="nouvelle société", titre=nom,
                       ville=(sg.get("libelle_commune") or "").upper(), cp=sg.get("code_postal") or "",
                       statut="à surveiller", url=f"https://annuaire-entreprises.data.gouv.fr/entreprise/{r['siren']}",
                       detail=f"SIREN {r['siren']} · créée le {cree} · NAF {r.get('activite_principale') or '—'} · requête « {q} »")
            if len(res) < 25:
                break
            time.sleep(0.4)
        time.sleep(0.4)


def source_escape(signal) -> None:
    import glob
    d = read_json("escape_idf_directory.json", {}) or {}
    suivies = {_cle((read_json(f, {}) or {}).get("enseigne_nom", "")) for f in glob.glob("escape_data/4escape_all/*.json")}
    # marques suivies d'après la base (lecture publique, la clé est celle du dashboard)
    try:
        k = "sb_publishable_5vFAbOG-hHH0s9mAoYmGyA_EG4bIQYj"
        r = urlopen(Request("https://nbvronwfvoksonatolcs.supabase.co/rest/v1/enseignes?select=nom",
                            headers={"apikey": k, "Authorization": "Bearer " + k}), timeout=40)
        suivies |= {_cle(e.get("nom") or "") for e in json.loads(r.read())}
    except Exception as e:
        print(f"[veille] marques suivies indisponibles ({e}) : seule la couverture 4escape compte")
    suivies.discard("")
    for v in d.get("venues") or []:
        if v.get("likely_event"):
            continue
        n = _cle(v.get("name") or "")
        suivi = bool(v.get("covered_4escape")) or any(n and (n == s or n in s or s in n) for s in suivies if len(s) > 3)
        signal(f"escape:{v.get('source', '')}:{v.get('slug')}", univers="escape", type="lieu d'escape game",
               titre=v.get("name") or v.get("slug"), ville=(v.get("city") or "").upper(), cp=v.get("cp") or v.get("dept") or "",
               statut="suivi" if suivi else "non suivi", url=v.get("url"),
               detail=f"{v.get('n_rooms') or 0} salle(s) annoncée(s) · source {v.get('source')}")


# ───────────────────────── passage ─────────────────────────
def main() -> int:
    prec = read_json(OUT, {}) or {}
    items = prec.get("items", {})
    initial = not items
    dec = read_json(DECOUVERTS, {}) or {}
    decouverts = dec.get("parcs", [])
    maintenant = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nouveaux, vus = [], set()

    def signal(cle, **x):
        vus.add(cle)
        old = items.get(cle)
        items[cle] = {**(old or {}), **x, "premier_vu": (old or {}).get("premier_vu") or maintenant, "dernier_vu": maintenant}
        if not old and not initial:
            nouveaux.append(cle)
        elif old and old.get("statut") != x.get("statut") and x.get("statut") == "intégré automatiquement":
            nouveaux.append(cle)

    for nom, f in (("réseaux", lambda: source_reseaux(signal, decouverts)), ("registre", lambda: source_registre(signal)),
                   ("escape", lambda: source_escape(signal))):
        try:
            f()
        except Exception as e:                       # une source en panne n'arrête pas les autres
            print(f"[veille] ⚠ source {nom} : {e}")
    for k, v in items.items():                       # disparu des sources : gardé, marqué
        v["actif"] = k in vus or not v.get("dernier_vu")
    write_json(OUT, {"generated": maintenant, "inventaire_initial": prec.get("inventaire_initial") or maintenant,
                     "items": dict(sorted(items.items()))})
    write_json(DECOUVERTS, {"maj": maintenant, "parcs": decouverts,
                            "note": "parcs découverts par veille.py et collectés automatiquement par escape_parcs.py"})
    lignes = []
    for k in nouveaux:
        x = items[k]
        lignes.append(f"- **{x.get('titre')}** — {x.get('type')} · {x.get('statut')}"
                      f"{' · ' + x['ville'].title() if x.get('ville') else ''} — {x.get('detail', '')}"
                      f"{' — ' + x['url'] if x.get('url') else ''}")
    with open(RESUME, "w") as fh:
        fh.write(("\n".join(lignes) + "\n") if lignes else "")
    print(f"[veille] {len(items)} signaux suivis · {len(nouveaux)} nouveaux"
          f"{' (inventaire initial, sans alerte)' if initial else ''} · {len(decouverts)} parcs découverts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
