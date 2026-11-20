-- Bot 2 (social-factory) schema. Lives in the same unit1-outreach Supabase
-- project as Bot 1; every table is prefixed bot2_* to avoid collisions.

create extension if not exists pgcrypto;

create table if not exists bot2_content_queue (
    id                        uuid primary key default gen_random_uuid(),
    created_at                timestamptz not null default now(),
    scheduled_for             timestamptz,
    content_type              text not null
                              check (content_type in ('reel','carousel','long','tweet','long_form_video')),
    caption                   text,
    media_urls                text[] not null default '{}',
    target_platforms          text[] not null default '{}',
    status                    text not null default 'queued',
    approval_telegram_msg_id  bigint
);
create index if not exists bot2_content_queue_status_idx
    on bot2_content_queue (status, scheduled_for);

create table if not exists bot2_dispatches (
    id            uuid primary key default gen_random_uuid(),
    content_id    uuid not null references bot2_content_queue(id) on delete cascade,
    platform      text not null,
    method        text not null check (method in ('api','playwright')),
    posted_at     timestamptz,
    external_url  text,
    status        text,
    error         text,
    retry_count   int not null default 0
);
create index if not exists bot2_dispatches_content_idx on bot2_dispatches (content_id);
create index if not exists bot2_dispatches_status_idx  on bot2_dispatches (status, platform);

create table if not exists bot2_account_health (
    platform              text primary key,
    last_successful_post  timestamptz,
    consecutive_failures  int not null default 0,
    status                text not null default 'green'
                          check (status in ('green','yellow','red')),
    notes                 text
);
