# Analyse scraping — état réel & stratégie

_Run d'investigation live du 2026-06-18. Données réelles, pas de démo._

## TL;DR
- **Découverte majeure** : `escapegame.fr` est la façade d'une plateforme de
  résa **4escape.io**. Une API publique donne la dispo réelle :
  `GET https://availability.4escape.io/egfr/upcoming/<company>/<roomId>`.
- **56 salles / 16 enseignes** parisiennes scrapées **en réel** dès le 1er run,
  **100 % de réponse**, toutes sur 4escape (dont **The Game, Lock Academy,
  Phobia, Deep Inside, The One Escape…**).
- **Limite** : l'API ne renvoie que le **prochain créneau libre** (pas tout le
  calendrier) → on reconstruit la demande par **observation répétée** (le coeur
  de l'archi). On **maximise l'historique** en append-only à chaque run.
- **Ce qui bloque** : (a) la majorité des URLs de mon catalogue v1 étaient des
  **devinettes DNS fausses** ; (b) les sites "propres" sont des **SPA/widgets
  hétérogènes** sans endpoint commun. → On **pivote sur 4escape comme source
  primaire**, et on garde le scraping par-site pour les **non-référencés**.

---

## 1. Ce qui bloque (probe live des 28 domaines du catalogue v1)

| Cas | Nb | Détail |
|---|---|---|
| ❌ DNS inexistant | 18 | URLs devinées fausses (`tikla.fr`, `questroom.fr`, `komnataquest.fr`, `hoplastudios.com`, `weescape.fr`…). **Action : ne jamais deviner une URL — l'extraire d'un annuaire.** |
| ⚠️ 503 / anti-bot | 3 | `misterroom.fr`, `lechantdessirenes.fr`, `backrooms.fr` (Cloudflare/WAF probable). |
| ✅ Répond 200 | 7 | `thegame-france.com`, `escapeyourself.fr`, `hinthunt.fr`, `liveescapegame.fr`, `getout.fr`, `mindtrap.fr`, `insideopera.com`. |

**Conclusion blocage** : le vrai blocage n'est pas technique mais **la qualité
du catalogue**. La solution est l'annuaire escapegame.fr (URLs + identifiants
fiables) + l'API 4escape.

## 2. Ce qui est OK (réel, scrapé ce run)

56 salles, 16 enseignes, **toutes répondent**, plateforme = **4escape** :

| Enseigne | Salles | Enseigne | Salles |
|---|---|---|---|
| The Game | 12 | Wanderlust | 3 |
| Lock Academy | 6 | Secrets Hunters | 3 |
| Phobia | 5 | Pandore & Associés | 2 |
| Majestic Escape Game | 4 | Masterio | 2 |
| Crack The Egg | 4 | Joueurz | 2 |
| Deep Inside | 3 | L'Antichambre | 2 |
| The One Escape | 3 | Live Cinema | 1 |
| The Quest Factory | 3 | Unleash Escape | 1 |

→ `escape_4escape_catalog.json` (catalogue réel) + `escape_4escape_live.json`
(instantané : prochain créneau libre, lead_days = délai = proxy de demande).

## 3. Faut-il adapter notre façon de faire ? — OUI

**Pivot : 4escape = source PRIMAIRE.** Raisons :
1. Une seule API couvre les **grandes enseignes** d'un coup (pas 30 scrapers).
2. Données **structurées & fiables** (room_id, nom, thème, joueurs, niveaux).
3. Le `book-url` révèle la **plateforme sous-jacente** de chaque enseigne.

**Adaptation du modèle d'occupation.** L'API ne donne que le *prochain* créneau,
donc on ne peut pas mesurer le taux d'occupation slot-par-slot du jour 1. On
mesure à la place, **en temps réel** :
- `lead_days` = jours jusqu'au prochain créneau libre → **proxy de demande**
  (ex. ce run : Wanderlust "Super sauvetage" = **19 j**, Deep Inside = 4 j).
- `available_now` = dispo le jour même (oui/non).

Et **avec l'accumulation** (le workflow /30 min) :
- disparition du "prochain créneau" entre 2 relevés ⇒ **réservation détectée**
  (`event: prev_slot_reserved`) → on reconstruit l'occupation réelle, comme
  prévu dans l'archi "disparition de créneau".
- tendance du `lead_days` sur 6 mois → pression de la demande par enseigne.

