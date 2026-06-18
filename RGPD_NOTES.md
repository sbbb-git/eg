# Notes RGPD / éthique de collecte

Traçabilité du périmètre de collecte de l'observatoire Escape Room IDF.

## Ce qui est collecté
- **Disponibilité publique de créneaux** affichée sur les pages de réservation
  publiques des enseignes (libre / réservé, reconstruit par disparition de créneau).
- **Prix affichés publiquement** (grille par nombre de joueurs, suppléments
  week-end / soir).
- **Métadonnées de room** : nom de salle, durée, thématique, code postal du centre.

## Ce qui n'est PAS collecté
- Aucune **donnée personnelle** : pas de noms de joueurs, e-mails, téléphones,
  comptes clients, avis nominatifs.
- Aucune **géolocalisation fine** : on se limite au centroïde du code postal /
  arrondissement pour la carte (pas l'adresse exacte).
- Aucun **contact** des enseignes ; aucune création de compte ou de réservation.

## Base légale & finalité
- Finalité : **benchmark concurrentiel interne** (intérêt légitime), privé.
- Pas de revente de données, pas de diffusion publique : site
  **password-protégé** (SHA-256 client-side + `sessionStorage`, `noindex`).

## Bonnes pratiques techniques respectées
- Fréquence raisonnable (1 relevé / 30 min), retry avec backoff, délais entre
  requêtes (politesse serveur), pas de contournement d'authentification.
- Respect attendu des `robots.txt` et CGU : **à vérifier enseigne par enseigne**
  avant d'activer un handler de scrape en production. Une enseigne qui interdit
  explicitement la collecte doit être retirée du catalogue ou marquée
  `priorite: 0` (désactivée).
- Données brutes > 30 jours : pruning local + archive (Supabase), pas de
  conservation indéfinie en clair dans le dépôt git.

## Droits
Les enseignes peuvent demander le retrait de leur établissement du périmètre :
retirer l'entrée de `escape_extension_brands.json` et purger les
`escape_data/<slug>_data.json` correspondants.
