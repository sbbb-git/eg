#!/usr/bin/env python3
"""escape_parcs.py — parcs de loisirs enfants : places restantes par créneau.

Parcs suivis : Palomano (19), ses concurrents du benchmark et tous les parcs de Paris
et de l'Ouest parisien qui vendent leurs entrées par créneau en ligne. Une fonction
de relevé par billetterie :
  - Qweekle, ANCIENNE boutique (<org>.qweekle.com/shop/<parc>) : Palomano, Octopia,
    Gulli Parc, Les Zacroc's, Toumini City, Quand je serai grand, Le Monde d'Ayden,
    Bulles & Billes. Elle liste seulement les créneaux réservables pour N personnes
    (sauf quand elle affiche « N Places disponibles ») : les places restantes EXACTES
    sont retrouvées par dichotomie sur N, une requête servant à tous les créneaux.
  - Qweekle eshop-v3 (*.qweekle.shop, Rêvopolie) : API JSON, places et jauge publiées.
  - ROLLER (api.roller.app : Palomano Clichy et Alfortville, You Kids).
  - Cité des sciences (Cité des enfants), Event Calendar App (Petit Nuage),
    Apex Timing (Île de la Tortuga), Wix Bookings (Kidz World Lyon) : API JSON publiques.
Non mesurables : Royal Kids (entrée libre, pas de vente datée en ligne), Kingooroo
(site fermé), Kid's Palace, Filou Planet… (pas de billetterie en ligne).

Toutes les entrées d'un parc (enfant, adulte, bébé…) puisent dans la même jauge par
créneau (vérifié : 15 enfants + 15 adultes = 30 enfants). On mesure donc UNE jauge
par parc et on relève les prix enfant et adulte de chaque créneau.

Store : escape_data/4escape_all/parc__<enseigne>.json, même format que les enseignes
4escape (centres / rooms / sessions), poussé par supa_sync.py. Les parcs sont marqués
salle.theme = "parc" et session.groupby = "parc" : le dashboard les sépare des escape
games (onglet « Parcs enfants »).

Mesures par créneau :
  places_restantes : places encore réservables (calcul de la billetterie) ;
  places_max       : jauge (publiée, ou valeur habituelle du parc, ou plus haut niveau
                     de places libres vu sur le créneau) ;
  places_init / places_vendues : jauge et places prises, en ENTRÉES. Quand la
                     billetterie compte des présences (jauge du parc à l'instant t,
                     comptage = presence), on les convertit en entrées (× pas / durée).

  python3 escape_parcs.py [--days 14] [--only palomano] [--parc palomano-clichy]
"""
from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
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
        # quota d'ARRIVÉES par créneau de 20 min (30 à Clichy : capacité de ticket ROLLER par
        # session, jauge du parc 180 à part) ; en vacances la jauge du parc s'ajoute le matin
        "comptage": "arrivees",
        "parcs": {
            "palomano-aix-en-provence": _p("45 boulevard Aristide Briand", "13100", "Aix-en-Provence", "Palomano Aix-en-Provence"),
            "palomano-angers": _p("21 rue du Hanipet", "49124", "Saint-Barthélemy-d'Anjou", "Palomano Angers"),
            "palomano-bordeaux": _p("Cours du Médoc", "33300", "Bordeaux", "Palomano Bordeaux"),
            "palomano-caen": _p("10 rue des Hauts de Beaulieu", "14000", "Caen", "Palomano Caen"),
            # premier parc (fév. 2023), seul sur ROLLER — 1,12 M€ de CA déclaré en 2024
            "palomano-clichy": {**_p("125 boulevard Jean Jaurès", "92110", "Clichy", "Palomano Clichy"),
                                "kind": "roller", "anniversaires": "",   # toutes les formules du lieu
                                "booking": "https://reservation-clichy.palomano.com/reserver/fr/products"},
            # dans le complexe Nikito : billetterie ROLLER de Nikito, jauge de parc (présences)
            "palomano-alfortville": {**_p("Rue Félix Mothiron (Nikito)", "94140", "Alfortville", "Palomano Alfortville"),
                                     "kind": "roller", "produit": "1288010", "comptage": "presence",
                                     "booking": "https://ecom.roller.app/nikitoalfortville/palomano/fr/products"},
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
        # jauge du PARC (70, places libres identiques sur plusieurs créneaux consécutifs) :
        # les places mesurent des présences, pas des arrivées
        "parcs": {"octopia": {**_p("5 rue Émile Allez", "75017", "Paris", "Octopia"), "comptage": "presence"}},
    },
    # ── Concurrents (benchmark SB Conseil + recensement Paris / Ouest parisien, oct. 2026) ──
    "gulli-parc": {
        "nom": "Gulli Parc", "website": "https://gulli-parc.com", "kind": "legacy", "org": "la-boite-aux-enfants",
        "parcs": {k: {**v, "cat": "entrees-parc"} for k, v in {
            "gulli-parc-bry": _p("19 bis boulevard Jean Monnet", "94360", "Bry-sur-Marne", "Gulli Parc Bry-sur-Marne"),
            "gulli-parc-thiais": _p("3 rue de la Résistance", "94320", "Thiais", "Gulli Parc Thiais"),
            "gulli-parc-carre-senart": _p("Shopping Parc Carré Sénart", "77127", "Lieusaint", "Gulli Parc Carré Sénart"),
            "gulli-parc-conflans-sainte-honorine": _p("18 rue de l'Hautil", "78700", "Conflans-Sainte-Honorine",
                                                      "Gulli Parc Conflans"),
            "gulli-parc-la-ville-du-bois": _p("5 rue de la Croix Saint-Jacques", "91620", "La Ville-du-Bois",
                                              "Gulli Parc La Ville-du-Bois"),
            "gulli-parc-meaux": _p("71 rue Aristide Briand", "77124", "Villenoy", "Gulli Parc Meaux"),
            "gulli-parc-sainte-genevieve-des-bois": _p("20 avenue de la Croix Blanche", "91700",
                                                       "Sainte-Geneviève-des-Bois", "Gulli Parc Sainte-Geneviève"),
            # hors Île-de-France (réseau de 20 parcs, benchmark)
            "gulli-parc-aix-en-provence": _p("", "13290", "Aix-en-Provence", "Gulli Parc Aix-en-Provence"),
            "gulli-parc-audincourt": _p("", "25400", "Audincourt", "Gulli Parc Audincourt"),
            "gulli-parc-henin-beaumont": _p("", "62110", "Hénin-Beaumont", "Gulli Parc Hénin-Beaumont"),
            "gulli-parc-le-havre": _p("", "76600", "Le Havre", "Gulli Parc Le Havre"),
            "gulli-parc-le-mans": _p("", "72650", "Saint-Saturnin", "Gulli Parc Le Mans"),
            "gulli-parc-lomme": _p("", "59160", "Lomme", "Gulli Parc Lomme"),
            "gulli-parc-mulhouse": _p("", "68260", "Kingersheim", "Gulli Parc Mulhouse"),
            "gulli-parc-cap-malo": _p("", "35520", "La Mézière", "Gulli Parc Cap Malo (Rennes)"),
            "gulli-parc-cesson-sevigne": _p("", "35510", "Cesson-Sévigné", "Gulli Parc Cesson-Sévigné"),
            "gulli-parc-rouen": _p("", "76000", "Rouen", "Gulli Parc Rouen"),
            "gulli-parc-toulouse": _p("", "31650", "Saint-Orens-de-Gameville", "Gulli Parc Toulouse"),
            "gulli-parc-vendeville": _p("", "59175", "Vendeville", "Gulli Parc Vendeville"),
            "gulli-parc-wambrechies": _p("", "59118", "Wambrechies", "Gulli Parc Wambrechies"),
        }.items()},
    },
    "les-zacrocs": {
        "nom": "Les Zacroc's", "website": "https://www.leszacrocs.com", "kind": "legacy", "org": "la-boite-aux-enfants",
        "parcs": {"leszacrocs": {**_p("12 rue Paul Langevin", "78370", "Plaisir", "Les Zacroc's Plaisir"),
                                 "cat": "entrees-parc"}},
    },
    "you-kids": {
        "nom": "You Kids", "website": "https://you-kids.fr/montigny", "kind": "roller",
        # billetterie partagée avec le trampoline You Jump : on suit le seul produit You Kids
        "comptage": "presence",                      # jauge commune à tout le parc (290 à 640 places)
        "parcs": {
            "you-kids-sqy": {**_p("1 avenue de la Source de la Bièvre", "78180", "Montigny-le-Bretonneux",
                                  "You Kids SQY Ouest"),
                             "booking": "https://billetterie-montigny.you-kids.fr/shop/fr/home", "produit": "1366219"},
            "you-kids-plaisir": {**_p("2 route de Thiverval", "78850", "Thiverval-Grignon", "You Kids Plaisir"),
                                 "kind": "legacy", "org": "you-kids", "shop": "you-kids-thiverval",
                                 "cat": "entree-kids", "comptage": None},
            **{k: {**_p("", cp, ville, nom), "booking": f"https://ecom.roller.app/{v}/{sl}/fr/home", "produit": pr}
               for k, cp, ville, nom, v, sl, pr in [
                   ("you-kids-palaiseau", "91120", "Palaiseau", "You Kids Paris-Palaiseau",
                    "youjumpparispalaiseau", "youkids", "1352736"),
                   ("you-kids-avignon", "84000", "Avignon", "You Kids Avignon", "youjumpavignon", "youkids", "1351832"),
                   ("you-kids-merignac", "33700", "Mérignac", "You Kids Bordeaux-Mérignac",
                    "youjumpbordeauxmerignac", "youkids", "1352285"),
                   ("you-kids-caen", "14000", "Caen", "You Kids Caen", "youjumpcaen", "shop", "1370801"),
                   ("you-kids-lille", "59520", "Marquette-lez-Lille", "You Kids Lille-Marquette",
                    "youjumplille", "youkids", "1364866"),
                   ("you-kids-montpellier", "34970", "Lattes", "You Kids Montpellier-Lattes",
                    "youjumpmontpellierlattes", "youkids", "1368650"),
                   ("you-kids-nantes", "44800", "Saint-Herblain", "You Kids Nantes Atlantis",
                    "youjumpnantesatlantis", "shop", "1371289"),
                   ("you-kids-geispolsheim", "67118", "Geispolsheim", "You Kids Strasbourg-Geispolsheim",
                    "youjumpstrasbourggeispolsheim", "youkids", "1372297"),
                   ("you-kids-mundolsheim", "67450", "Mundolsheim", "You Kids Strasbourg-Mundolsheim",
                    "youjumpstrasbourgmundolsheim", "youkids", "1370301"),
                   ("you-kids-toulouse-montaudran", "31400", "Toulouse", "You Kids Toulouse-Montaudran",
                    "youjumptoulousemontaudran", "youkids", "1369728"),
                   ("you-kids-toulouse-sept-deniers", "31200", "Toulouse", "You Kids Toulouse-Sept-Deniers",
                    "youjumptoulouseseptdeniers", "youkids", "1296941"),
               ]},
        },
    },
    # Royal Kids : entrée libre partout... sauf Roissy-en-Brie, qui vend l'entrée datée (eshop-v3)
    "royal-kids": {
        "nom": "Royal Kids", "website": "https://royalkids.fr", "kind": "v3",
        "parcs": {"royal-kids-roissy": {**_p("", "77680", "Roissy-en-Brie", "Royal Kids Roissy-en-Brie"),
                                        "host": "resagames-rk.qweekle.shop",
                                        "produit": "9e8c537987be4f168fec123512475f74"}},
    },
    "planet-park": {
        "nom": "Planet Park", "website": "https://planet-park.com", "kind": "v3",
        "parcs": {"planet-park-emerainville": {**_p("", "77184", "Émerainville", "Planet Park Émerainville"),
                                               "host": "resagames-planetparkemerainville.qweekle.shop",
                                               "produit": "f3570c5e10444b3580e78852c4b6eec5"},
                  "planet-park-varennes": {**_p("", "77130", "Varennes-sur-Seine", "Planet Park Varennes"),
                                           "host": "resagames-planetparkvarennes.qweekle.shop"},
                  # Nantes : ancienne boutique, une seule activité (journée illimitée, ~160 par créneau).
                  # Cormeilles-en-Parisis, Corbeil, Vedène : e-billets non datés, non mesurables.
                  "planet-park-nantes": {**_p("5 rue de la Garde", "44300", "Nantes", "Planet Park Nantes"),
                                         "kind": "legacy", "org": "royal-kids-sainte-luce",
                                         "shop": "planet-park-nantes", "cat": "entrees-parc",
                                         "activite": "entree-journee-illimitee-12-eur",
                                         "prix": {"enfant": 12.0, "adulte": 2.0}}},
    },
    "kawatee": {
        "nom": "Kawatee", "website": "https://kawatee-kawatee.qweekle.shop", "kind": "v3",
        "parcs": {"kawatee": {**_p("", "91120", "Palaiseau", "Kawatee Palaiseau"),
                              "host": "kawatee-kawatee.qweekle.shop", "produit": "94aa2220d60846b49c58fa0dec6badf0"}},
    },
    # Mini-villes et parcs du benchmark hors Île-de-France
    "little-city": {
        "nom": "Little City (Big Little)", "website": "https://biglittle.qweekle.com/shop/biglittle", "kind": "legacy",
        "org": "biglittle",
        "parcs": {"little-city": {**_p("17 rue Jean Monnet", "68270", "Wittenheim", "Little City Wittenheim"),
                                  "shop": "biglittle", "cat": "little-city",
                                  "activite": "entree-little-city-jusqua-10-ans"}},
    },
    "chibitopia": {
        "nom": "Chibitopia", "website": "https://chibitopia.qweekle.com/shop/chibitopia", "kind": "legacy",
        "org": "chibitopia",
        "parcs": {"chibitopia": {**_p("", "67380", "Lingolsheim", "Chibitopia Lingolsheim"),
                                 "cat": "entrees-familles"}},
    },
    # Kidz World (Lyon 7e, mini-ville ouverte en avril 2026) : Wix Bookings, une séance de 2 h
    # par « bracelet », quota propre à chaque séance ; adultes payés sur place (non comptés)
    "kidz-world": {
        "nom": "Kidz World", "website": "https://www.kidzworldlyon.com", "kind": "wix", "comptage": "arrivees",
        "parcs": {"kidz-world-lyon": {**_p("11 rue du Sauveur", "69007", "Lyon", "Kidz World Lyon"),
                                      "site": "https://www.kidzworldlyon.com",
                                      "services": {"016ac2e9-59cd-4819-a956-835569f64848": 19.90}}},
    },
    "kizou-aventures": {
        "nom": "Kizou Aventures", "website": "https://kizou-aventures.fr", "kind": "legacy", "org": "kizou-clermont",
        "parcs": {"kizou-aventures-clermont-ferrand": {**_p("", "63000", "Clermont-Ferrand", "Kizou Aventures Clermont"),
                                                      "cat": "entrees-au-parc"}},
    },
    "quand-je-serai-grand": {
        "nom": "Quand je serai grand", "website": "https://quandjeseraigrand-parc.com", "kind": "legacy",
        "org": "terrakids",
        "parcs": {"quandjeseraigrand": {**_p("Rue du Kiosque, Grand Val", "95260", "Mours", "Quand je serai grand"),
                                        "cat": "entrees-parc"}},
    },
    "toumini-city": {
        "nom": "Toumini City", "website": "https://touminicity.com", "kind": "legacy", "org": "touminicity",
        "parcs": {"toumini-city": {**_p("52 avenue de la Commune de Paris", "91220", "Brétigny-sur-Orge",
                                        "Toumini City"), "cat": "entree-en-famille-ou-entre-amis"}},
    },
    "le-monde-d-ayden": {
        "nom": "Le Monde d'Ayden", "website": "https://www.lemondedayden.com", "kind": "legacy", "org": "lemondedayden",
        "parcs": {"asnieres": {**_p("Rue Henri Bergson", "92600", "Asnières-sur-Seine", "Le Monde d'Ayden Asnières"),
                               "cat": "entrees-2-heures"}},
    },
    "bulles-et-billes": {
        "nom": "Bulles & Billes", "website": "https://www.bullesetbilles.fr", "kind": "legacy", "org": "bullesetbilles",
        "parcs": {"bullesetbilles-paris9": {**_p("13 rue Sainte-Cécile", "75009", "Paris", "Bulles & Billes"),
                                           "cat": "entrees-chez-bulles-billes"}},
    },
    "petit-nuage": {
        "nom": "Petit Nuage", "website": "https://www.lepetitnuage.fr", "kind": "eventcal",
        "parcs": {"petit-nuage": {**_p("13 rue Simone Veil", "93400", "Saint-Ouen-sur-Seine", "Petit Nuage"),
                                  "calendrier": "14979", "widget": "d012cbaa-367a-4257-818e-7f283cbc4311"}},
    },
    "cite-des-enfants": {
        "nom": "Cité des enfants", "website": "https://www.cite-sciences.fr/fr/au-programme/expos-permanentes/cite-des-enfants",
        "kind": "cite",
        "parcs": {"cite-des-enfants-2-6": {**_p("30 avenue Corentin Cariou", "75019", "Paris",
                                                "Cité des enfants · 2-6 ans"), "offre": "23"},
                  "cite-des-enfants-5-10": {**_p("30 avenue Corentin Cariou", "75019", "Paris",
                                                 "Cité des enfants · 5-10 ans"), "offre": "24"}},
    },
    "ile-de-la-tortuga": {
        "nom": "Île de la Tortuga", "website": "https://paris.sevensquares.fr/kids-park-paris/", "kind": "apex",
        "parcs": {"ile-de-la-tortuga": {**_p("30 avenue Corentin Cariou (Boom Boom Villette)", "75019", "Paris",
                                             "Île de la Tortuga (Seven Squares)"), "centre": "623", "produit": "441"}},
    },
    "revopolie": {
        "nom": "Rêvopolie", "website": "https://revopolie-parc.qweekle.shop", "kind": "v3",
        # « available » = places libres dans le parc à l'heure dite (130 au plus), pas des arrivées
        "parcs": {"revopolie-parc.qweekle.shop": {**_p("25 rue Desnouettes", "75015", "Paris", "Rêvopolie"),
                                                  "comptage": "presence"}},
    },
}