## 4. Historique max (demande explicite)

- Stockage **append-only** : `escape_data/4escape/<company>.json`, une
  `observation` ajoutée à chaque salle à **chaque run**, jamais écrasée.
- On ne peut pas *backfiller* le passé (pas d'API historique), mais on capture
  **tout** dès maintenant et on ne prune pas avant 90 j (puis archive Supabase).
- Schéma observation prêt pour Supabase (table `observation(room_id, releve,
  available, next_date, next_time, lead_days, platform, event)`).

## 5. Multi-site & multi-salles par enseigne

- **Multi-salles** : géré nativement — la clé est `room_id = <company>/<mongoid>`,
  chaque enseigne a N salles (The Game = 12, Lock Academy = 6…). Le catalogue
  est indexé par salle, regroupé par `company`.
- **Multi-sites** : une enseigne peut avoir plusieurs adresses/villes. Le
  catalogue agrège `company.cities[]` (multi-villes) et chaque salle porte sa
  `city`. À surveiller : certaines chaînes ont une `company` distincte par
  établissement (ex. `lockacademy-paris`) — à fusionner via une table de
  **mapping enseigne → companies[]** (à enrichir : `escape_brand_map.json`, TODO).

## 6. Salles NON référencées sur escapegame.fr (demande explicite)

escapegame.fr n'expose la dispo live **que pour les venues 4escape**. Sont donc
**hors radar** et nécessitent un **scraper par-site dédié** :
- Enseignes du catalogue v1 absentes de la liste 4escape : `HintHunt`,
  `Get Out`, `Mind Trap`, `Inside Opera`, `Live Escape Game`, `Mister Room`,
  `Le Chant des Sirènes`, `Escape Yourself`… + tous les **indés banlieue**.
- Plateformes détectées sur leurs sites (track-2, voir §7) → handlers à écrire.
- **TODO** : croiser le catalogue 4escape avec `escape_extension_brands.json`
  pour produire la liste "à scraper en propre" (champ `covered_by_4escape:
  true/false`).

## 7. Sites de chaque enseigne (track-2, demande explicite)

Investigation des sites qui répondent :

| Site | Plateforme détectée | Scraping |
|---|---|---|
| `escapeyourself.fr` | **4escape + Bookeo** | partiellement via 4escape ; sinon API Bookeo. |
| `thegame-france.com` | **SPA Astro + 4escape** (catalog UUID `00ce79e0-…`) | ✅ déjà couvert par l'API 4escape. |
| `mindtrap.fr` | **WooCommerce / WordPress** | produits = créneaux ; API Store WC. |
| `getout.fr` | **WordPress** | calendrier custom à inspecter (XHR). |
| `hinthunt.fr` / `liveescapegame.fr` / `insideopera.com` | opaque (pas de signature) | inspection XHR manuelle / fallback Playwright. |

**Constat** : les sites "propres" sont **hétérogènes** (SPA, WooCommerce, widgets
maison). ROI faible vs 4escape. Stratégie : **4escape d'abord** (gros volume),
puis handlers par-site **ciblés** pour les indés à valeur (un par plateforme :
Bookeo, WooCommerce, puis Playwright pour le reste).

## MAJ — investigation des pages de résa propres (4escape booking)

Chaque enseigne 4escape a une page de résa `<company>.4escape.io` qui appelle
en **POST** `/booking-data-json` avec `{UID, date, viewDuration (1-14)}`.

**Ce que ça donne (réel, dès le 1er run) :**
- ✅ **PRIX** : grille complète par nb de joueurs (`prices[n].amount_charged`,
  en centimes). Ex. Lock Academy 55€→30€/pers (2→6 j), Majestic 60→32€,
  Deep Inside 150→240€ (forfait groupe). ⚠️ le champ `mode` distingue
  *per-player* / forfait — à normaliser (certaines grilles sont par groupe).
- ✅ **Planning théorique complet** (tous les créneaux, durées, maxPlayers) sur
  14 jours glissants → c'est le **dénominateur** de l'occupation.
- ❌ **PAS l'occupation** : les créneaux réservés ne sont **pas retirés** (compte
  de créneaux/jour identique au passé, présent et futur ⇒ template fixe).

**Limites :**
- **11/16 enseignes renvoient 401** sur l'API directe (verrouillée). Pour
  celles-là : prix via leur site (WordPress affiche la grille) ou via une
  enseigne sœur. La dispo reste accessible pour TOUTES via le proxy
  escapegame.fr `upcoming`.
