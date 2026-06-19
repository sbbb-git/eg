-- supabase_schema.sql — observatoire escape IDF (hiérarchie normalisée).
-- À exécuter UNE FOIS : Supabase > SQL Editor > coller > Run.
-- Lecture publique (clé publishable, dashboard) ; écriture via clé secrète (CI).
-- Modèle : enseigne (1—N) centre (1—N) salle (1—N) session.

-- ───────────────────────────── enseignes ─────────────────────────────
create table if not exists enseignes (
  id          text primary key,           -- slug normalisé du nom de marque
  nom         text not null,
  website     text,
  updated_at  timestamptz default now()
);

-- ────────────────────────────── centres ──────────────────────────────
create table if not exists centres (
  id           text primary key,          -- premise id (ou company si pas de premise)
  enseigne_id  text not null references enseignes(id) on delete cascade,
  nom          text,
  cp           text,
  ville        text,
  adresse      text,
  lat          double precision,
  lon          double precision,
  updated_at   timestamptz default now()
);
create index if not exists centres_enseigne_idx on centres(enseigne_id);

-- ─────────────────────────────── salles ──────────────────────────────
create table if not exists salles (
  id            text primary key,          -- room id
  centre_id     text not null references centres(id) on delete cascade,
  nom           text,
  theme         text,                      -- horreur/enquête/fantastique/science-fiction/aventure
  difficulty    int,
  duree_minutes int,
  joueurs_min   int,
  joueurs_max   int,
  updated_at    timestamptz default now()
);
create index if not exists salles_centre_idx on salles(centre_id);

-- ────────────────────────────── sessions ─────────────────────────────
create table if not exists sessions (
  salle_id          text not null references salles(id) on delete cascade,
  date              date not null,
  heure             text not null,
  duree_minutes     int,
  nb_joueurs_min    int,
  nb_joueurs_max    int,
  prix_total        jsonb,                 -- {"2": 110, "3": 126, ...} prix TOTAL par nb joueurs
  prix_total_moyen  numeric,               -- moyenne du prix total sur les tailles valides
  dispo             boolean,
  booked            boolean,
  statut            text,                  -- reserve / libre_fin / null
  premier_vu        timestamptz,
  dernier_vu        timestamptz,
  releve            timestamptz,
  primary key (salle_id, date, heure)      -- upsert idempotent
);
create index if not exists sessions_date_idx    on sessions(date);
create index if not exists sessions_statut_idx  on sessions(statut);
create index if not exists sessions_booked_idx  on sessions(booked);

-- ──────────────────── RLS : lecture publique seule ────────────────────
alter table enseignes enable row level security;
alter table centres   enable row level security;
alter table salles    enable row level security;
alter table sessions  enable row level security;

drop policy if exists "read enseignes" on enseignes;
drop policy if exists "read centres"   on centres;
drop policy if exists "read salles"    on salles;
drop policy if exists "read sessions"  on sessions;
create policy "read enseignes" on enseignes for select using (true);
create policy "read centres"   on centres   for select using (true);
create policy "read salles"    on salles    for select using (true);
create policy "read sessions"  on sessions  for select using (true);
-- (l'écriture passe par la clé secrète service_role qui bypass RLS)