# Choix des produits : la catégorie « entrées famille » du parc et, dedans, l'entrée
# enfant standard (les variantes restreintes — after school, tout-petits, CSE… —
# n'ont pas tous les créneaux).
CAT_EXCLUE = re.compile(r"assistante|ecole|groupe|anniv|privat|atelier|bouh|ween|boum|boom|spectacle|"
                        r"sharks|marionnette|giftcard|ticketing|member|booking|cart|general", re.I)
CAT_PARC = re.compile(r"acces-parc|entrees?-au-parc|viens-jouer|billets-parc|entree", re.I)
ENTREE_EXCLUE = re.compile(r"after|b[ée]b[ée]|baby|bout.?chou|1 (an )?à 2 ans|12 à 2[34] mois|pichoun|minot|bambin|"
                           r"handicap|pmr|extra.?care|cse|pr[ée]pay|papi|palo-?pass|mini|p.tit|assist|suppl|abonn|"
                           r"scolaire|privat|atelier|anniv|gratuit|groupe|encas|halloween|soir[ée]e|nombreuse|"
                           r"non accompagn|-20 ?mois|20 ?mois à", re.I)


def choisir_entrees(produits: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """(id entrée enfant, id entrée adulte) parmi [(id, libellé)]."""
    ok = [(i, n) for i, n in produits if not ENTREE_EXCLUE.search(n)]
    enfant = next((i for i, n in ok if re.search(r"enfant|junior|\bkids?\b|acc[eè]s parc", n, re.I)), None)
    adulte = next((i for i, n in ok if re.search(r"adulte|accompagnat", n, re.I)), None)
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
        self.jar = http.cookiejar.CookieJar()
        self.op = build_opener(HTTPCookieProcessor(self.jar))
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
        self.single = False
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
        # prix « à partir de » de chaque entrée, quand les créneaux n'affichent pas de prix
        self.prix_base = {}
        for bloc in h.split("multi_activity-item")[1:]:
            ida = re.search(r'name="activities_qty\[([^\]]+)\]"', bloc)
            px = re.search(r"activity-price.*?([\d.,]+)\s*&euro;", bloc, re.S)
            if ida and px:
                self.prix_base[ida.group(1)] = _num(px.group(1))
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
        if self.single:                              # page « une activité » (Little City)
            url = self.base + f"{self.cat}/{act}/data?lang=fr&" + up.urlencode(
                {"start_at": jour.strftime("%d/%m/%Y"), "qty": qty})
        else:
            q = [("_token", self.tok), ("start_at", jour.strftime("%d/%m/%Y"))]
            q += [(f"activities_qty[{a}]", str(qty) if a == act else "0") for a, _ in self.acts]
            url = self.base + f"multi/{self.cat}/data?lang=fr&" + up.urlencode(q)
        for essai in range(3):                       # page d'erreur passagère : on retente
            st, d = self.c.get(url, headers={"X-Requested-With": "XMLHttpRequest"})
            if st == 200 and ("activity-slots" in d or "no-slots-available" in d or "update_date" in d):
                break
            time.sleep(3 * (essai + 1))
        else:
            return None
        d = re.sub(r"\s+", " ", d)
        slots = {}
        self.affiches = {}                            # « N Places disponibles », quand la boutique l'affiche
        motif = (r'<a href="[^"]*/(\d{4}-\d\d-\d\d_\d\d:\d\d)\?qty=[^"]*"[^>]*>(.*?)</a>' if self.single
                 else r'data-slot_url="[^"]*/(\d{4}-\d\d-\d\d_\d\d:\d\d)/[^"]*"[^>]*>(.*?)</a>')
        for dt, inner in re.findall(motif, d):
            k = dt.replace("_", " ")
            pm = re.search(r"([\d.,\s]+)\s*&euro;", inner)
            slots[k] = _num(pm.group(1)) if pm else None
            pr = re.search(r"(\d+)\s*Places? disponibles?", inner, re.I)
            if pr:
                self.affiches[k] = int(pr.group(1))
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
        aff = getattr(self, "affiches", {})
        if base and all(k in aff for k in base):     # la boutique affiche les places : rien à deviner
            return {k: aff[k] for k in base}
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


def releve_legacy(org: str, parc: str, st: dict, jours: int, cat: str | None = None,
                  shop: str | None = None, activite: str | None = None) -> dict:
    """-> {"slots": {'YYYY-MM-DD HH:MM': {...}}, "couverts": set(dates), "cat":…, "req": n}
    `shop` : identifiant de la boutique s'il diffère de l'identifiant du parc ;
    `activite` : boutique à page « une activité » (sans page multi-activités)."""
    meta = st.setdefault("_meta", {}).setdefault("parcs", {}).setdefault(parc, {})
    L = Legacy(org, shop or parc, meta.get("cat") or cat)
    if activite:
        L.single, L.acts = True, [(activite, "Entrée enfant")]
    elif not L.ouvrir():
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
        pb = getattr(L, "prix_base", {})
        for k, px in base.items():
            out[k] = {"rest": rest.get(k, 0), "enfant": px if px is not None else pb.get(enfant),
                      "adulte": px_ad.get(k) if px_ad.get(k) is not None else pb.get(adulte)}
        jour = date.fromisoformat(dates[-1]) + timedelta(days=1)
    meta.update({"cat": L.cat, "entree": dict(L.acts).get(enfant), "requetes": L.c.n,
                 "mode": "complet" if complet else "leger"})
    return {"slots": out, "couverts": couverts, "source": L.base + L.cat, "req": L.c.n,
            "duree": _duree(dict(L.acts).get(enfant) or "")}


def _duree(nom: str) -> int | None:
    m = re.search(r"(\d+)\s*h", nom)
    return int(m.group(1)) * 60 if m else None


# ───────────────────────────── eshop-v3 (JSON) ─────────────────────────────
def releve_v3(host: str, st: dict, jours: int, produit: str | None = None) -> dict:
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
    if produit:                                           # entrée imposée par la configuration
        enfant = next((i for i, _ in cand if produit in i), produit)
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


# ───────────────────────────── ROLLER (api.roller.app) ─────────────────────────────
def releve_roller(booking_url: str, st: dict, jours: int, produit: str | None = None) -> dict:
    """Billetterie ROLLER (Palomano Clichy et Alfortville, You Kids…) : API publique du
    widget de réservation. Clé du lieu : cookie « Current-Venue » posé par la page de
    réservation (domaine propre) ou 1er segment du chemin sur ecom.roller.app ; le
    segment suivant est le « checkout slug ». /product-availability?date= renvoie, par
    produit, les sessions du jour avec capacityRemaining (places encore réservables).
    `produit` : id du produit à suivre quand le lieu en vend plusieurs (ex. You Kids /
    You Jump partagent la même billetterie)."""
    c = Client()
    u = up.urlparse(booking_url)
    segs = [x for x in u.path.split("/") if x]
    if u.netloc == "ecom.roller.app" and len(segs) >= 2:
        venue, slug = segs[0], segs[1]
    else:
        st_code, _ = c.get(booking_url)
        if st_code != 200:
            raise IOError(f"page de réservation HTTP {st_code}")
        venue = next((ck.value for ck in c.jar if ck.name == "Current-Venue"), None)
        slug = segs[0] if segs else None
    if not (venue and slug):
        raise IOError("clé de lieu ROLLER introuvable")
    H = {"Accept": "application/json", "X-Api-Key": venue, "X-Checkout-Slug": slug}
    # L'API route chaque lieu vers une « cellule » : la 1re requête répond 302 + cookie
    # « Cell » en perdant les paramètres ; on amorce, puis on passe la cellule à chaque appel.
    c.get(f"https://api.roller.app/api/checkout/{slug}/availability?days=1", headers=H)
    cell = next((ck.value for ck in c.jar if ck.name == "Cell"), None)
    if cell:
        H["X-Cell-Id"] = cell

    def J(path: str):
        path += ("&" if "?" in path else "?") + f"Cell={cell}" if cell else ""
        s, b = c.get(f"https://api.roller.app/api/checkout/{slug}/{path}", headers=H)
        if s != 200:
            raise IOError(f"{path.split('?')[0]} HTTP {s}")
        return json.loads(b)

    today = paris_now().date()
    out, couverts, duree, src = {}, set(), None, None
    for i in range(jours):
        d = today + timedelta(days=i)
        prods = J(f"product-availability?date={d.isoformat()}")
        couverts.add(d.isoformat())
        # produit « entrée au parc » : un pass de session dont les variantes sont les entrées
        if produit:
            cands = [p for p in prods if produit in (str(p.get("id")), str(p.get("parentProductId")))
                     and p.get("sessions")]
        else:
            cands = [p for p in prods if p.get("type") in ("sessionpass", "session") and p.get("sessions")
                     and not ENTREE_EXCLUE.search(p.get("name") or "")]
        if not cands:
            continue
        prod = max(cands, key=lambda p: len(p["sessions"]))
        variantes = [(v["id"], v.get("name") or "") for v in prod.get("products") or []]
        enfant, adulte = choisir_entrees(variantes)
        cout = {v["id"]: v.get("cost") for v in prod.get("products") or []}
        src = src or prod.get("name")
        for s in prod["sessions"]:
            if not s.get("startTime"):
                continue
            k = f"{s.get('date') or d.isoformat()} {s['startTime'][:5]}"
            if s.get("endTime") and duree is None:
                h1, m1 = map(int, s["startTime"][:5].split(":"))
                h2, m2 = map(int, s["endTime"][:5].split(":"))
                duree = (h2 * 60 + m2) - (h1 * 60 + m1) or None
            rest = max(0, int(s.get("capacityRemaining") or 0))
            out[k] = {"rest": rest, "reservable": bool(s.get("onlineSalesOpen", True)) and rest > 0,
                      "enfant": cout.get(enfant), "adulte": cout.get(adulte)}
    return {"slots": out, "couverts": couverts, "source": booking_url, "req": c.n, "duree": duree}


# ───────────────────────── autres billetteries (API JSON) ─────────────────────────
def releve_cite(offre: str, st: dict, jours: int) -> dict:
    """Cité des enfants (Cité des sciences) : billetterie maison, API JSON publique.
    /api/offers/<offre>/shows.json?filter[date]= -> séances avec availableQuantity."""
    c = Client()
    H = {"Accept": "application/json"}
    s, b = c.get(f"https://billetterie.cite-sciences.fr/api/offers/{offre}/prices.json", headers=H)
    tarifs = {}
    if s == 200:
        for g in (json.loads(b).get("data") or {}).get("data") or []:
            for t in (g.get("prices") or {}).get("data") or []:
                tarifs[(t.get("title") or "").lower()] = t.get("price")
    plein = tarifs.get("plein tarif")
    enfant = next((v for k, v in tarifs.items() if "enfant" in k or "2-17" in k), plein)
    today = paris_now().date()
    out, couverts, duree = {}, set(), None
    for i in range(jours):
        d = (today + timedelta(days=i)).isoformat()
        s, b = c.get(f"https://billetterie.cite-sciences.fr/api/offers/{offre}/shows.json?filter[date]={d}", headers=H)
        if s != 200:
            time.sleep(5)                                 # « Service indisponible » si trop rapide
            s, b = c.get(f"https://billetterie.cite-sciences.fr/api/offers/{offre}/shows.json?filter[date]={d}",
                         headers=H)
            if s != 200:
                raise IOError(f"shows {d} HTTP {s}")
        couverts.add(d)
        for sh in ((json.loads(b).get("data") or {}).get("shows") or {}).get("data") or []:
            t0 = datetime.fromisoformat(sh["dateStart"])
            if sh.get("dateEnd") and duree is None:
                duree = int((datetime.fromisoformat(sh["dateEnd"]) - t0).total_seconds() // 60)
            rest = max(0, int(sh.get("availableQuantity") or 0))
            out[t0.strftime("%Y-%m-%d %H:%M")] = {
                "rest": rest, "reservable": bool(sh.get("isSalesOpen")) and bool(sh.get("isInStock")) and rest > 0,
                "enfant": enfant, "adulte": plein}
        time.sleep(1)
    return {"slots": out, "couverts": couverts, "source": "https://billetterie.cite-sciences.fr",
            "req": c.n, "duree": duree}


def releve_eventcal(cal: str, widget: str, st: dict, jours: int) -> dict:
    """Event Calendar App (Petit Nuage) : liste paginée des séances, avec ticketsRemaining."""
    c = Client()
    today = paris_now().date()
    fin = (today + timedelta(days=jours - 1)).isoformat()
    out, couverts, duree = {}, set(), None
    url = f"https://api.eventcalendarapp.com/events?id={cal}&widgetUuid={widget}&page=1"
    for _ in range(40):
        s, b = c.get(url, headers={"Accept": "application/json"})
        if s != 200:
            raise IOError(f"events HTTP {s}")
        d = json.loads(b)
        evs = d.get("events") or []
        for e in evs:
            k = (e.get("timezoneStart") or "")[:16].replace("T", " ")
            if not k or k[:10] > fin:
                continue
            couverts.add(k[:10])
            if e.get("timezoneEnd") and duree is None:
                duree = int((datetime.fromisoformat(e["timezoneEnd"]) - datetime.fromisoformat(e["timezoneStart"]))
                            .total_seconds() // 60)
            prix = {}
            for t in e.get("ticketTypes") or []:
                n, v = (t.get("name") or "").lower(), t.get("price")
                if v is None:
                    continue
                if "adulte" in n:
                    prix.setdefault("adulte", float(v))
                elif "enfant" in n and "moins" not in n:
                    prix.setdefault("enfant", float(v))
            if not prix:                                  # tarifs seulement dans le texte
                m = re.findall(r"enfant\s*:\s*([\d,\.]+)|adulte\s*:\s*([\d,\.]+)", e.get("shortDescription") or "", re.I)
                for a, b2 in m:
                    if a and "enfant" not in prix:
                        prix["enfant"] = _num(a)
                    if b2 and "adulte" not in prix:
                        prix["adulte"] = _num(b2)
            rest = max(0, int(e.get("ticketsRemaining") or 0))
            out[k] = {"rest": rest, "reservable": not e.get("soldOut") and rest > 0, **prix}
        dernier = max(((e.get("timezoneStart") or "")[:10] for e in evs), default="")
        nxt = (d.get("pages") or {}).get("nextPage")
        if not evs or not nxt or dernier > fin:
            break
        url = nxt
    return {"slots": out, "couverts": couverts, "source": "https://www.lepetitnuage.fr/aire-de-jeu",
            "req": c.n, "duree": duree}


def releve_apex(centre: str, produit: str, st: dict, jours: int) -> dict:
    """Apex Timing (Île de la Tortuga, Seven Squares) : le module de réservation ne répond
    qu'en POST (simple recherche des séances, aucune réservation) -> places_available."""
    c = Client()
    today = paris_now().date()
    out, couverts = {}, set()
    for i in range(jours):
        d = today + timedelta(days=i)
        data = up.urlencode({"type": "load_schedules", "center_id": centre, "product_id": produit,
                             "date": d.strftime("%Y/%m/%d"), "people_number": "1", "kart_id": ""}).encode()
        r = None
        for essai in range(3):
            try:
                r = c.op.open(Request("https://www.apex-timing.com/gokarts/functions/request_session_booking.php",
                                      data=data, headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                                                          "Content-Type": "application/x-www-form-urlencoded"}),
                              timeout=45).read().decode("utf-8", "replace")
                c.n += 1
                break
            except Exception:
                time.sleep(3 * (essai + 1))
        if r is None:
            raise IOError(f"séances {d} injoignables")
        txt = r.strip()
        sl = json.loads(txt[txt.find("["):]) if "[" in txt else []
        couverts.add(d.isoformat())
        for x in sl:
            k = f"{x['date'].replace('/', '-')} {x['start_hour'][:5]}"
            rest = max(0, int(x.get("places_available") or 0))
            out[k] = {"rest": rest, "reservable": rest > 0, "enfant": _num(str(x.get("price") or "")) if x.get("price") else None}
    return {"slots": out, "couverts": couverts, "source": "https://paris.sevensquares.fr/reservation-activite/",
            "req": c.n, "duree": None}


# ───────────────────────────── Wix Bookings ─────────────────────────────
WIX_BOOKINGS = "13d21c63-b5ec-5912-8397-c3a5ddb27a97"


def releve_wix(site: str, services: dict, st: dict, jours: int) -> dict:
    """Wix Bookings (Kidz World Lyon) : jeton public de l'application Bookings
    (/_api/v1/access-tokens), puis recherche des séances (POST, aucune réservation) :
    remainingCapacity / totalCapacity par séance, séances complètes comprises.
    `services` : {id du service: prix enfant}. Deux séances à la même heure : additionnées."""
    c = Client()
    s, b = c.get(f"{site}/_api/v1/access-tokens", headers={"Accept": "application/json"})
    if s != 200:
        raise IOError(f"jeton Wix HTTP {s}")
    tok = json.loads(b)["apps"][WIX_BOOKINGS]["instance"]
    today = paris_now().date()
    fin = today + timedelta(days=jours)
    body = json.dumps({"serviceIds": list(services), "fromLocalDate": f"{today}T00:00:00",
                       "toLocalDate": f"{fin}T00:00:00", "timeZone": "Europe/Paris",
                       "includeNonBookable": True, "cursorPaging": {"limit": 1000}}).encode()
    d = None
    for essai in range(3):
        try:
            c.n += 1
            d = json.loads(c.op.open(Request(f"{site}/_api/service-availability/v2/time-slots/event", data=body,
                                             headers={"User-Agent": UA, "Authorization": tok,
                                                      "Content-Type": "application/json",
                                                      "Accept": "application/json"}), timeout=60).read())
            break
        except Exception:
            time.sleep(3 * (essai + 1))
    if d is None:
        raise IOError("séances Wix injoignables")
    out, duree = {}, None
    for t in d.get("timeSlots") or []:
        k = t["localStartDate"][:16].replace("T", " ")
        if duree is None and t.get("localEndDate"):
            h1, m1 = map(int, t["localStartDate"][11:16].split(":"))
            h2, m2 = map(int, t["localEndDate"][11:16].split(":"))
            duree = (h2 * 60 + m2) - (h1 * 60 + m1)
        o = out.setdefault(k, {"rest": 0, "cap": 0, "reservable": False, "enfant": services.get(t["serviceId"])})
        o["rest"] += max(0, int(t.get("remainingCapacity") or 0))
        o["cap"] += int(t.get("totalCapacity") or 0)
        o["reservable"] |= bool(t.get("bookable"))
    return {"slots": out, "couverts": {(today + timedelta(days=i)).isoformat() for i in range(jours)},
            "source": site, "req": c.n, "duree": duree}


# ───────────────────────────── fusion dans le store ─────────────────────────────
def _comptage(slots: dict, jauge: int | None) -> str:
    """« presence » si la billetterie applique la jauge du PARC sur toute la visite : les
    places libres sont alors identiques sur plusieurs créneaux consécutifs (ex. Gulli Parc :
    323, 323, 323, 323, 349…). Sinon chaque créneau a son propre quota d'arrivées
    (Palomano : 30 par créneau de 20 min) : « arrivees »."""
    egaux = paires = 0
    par_jour: dict[str, list] = {}
    for k, o in sorted(slots.items()):
        par_jour.setdefault(k[:10], []).append(o["rest"])
    for v in par_jour.values():
        for a, b in zip(v, v[1:]):
            if jauge and min(a, b) >= jauge:          # deux créneaux vides : non informatif
                continue
            paires += 1
            egaux += a == b
    return "presence" if paires >= 10 and egaux / paires >= 0.4 else "arrivees"


def _pas(slots: dict) -> int:
    """Écart habituel (minutes) entre deux créneaux d'un même jour (20 ou 30 en général)."""
    ecarts = Counter()
    par_jour: dict[str, list[int]] = {}
    for k in slots:
        h, m = map(int, k[11:16].split(":"))
        par_jour.setdefault(k[:10], []).append(h * 60 + m)
    for v in par_jour.values():
        v.sort()
        ecarts.update(b - a for a, b in zip(v, v[1:]) if b > a)
    return ecarts.most_common(1)[0][0] if ecarts else 30


def jauge_habituelle(S: dict, rid: str, pnow_s: str) -> int | None:
    """Jauge habituelle d'un créneau du parc, quand la billetterie ne la publie pas : la
    valeur la plus fréquente (si elle concerne ≥ 30 % des créneaux), sinon le 75e centile,
    des plus hauts niveaux de places libres vus sur les créneaux à J+7 et au-delà. Loin dans le futur, un créneau est presque vide, donc proche de sa jauge ;
    près de la date, les parcs pleins n'affichent que « 1 place restante » (Quand je serai
    grand, Toumini City en vacances). Le centile écarte aussi les rares créneaux à jauge
    élargie (matinées de vacances Palomano). Ex. : Octopia 70, Palomano Montrouge 30."""
    loin = (datetime.strptime(pnow_s[:10], "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
    tous = [(ex["date"], ex["places_max_vu"]) for ex in S.values()
            if ex.get("room_id") == rid and (ex.get("places_max_vu") or 0) > 0
            and f"{ex['date']} {ex['heure']}" > pnow_s]
    vals = sorted(v for d, v in tous if d >= loin)
    if len(vals) < 20:
        vals = sorted(v for _, v in tous)
    if not vals:
        return None
    v, n = Counter(vals).most_common(1)[0]
    if v > 1 and n >= 0.3 * len(vals):              # une jauge nette (Palomano Bordeaux : 30, pas 40)
        return v
    return vals[int(0.75 * (len(vals) - 1))]


def fusion(st: dict, rid: str, obs: dict, kind: str) -> int:
    """Met à jour les sessions du parc `rid` à partir d'un relevé.
    - créneau commencé : figé (statut final reserve / libre_fin) ;
    - ancienne boutique : un créneau connu qui disparaît (jour couvert, début > 90 min)
      n'a plus de place -> 0 restante, complet ;
    - eshop-v3 / ROLLER : les créneaux complets restent listés -> un créneau qui
      disparaît a été retiré.
    Capacité de référence (places_init) : jauge publiée (eshop-v3) ; sinon le plus haut
    entre les places libres jamais vues sur ce créneau et la jauge habituelle du parc
    (certains créneaux ont une jauge plus large, ex. matinées de vacances)."""
    legacy = kind == "legacy"
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
    jauge = jauge_habituelle(S, rid, pnow_s) if kind not in ("v3", "wix") else None   # jauge non publiée
    # Présences -> entrées : une visite de D minutes occupe D/pas créneaux consécutifs ; sommer
    # les places prises de chaque créneau compterait chaque visiteur D/pas fois.
    comptage = obs.get("comptage") or ("presence" if kind == "v3" else _comptage(obs["slots"], jauge))
    obs["comptage"] = comptage
    f = 1.0
    if comptage == "presence":
        f = min(1.0, _pas(obs["slots"]) / (obs.get("duree") or 120))
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
            # places_init / places_vendues en ENTRÉES (comparables entre parcs, base du CA) ;
            # places_max garde la jauge réelle, places_restantes les places réellement libres
            ex.update({"places_max": ref, "nb_joueurs_max": ref, "comptage": obs.get("comptage"),
                       "places_init": int(ref * f + 0.5), "places_vendues": int((ref - rest) * f + 0.5)})
        if avant != (ex.get("statut"), ex.get("dispo"), ex.get("places_restantes"),
                     ex.get("places_init")) or ex.get("releve") == now:
            ex["maj"] = now
    return n


def releve_enseigne(eid: str, jours: int, seul: str | None = None) -> tuple[str, str, int]:
    cfg = ENSEIGNES[eid]
    parcs = [p for p in cfg["parcs"] if not seul or p == seul]
    path = f"{OBS_DIR}/parc__{eid}.json"
    ancien = f"{OBS_DIR}/qweekle__{eid}.json"          # nom d'avant la généralisation
    if not os.path.exists(path) and os.path.exists(ancien):
        os.replace(ancien, path)
    st = read_json(path, {}) or {}
    st.update({"enseigne_id": eid, "enseigne_nom": cfg["nom"], "website": cfg["website"],
               "plateforme": "qweekle", "secteur": "parc"})
    st.setdefault("sessions", {})
    rooms = st.setdefault("rooms", {})
    connus = {f"qw-{p}" for p in cfg["parcs"]}
    st["centres"] = {k: v for k, v in (st.get("centres") or {}).items() if k in connus}
    meta = st.setdefault("_meta", {})
    t0, debut, n_total, erreurs, req = time.time(), now_iso(), 0, [], 0

    def un(parc: str):
        info = cfg["parcs"][parc]
        kind = info.get("kind", cfg.get("kind"))
        try:
            if kind == "legacy":
                obs = releve_legacy(info.get("org", cfg.get("org")), parc, st, jours,
                                    info.get("cat"), info.get("shop"), info.get("activite"))
            elif kind == "v3":
                obs = releve_v3(info.get("host", parc), st, jours, info.get("produit"))
            elif kind == "roller":
                obs = releve_roller(info["booking"], st, jours, info.get("produit"))
            elif kind == "cite":
                obs = releve_cite(info["offre"], st, jours)
            elif kind == "eventcal":
                obs = releve_eventcal(info["calendrier"], info["widget"], st, jours)
            elif kind == "apex":
                obs = releve_apex(info["centre"], info["produit"], st, jours)
            elif kind == "wix":
                obs = releve_wix(info["site"], info["services"], st, jours)
            else:
                raise ValueError(f"plateforme inconnue : {kind}")
            obs["kind"] = kind
            obs["comptage"] = info.get("comptage", cfg.get("comptage"))   # sinon détecté à la fusion
            for o in obs["slots"].values():               # prix non affichés par la billetterie : grille du parc
                for k2, v in (info.get("prix") or {}).items():
                    if o.get(k2) is None:
                        o[k2] = v
            return parc, obs, None
        except Exception as e:                            # un parc en échec n'arrête pas les autres
            return parc, None, f"{parc}: {e}"[:160]

    with ThreadPoolExecutor(WORKERS) as pool:
        res = list(pool.map(un, parcs))
    for parc, obs, err in res:
        info = cfg["parcs"][parc]
        cid, rid = f"qw-{parc}", f"qw-{parc}-parc"
        st["centres"][cid] = {k: info[k] for k in ("nom", "adresse", "cp", "ville")}
        if err:
            erreurs.append(err)
            continue
        n_total += fusion(st, rid, obs, obs["kind"])
        caps = [s.get("places_init") or 0 for s in st["sessions"].values() if s.get("room_id") == rid]
        meta.setdefault("parcs", {}).setdefault(parc, {})["comptage"] = obs.get("comptage")
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


def scrape(jours: int = HORIZON_JOURS, only: str | None = None, parc: str | None = None) -> int:
    t0 = time.time()
    ids = [e for e in ENSEIGNES if not only or e == only]
    with ThreadPoolExecutor(len(ids)) as pool:            # hôtes différents : en parallèle
        res = list(pool.map(lambda e: releve_enseigne(e, jours, parc), ids))
    ko = 0
    for eid, statut, n in res:
        if statut != "ok":
            ko += 1
            m = (read_json(f"{OBS_DIR}/parc__{eid}.json", {}) or {}).get("_meta", {})
            print(f"[parcs] ⚠ {eid}: {statut} — {m.get('erreur', '')}")
        else:
            print(f"[parcs] {eid}: {n} créneaux")
    print(f"[parcs] {len(res) - ko}/{len(res)} enseignes OK | {time.time() - t0:.0f}s")
    return ko


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=HORIZON_JOURS)
    ap.add_argument("--only", choices=sorted(ENSEIGNES))
    ap.add_argument("--parc", help="un seul parc (mise au point)")
    a = ap.parse_args()
    scrape(a.days, a.only, a.parc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
