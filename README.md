# 🗝️ Observatoire Escape Room — Île-de-France

Observatoire data **privé** du marché des escape games en Île-de-France. Relève
en continu les créneaux publiés par les enseignes pour reconstituer l'occupation,
les prix, le nombre de joueurs et le panier, affichés dans un dashboard unique.

> ⚠️ Usage interne / benchmark concurrentiel uniquement. Voir [`RGPD_NOTES.md`](RGPD_NOTES.md).

## Vue d'ensemble

```
API publique 4escape (booking-data-json + /settings, ~50 enseignes)
        │   escape_loop.py  — boucle : relevé complet toutes les ~10 min
        ▼   escape_4escape_all.py --scrape   (parallèle, ~30 s pour tout le réseau)
escape_data/4escape_all/<enseigne>.json   store brut (cache CI, non versionné)
        │   supa_sync.py --since <…>   (seules les sessions modifiées)
        ▼
Supabase : enseignes → centres → salles → sessions        (supabase_schema.sql)
        │   lecture publique (clé publishable, RLS lecture seule)
        ▼
index.html — dashboard unique (GitHub Pages), lit Supabase en direct
```

## Fichiers

| Fichier | Rôle |
|---|---|
| `escape_4escape_all.py` | Cœur : découverte des comptes 4escape (`--discover`) et relevé de toutes les enseignes (`--scrape`) : enseigne → centres → salles → sessions, prix, places, statut. |
| `escape_loop.py` | Boucle de relevés continus (un run CI = ~5 h 30 de relevés toutes les 10 min). |
| `supa_sync.py` | Pousse le store vers Supabase (`--since` : incrémental). Géocode les centres, purge les enseignes orphelines. |
| `sanity_check.py` | Surveillance de bout en bout sur Supabase + erreurs du scraper. |
| `index.html` | Dashboard (mot de passe) : occupation, heatmap, classements, prix × demande, joueurs et panier réels, salles sans disponibilité, détail enseigne / salle avec sources. |
| `escape_deep_discover.py` | Trouve les comptes 4escape à sous-domaine non devinable (Playwright). |
| `escape_engine_bookeo.py`, `escape_proxy.py` | Moteur Bookeo, actif seulement avec un proxy résidentiel (voir plus bas). |
| `escape_4escape.py`, `escape_idf_directory.py`, `escape_merge_escapers.py`, `escape_resolve_*.py`, `escape_extension_discover.py`, `escape_prices_site.py`, `escape_geocode.py` | Rafraîchissement quotidien : catalogue, annuaire IDF, résolution des sites et plateformes. |

## Ce que mesure chaque indicateur

- **Occupation** : part des créneaux publiés qui ne sont plus réservables. Les salles
  qui n'affichent jamais un créneau libre (fermées, résa par téléphone…) et le dernier
  jour de l'horizon (publié partiellement) sont **exclus** et signalés.
- **Statut d'un créneau passé** : `reserve` (booké après avoir été vu libre),
  `libre_fin` (resté libre jusqu'à son début), `retire` (supprimé par l'enseigne avant
  son heure), vide (jamais vu libre). Un relevé fait après le début d'un créneau est
  ignoré : l'API renvoie alors tout créneau passé comme « désactivé ».
- **Panier estimé (salles privées)** : prix d'une session pour un groupe type de
  4 joueurs. 4escape ne publie pas la taille des groupes : c'est une estimation.
- **Joueurs et panier réels (sessions vendues à la place)** : places vendues = baisse
  des places libres observée (référence = minimum de places bloquées, ce qui écarte la
  capacité bloquée d'emblée, les annulations et les changements de capacité). Les
  sessions passées d'un coup à complet sont traitées comme des privatisations et écartées.
- **Prix** : chaque tarif est lu selon son type publié (`absolute` / `per-player` /
  `*-product-quantity`).

## Automatisation (GitHub Actions)

| Workflow | Fréquence | Rôle |
|---|---|---|
| `escape-extension.yml` | en continu | Boucle de relevés + sync Supabase. **Seul** écrivain du store et des sessions. |
| `escape-daily.yml` | 1×/jour | Catalogue, annuaire, résolution des sites, découverte des comptes. |
| `escape-deep-discover.yml` | ~4 h | Nouveaux comptes 4escape cachés (la boucle les relève au tour suivant). |
| `sanity.yml` | ~3 h | Surveillance ; ouvre une issue `stale-data` en cas de problème, la referme une fois rétabli. |
| `pages.yml` | à chaque modif d'`index.html` | Publie **uniquement** la page sur GitHub Pages. |

GitHub n'exécute qu'une fraction des crons planifiés : la fréquence de relevé ne
dépend donc pas du cron, mais de la boucle interne du job (le cron sert à mettre
le run suivant en file d'attente).

Secrets requis : `SUPABASE_URL`, `SUPABASE_KEY` (clé `service_role`, écriture).

## Déploiement (GitHub Pages)

Réglage unique : *Settings → Pages → Build and deployment → Source = **GitHub Actions***.
`pages.yml` publie ensuite la page à chaque modification.

## Sécurité — à savoir

Le mot de passe du dashboard est vérifié **côté navigateur** : il masque l'interface,
il ne protège pas les données. La clé Supabase publique (présente dans la page)
permet de lire la base, et ce dépôt est public. Pour une vraie protection :
Supabase Auth (comptes utilisateurs) + politiques RLS réservées aux utilisateurs
connectés. Passer le dépôt en privé n'est pas une solution gratuite : la collecte
continue consomme plus de minutes Actions que le quota des plans privés.

## Couverture des plateformes anti-bot (Bookeo) — proxy résidentiel

Certaines venues (Bookeo) bloquent les IP de datacenter. Pour les relever il faut
sortir par un **proxy résidentiel** (Bright Data, Oxylabs, Smartproxy…). Ajouter
ces 3 secrets au repo (*Settings ▸ Secrets ▸ Actions*) :

| Secret | Exemple |
|---|---|
| `PROXY_SERVER` | `http://gate.smartproxy.com:7000` |
| `PROXY_USERNAME` | identifiant du provider |
| `PROXY_PASSWORD` | mot de passe du provider |

La boucle de relevés lance alors automatiquement `escape_engine_bookeo.py`
(installation de Playwright comprise). Sans ces secrets, rien ne change.
