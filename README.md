# 🗝️ Observatoire Escape Room — Île-de-France

Observatoire data **privé** du marché des Escape Rooms en Île-de-France
(Paris + 77 + 78 + 91 + 92 + 93 + 94 + 95). Scrape en continu la disponibilité
des créneaux des enseignes pour reconstruire taux d'occupation, prix, nouveautés
et demande live, le tout dans un dashboard statique password-protégé.

> ⚠️ Usage interne / benchmark concurrentiel uniquement. Voir [`RGPD_NOTES.md`](RGPD_NOTES.md).

## Source primaire : escapegame.fr / 4escape

Après investigation live (voir [`ANALYSE_SCRAPING.md`](ANALYSE_SCRAPING.md)), la
source la plus rentable est l'annuaire **escapegame.fr**, façade de la plateforme
de résa **4escape.io**. Une API publique donne la dispo réelle :

```
GET https://availability.4escape.io/egfr/upcoming/<company>/<roomId>
 -> { date, time, available, book-url }   # PROCHAIN créneau libre uniquement
```

`escape_4escape.py` harvest le catalogue (≈56 salles / 16 enseignes Paris, dont
The Game, Lock Academy, Phobia, Deep Inside…) et relève le prochain créneau libre
par salle à chaque run (append-only). La **demande** se mesure via `lead_days`
(délai jusqu'au prochain créneau libre) ; l'**occupation** se reconstruit avec
l'historique (disparition du prochain créneau ⇒ réservation). Les venues
**non couvertes par 4escape** restent sur le track "scraping par-site"
(`escape_extension_*`).

## Déploiement (GitHub Pages)

Le site est statique. **Réglage unique** à faire dans l'UI GitHub :
*Settings → Pages → Build and deployment → Source = **GitHub Actions***.
Ensuite le workflow `pages.yml` publie automatiquement à chaque push (dashboard
+ data). Le site est protégé par mot de passe (SHA-256 client-side).

> Alternative sans workflow : *Source = Deploy from a branch →
> `claude/escape-init-fkcfrk` / `(root)`* — sert `index.html` directement.

## Pipeline RÉEL (4escape) — fichiers

| Fichier | Rôle |
|---|---|
| `escape_4escape.py` | harvest annuaire + `--enrich` (catalogue via `/api/public/settings`) + `--prices` (booking-data-json) + `--scrape` (dispo upcoming). |
| `escape_geocode.py` | géocode les enseignes (Nominatim) → CP + lat/lon. |
| `escape_idf_directory.py` | annuaire COMPLET IDF (153 venues, 8 dépts) via sitemap escapegame.fr. |
| `escape_live_compute.py` | fusion catalogue+geo+dispo → `escape_live_data.json`. |
| `escape_4escape_compute.py` | instantané demande → `escape_4escape_live.json`. |
| `escape_live.html` | **dashboard LIVE réel** (carte tous escape games IDF, demande, prix, filtres, liens résa). |
| `ANALYSE_SCRAPING.md` | cartographie de l'écosystème 4escape + ce qui bloque. |
| `STRATEGIE_SCRAPING.md` | cadences 30min/quotidien/hebdo, rétrospectif, prix. |

> Le dashboard de démo `escape_idf.html` (heatmap/occupation sur données seed)
> est conservé comme **maquette** de ce que produira l'historique accumulé.

## Architecture

```
escape_extension_brands.json     Catalogue des enseignes IDF {label,url,cp,type,platform_guess,priorite}
        │
        ▼  escape_extension_discover.py   (fetch homepage + paths, détecte la plateforme)
escape_extension_resolved.json   Plateforme résolue par enseigne (idempotent)
        │
        ▼  escape_extension_scrape.py     (dispatch par plateforme, logique "disparition de créneau")
escape_data/<slug>_data.json     Store brut par enseigne (sessions) — non versionné (cache CI / Supabase)
        │
        ▼  escape_idf_compute.py          (agrégation : KPIs, carte, heatmap, top rooms, mensuel)
escape_idf_data.json             Agrégat unifié versionné, lu par le dashboard
        │
        ▼
index.html (hub login)  ──►  escape_idf.html (dashboard)
```

### Modèle d'occupation — "disparition de créneau"
On relève régulièrement les créneaux **disponibles**. Un créneau vu libre puis
qui **disparaît avant son heure** ⇒ `reserve`. Un créneau resté visible jusqu'après
son heure ⇒ `libre_fin`. Le **taux d'occupation** = `reserve / (reserve + libre_fin)`
sur les créneaux **passés** (issue finale connue). Les réservations sur créneaux
**futurs** alimentent l'indicateur de *demande live*, pas le taux.

## Composants

| Fichier | Rôle |
|---|---|
| `escape_extension_brands.json` | Catalogue v1 (41 enseignes IDF). |
| `escape_extension_discover.py` | Détecte bsport / Mindbody / Anybuddy / Doinsport / FullCalendar / Timekit / WordPress… avec retry+backoff & rotation d'UA. |
| `escape_extension_scrape.py` | Scrape par plateforme (FullCalendar & Timekit gérés ; autres à venir). Mode `--seed` = historique de démo offline. |
| `escape_idf_compute.py` | Agrège tous les `escape_data/*_data.json` en `escape_idf_data.json`. |
| `escape_idf.html` | Dashboard : KPIs, carte Leaflet, heatmap jour×heure, top 20 rooms, comparateur, top créneaux, classement, évolution mensuelle (Chart.js), roadmap. |
| `index.html` | Hub de login (SHA-256 + `sessionStorage`). |
| `safestore.py` | Écriture atomique JSON / gzip (anti-corruption). |
| `sanity_check.py` | Flag les centres stale (>16h). Code retour ≠0 si stale. |
| `escape_geo_cp.json` | Centroïdes [lat,lon] par code postal IDF (markers carte). |

## Démarrage rapide

```bash
# 1. (optionnel) résoudre les plateformes en live
python3 escape_extension_discover.py --only-unknown   # offline: ajouter --offline

# 2. amorcer un historique de démonstration (offline, sans réseau)
python3 escape_extension_scrape.py --seed

# 3. agréger pour le dashboard
python3 escape_idf_compute.py

# 4. servir le site
python3 -m http.server 8080      # puis http://localhost:8080/index.html
```

**Mot de passe de démo : `escape-idf-2026`.** Pour le changer, régénérer le
hash et le coller dans `PW_HASH` (présent dans `index.html` **et**
`escape_idf.html`) :

```bash
python3 -c "import hashlib;print(hashlib.sha256(b'NOUVEAU_MDP').hexdigest())"
```

## Automatisation (GitHub Actions)

| Workflow | Cron | Rôle |
|---|---|---|
| `escape-extension.yml` | `*/30 * * * *` | discovery (`--only-unknown`) + scrape + agrégat, commit. |
| `escape-aggregate.yml` | `0 */2 * * *` | recompute de l'agrégat (filet de sécurité). |
| `sanity.yml` | `0 */6 * * *` | freshness check, ouvre une issue `stale-data` si besoin. |

Le store brut `escape_data/` n'est **pas versionné** (volumineux) : il est
conservé entre runs via `actions/cache` et destiné à l'archive Supabase au-delà
de 30 jours. Seul l'agrégat `escape_idf_data.json` est commité (c'est le seul
fichier dont le dashboard a besoin).

## Étendre

- **Nouvelle enseigne** : ajouter une entrée dans `escape_extension_brands.json`.
- **Nouvelle plateforme** : écrire un handler `scrape_<plateforme>(rec, ua)`
  renvoyant des créneaux dispo bruts, et l'enregistrer dans `PLATFORM_HANDLERS`.
- **SPA React** (WeChamber, calendriers custom) : fallback Playwright headless
  (cf. `requirements.txt`).
- **Pricing** : grille `prix_grille:{"2":..,"3":..}` déjà gérée (dégressif +
  supplément week-end / soir tardif).

## Roadmap
- Résoudre les `unknown` → endpoints réels FullCalendar/Timekit.
- Handlers bsport / Mindbody / Anybuddy / Doinsport.
- Détection des nouveautés (nouvelle `room_id`) et rooms fermées.
- Carte des "déserts escape" (offre vs densité population).
- +60 indés grande couronne (77/78/91/95).

## Données affichées par défaut
`escape_idf_data.json` versionné contient un **agrégat de démonstration**
(généré via `--seed`, offline) pour que le dashboard s'affiche immédiatement.
Les chiffres ne sont **pas** des relevés réels tant que les handlers live ne sont
pas activés enseigne par enseigne.