- Le `roomId` 4escape ≠ mongoid escapegame.fr ⇒ on attache la grille au
  **niveau enseigne** (les salles d'une marque partagent la grille).

## RÉTROSPECTIF (question explicite) — réponse

**Non, pas d'historique d'occupation passé récupérable.** Aucune des deux API
n'expose les réservations passées :
- `booking-data-json` accepte une date passée mais renvoie le **planning
  théorique** (pas l'état réel) — inexploitable pour l'occupation rétro.
- `upcoming` ne donne que le **prochain** créneau libre (futur).

L'historique ne peut donc se construire que **vers l'avant**, par accumulation
de snapshots (/30 min) : on a déjà tout le dénominateur (planning + prix), et on
observe le remplissage des créneaux futurs au fil du temps → occupation réelle
reconstruite à J+1, J+2… On ne pourra pas remonter avant la date de mise en
service du scraper.

## IDF élargi (question explicite)

escapegame.fr expose des pages de communes de couronne (clichy, montreuil,
boulogne, issy, versailles…) mais **0 card 4escape** dessus : la couronne est
quasi exclusivement sur des plateformes non-4escape ⇒ **track par-site requis**.
La couverture 4escape via escapegame.fr est donc **Paris-centrée** (56 salles).
Élargir l'IDF = harvester les fiches venues (tous opérateurs) par commune puis
résoudre leur plateforme une par une (chantier track-2).

## MAJ 2 — écosystème 4escape entièrement cartographié (autonome)

Toutes les enseignes 4escape (incl. The Game, Lock Academy, Deep Inside,
Crack the Egg, Live Cinema, Pandore, Unleash, The Quest Factory, Artifact…)
partagent le **même backend** `<company>.4escape.io`, derrière 2 habillages :
- iframe `<company>.4escape.io` (ex. unleashescape, thequestfactory) ;
- widget `widgets.4escape.app` + `#/catalog/<uuid>` (ex. livecinema, pandore,
  crack-the-egg, thegame). Le widget pose `window.forescapeStore.baseURL =
  https://<company>.4escape.io` et tape la même API.

**API publique `<company>.4escape.io/api/public/` (UNIVERSELLE, 0 × 401) :**

| Endpoint | Donne | Usage |
|---|---|---|
| `GET /api/public/boot` | baseURL, langues, devise | validation enseigne |
| `GET /api/public/settings` | **organization (nom, tél, site, adresse), rooms (nom, durée, joueurs, difficulté, description, URL de résa), premises (multi-site), priceCategories, tags** | **catalogue complet + découverte auto des sites de résa** |
| `POST /booking-data-json` | planning théorique + **grille de prix** | prix (mais 401 sur ~11 enseignes) |
| `GET availability.4escape.io/egfr/upcoming/<c>/<r>` | prochain créneau libre | dispo (proxy escapegame, universel) |

→ **`/api/public/settings` remplace mon ancien catalogue** : 16/16 enseignes
enrichies sans blocage, 49/56 salles avec métadonnées + **URL de résa
auto-découverte** (plus besoin qu'on me donne les liens). CP non rempli par
4escape (geo `[null,null]`) ⇒ géocodage à partir de l'adresse (TODO).

**Découverte autonome (réponse à "trouve-les toi-même") :** la chaîne est
désormais : escapegame.fr → companies → `/api/public/settings` → sites officiels
+ salles + prix + dispo. Aucune saisie manuelle d'URL requise.

**Une seule enseigne hors 4escape parmi les 6 testées : One Hour**
(`escape-game-paris.one-hour.fr`, React custom) ⇒ handler par-site dédié.

## Prochaines étapes
1. Wirer `escape_4escape.py` dans le workflow /30 min (accumulation historique).
2. Élargir l'annuaire : villes IDF (les slugs suburbains testés ont renvoyé 0 →
   trouver la bonne structure d'URL, ex. `/france/ile-de-france/...`).
3. `escape_brand_map.json` (fusion enseigne ↔ companies, flag couverture 4escape).
4. Récupérer les **prix** (absents de l'API upcoming) via les fiches détail
   escapegame.fr ou les pages de résa.
5. Repointer le dashboard sur les données réelles (demande live) une fois
   quelques heures d'historique accumulées.
