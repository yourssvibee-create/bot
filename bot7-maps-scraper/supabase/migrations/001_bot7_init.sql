-- Bot 7 — Local Business Lead Factory schema.
-- Lives in the same Supabase project as Bot 1 / Bot 2, but every table is
-- prefixed `bot7_` so there's zero cross-unit mixing.

create extension if not exists "pgcrypto";

-- ---- scraped + enriched businesses ----
create table if not exists bot7_businesses (
    id                 uuid primary key default gen_random_uuid(),
    segment_slug       text not null,          -- "{metro}__{vertical}"
    metro_slug         text not null,
    vertical_slug      text not null,
    dedup_key          text not null,          -- name|address_prefix (lowercased)
    name               text not null,
    category           text,
    street_address     text,
    tagline            text,
    phone              text,
    website            text,
    domain             text,
    owner_first_name   text,
    owner_last_name    text,
    owner_title        text,
    owner_email        text,
    email_confidence   int,
    rating             double precision,
    review_count       int,
    google_maps_url    text,
    captured_at        timestamptz not null default now(),
    raw                jsonb,
    unique (segment_slug, dedup_key)
);
create index if not exists bot7_businesses_segment_idx
    on bot7_businesses (segment_slug, captured_at desc);
create index if not exists bot7_businesses_metro_idx
    on bot7_businesses (metro_slug, vertical_slug);
create index if not exists bot7_businesses_email_idx
    on bot7_businesses (owner_email) where owner_email is not null;

-- ---- run log ----
create table if not exists bot7_runs (
    id               uuid primary key default gen_random_uuid(),
    started_at       timestamptz not null,
    finished_at      timestamptz not null,
    segments_total   int not null,
    segments_ok      int not null,
    records_total    int not null,
    emails_found     int not null,
    status           text not null,            -- ok | partial | fail
    notes            text
);
create index if not exists bot7_runs_started_idx on bot7_runs (started_at desc);

-- ---- pack catalog (for the store listing) ----
create table if not exists bot7_packs (
    id               uuid primary key default gen_random_uuid(),
    segment_slug     text not null unique,
    metro_slug       text not null,
    vertical_slug    text not null,
    tier             int not null,
    price_usd        int not null,
    record_count     int not null default 0,
    last_built_at    timestamptz,
    gumroad_url      text,
    payhip_url       text,
    whop_url         text
);
create index if not exists bot7_packs_tier_idx on bot7_packs (tier, price_usd);
