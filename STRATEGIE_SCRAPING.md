# Stratégie de scraping — cadences, rétrospectif, prix

## 1. Cadences (quoi, à quelle fréquence, pourquoi)

| Fréquence | Action | Endpoint | Pourquoi |
|---|---|---|---|
| **30 min** | dispo de chaque salle 4escape | `availability.4escape.io/egfr/upcoming/<c>/<r>` | capter la **disparition de créneau** → reconstruire l'occupation. C'est le coeur. Léger (1 req/salle). |
| **Quotidien** (nuit) | catalogue + prix + nouveautés | `<c>.4escape.io/api/public/settings` + `booking-data-json` | grille de prix, nouvelles salles, fermetures, métadonnées. Change lentement. |
| **Quotidien** | annuaire IDF léger (Paris) | sitemap-company + fiches | détecter nouvelles enseignes / fermetures. |
| **Hebdo** | annuaire IDF complet + géocodage + résolution plateforme | sitemap + fiches + reverse-geocode | couverture exhaustive couronne, nouveaux opérateurs non-4escape. |
| **6 h** | sanity (fraîcheur) | local | alerte si une enseigne ne remonte plus. |

**Fenêtre temporelle** : dispo J→J+14 (limite 4escape `viewDuration`). Le planning
théorique (dénominateur) est récupéré en une fois ; la dispo se rafraîchit /30 min.

**Rétention** : observations brutes 90 j en JSON (`escape_data/`, non versionné,
cache CI), puis archive gzip / **Supabase** au-delà. Agrégats (`escape_*_data.json`)
versionnés et conservés indéfiniment (légers).

## 2. Rétrospectif — ce qui est possible (et ce qui ne l'est pas)

**❌ Backfill du passé : impossible.** Ni `upcoming` (prochain créneau futur) ni
`booking-data-json` (planning théorique, créneaux réservés non retirés) n'exposent
l'historique réel des réservations passées. On ne pourra pas reconstituer
l'occupation d'**avant** la mise en service du scraper.

**✅ Ce qu'on a immédiatement (pseudo-rétrospectif) :**
- Le **planning complet + prix** dès le 1er run (dénominateur prêt, pas besoin
  d'attendre).
- Dès quelques heures de relevés /30 min : occupation réelle reconstruite des
  créneaux **passés depuis le démarrage** (J, J+1…), par disparition.

**Construction de l'historique d'occupation (forward) :**
1. À chaque relevé : pour chaque salle, on connaît le **prochain créneau libre**.
2. S'il **avance/disparaît** entre 2 relevés alors que sa date était future →
   créneau **réservé** (`event: prev_slot_reserved`).
3. On croise avec le **planning théorique** (tous les créneaux existants) →
   `occupation = réservés / créneaux planifiés` par salle/jour/tranche.
4. Au fil des semaines : heatmap jour×heure, tendance prix, top créneaux qui
   partent en <24 h, saisonnalité.

→ La valeur s'accumule. **2–3 semaines** de relevés donnent une heatmap fiable ;
**6 mois** donnent les tendances prix/demande glissantes.

## 3. Stratégie prix

| Source | Couverture | Donnée | Note |
|---|---|---|---|
| `api/public/settings` | **16/16** (universel) | catalogue, joueurs, durée, difficulté | pas le tarif € |
| `POST booking-data-json` | 5/16 (11 en 401) | **grille € par nb joueurs** | normaliser `mode` per-player/forfait |
| Site enseigne (WordPress) | reste | grille affichée | parsing par-site (fallback) |

**Plan** : (1) settings pour tout le catalogue ; (2) booking-data-json pour les
ouvertes ; (3) pour les 11 verrouillées → parser la grille sur leur page de résa
WP (la plupart affichent « 2 joueurs : 55€/pers… »). Stockage `prix_grille:
{"2":..,"3":..}` + `mode`.

## 4. Couverture IDF

- **Source d'exhaustivité** : `sitemap-company.xml` d'escapegame.fr (3500+ venues
  monde) filtré sur les communes IDF → annuaire complet (`escape_idf_directory.json`).
- **Paris** : ~114 enseignes annuaire ; **16 sur 4escape** (dispo+prix live), le
  reste = autres plateformes → résolution par-site progressive.
- **Couronne (77/78/91/92/93/94/95)** : harvest par commune ; quasi aucune sur
  4escape → track par-site.
- Coords fiables via le lien maps de chaque fiche ; CP précis par reverse-geocode.

## 5. Détection nouveautés / fermetures
- Nouvelle `room_id` dans settings/annuaire = **nouvelle salle** → à signaler.
- Salle/enseigne disparue de l'annuaire = **fermeture** → à archiver.
- Diff quotidien du catalogue → flux « nouveautés » sur le dashboard.
