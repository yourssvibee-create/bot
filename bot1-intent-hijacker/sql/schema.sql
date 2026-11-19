-- Bot 1 — Intent Hijacker schema
-- All tables live in the Unit 1 Supabase project (shared with Bot 2).
-- Tables Bot 7 writes (`bot7_leads`) are declared elsewhere; a view-friendly
-- stub is included here so this file applies cleanly on a fresh project.

create extension if not exists "pgcrypto";

-- ---- raw scraped signals ----
create table if not exists signals (
    id            uuid primary key default gen_random_uuid(),
    source        text not null,              -- trustpilot | quora | x | reddit | bot7_maps
    source_id     text not null,              -- stable upstream id (url hash etc.)
    url           text,
    author        text,
    title         text,
    body          text,
    captured_at   timestamptz not null default now(),
    raw           jsonb,
    unique (source, source_id)
);
create index if not exists signals_source_idx    on signals (source, captured_at desc);
create index if not exists signals_captured_idx  on signals (captured_at desc);

-- ---- scored leads ----
create table if not exists leads (
    id             uuid primary key default gen_random_uuid(),
    signal_id      uuid not null references signals(id) on delete cascade,
    intent_score   int  not null check (intent_score between 0 and 100),
    reason         text,
    segment        text not null,             -- high_intent | medium_intent | email_only_fallback | drop
    website        text,                      -- business domain if known
    contact_email  text,                      -- filled only when email_only_fallback
    scored_at      timestamptz not null default now(),
    unique (signal_id)
);
create index if not exists leads_segment_idx on leads (segment, intent_score desc);
create index if not exists leads_scored_idx  on leads (scored_at desc);

-- ---- drafted replies awaiting approval ----
create table if not exists replies_drafted (
    id             uuid primary key default gen_random_uuid(),
    lead_id        uuid not null references leads(id) on delete cascade,
    channel        text not null,             -- contact_form | reddit | quora | x
    draft_text     text not null,
    persona        text not null default 'dave_fieldston_ominvo',
    telegram_msg_id text,
    status         text not null default 'pending',  -- pending | approved | rejected | sent | hold
    created_at     timestamptz not null default now(),
    decided_at     timestamptz
);
create index if not exists replies_drafted_status_idx on replies_drafted (status, created_at);

-- ---- actually sent replies ----
create table if not exists replies_sent (
    id             uuid primary key default gen_random_uuid(),
    drafted_id     uuid not null references replies_drafted(id) on delete cascade,
    channel        text not null,
    external_id    text,                      -- comment id / submission id / form response
    sent_at        timestamptz not null default now(),
    outcome        text not null              -- sent | failed | skipped
);
create index if not exists replies_sent_sent_idx on replies_sent (sent_at desc);

-- ---- email-only leads handed off to ominvo-leadgen ----
create table if not exists handoff_to_leadgen (
    id             uuid primary key default gen_random_uuid(),
    lead_id        uuid not null references leads(id) on delete cascade,
    website        text not null,
    contact_email  text,
    context_blurb  text,                      -- 2-3 lines of what the signal showed
    created_at     timestamptz not null default now(),
    picked_up_at   timestamptz,
    unique (lead_id)
);
create index if not exists handoff_pending_idx on handoff_to_leadgen (picked_up_at) where picked_up_at is null;

-- ---- every dispatch attempt, every skip, every error ----
create table if not exists dispatch_log (
    id             bigserial primary key,
    bot            text not null default 'bot1',
    kind           text not null,             -- scrape | score | draft | approve | dispatch | handoff | skip | error
    target         text,                      -- url or identifier
    channel        text,
    status         text not null,             -- ok | fail | skip
    detail         jsonb,
    at             timestamptz not null default now()
);
create index if not exists dispatch_log_at_idx   on dispatch_log (at desc);
create index if not exists dispatch_log_kind_idx on dispatch_log (kind, at desc);

-- ---- Bot 7's local-maps feed (read-only for Bot 1; Bot 7 populates) ----
-- Declared here so Bot 1 can run against a fresh project before Bot 7 ships.
create table if not exists bot7_leads (
    id             uuid primary key default gen_random_uuid(),
    business_name  text not null,
    website        text,
    phone          text,
    category       text,
    city           text,
    country        text,
    rating         numeric(3,2),
    review_count   int,
    captured_at    timestamptz not null default now(),
    raw            jsonb,
    unique (business_name, city)
);
create index if not exists bot7_leads_cap_idx on bot7_leads (captured_at desc);
