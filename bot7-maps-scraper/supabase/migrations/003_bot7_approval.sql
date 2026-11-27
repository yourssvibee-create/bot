-- Bot 7 — hosted CSV + Telegram-approve workflow.
-- Applies on top of 002_bot7_channels.sql.

-- ---- bot7_packs: approval + hosted CSV ----
alter table bot7_packs
  add column if not exists download_url         text,
  add column if not exists download_url_expires timestamptz,
  add column if not exists approval_message_id  text,
  add column if not exists approved_at          timestamptz,
  add column if not exists approved_by          text,
  add column if not exists published_at         timestamptz,
  add column if not exists publish_error        text;

create index if not exists bot7_packs_approval_idx
  on bot7_packs (approved_at desc nulls last);

-- ---- bot7_telegram_offset: single-row cursor for getUpdates polling ----
create table if not exists bot7_telegram_offset (
  id           int primary key default 1,
  last_offset  bigint not null default 0,
  updated_at   timestamptz not null default now(),
  constraint bot7_telegram_offset_singleton check (id = 1)
);
insert into bot7_telegram_offset (id, last_offset) values (1, 0)
  on conflict (id) do nothing;

-- ---- Storage bucket: bot7-packs (private; signed URLs only) ----
insert into storage.buckets (id, name, public)
values ('bot7-packs', 'bot7-packs', false)
on conflict (id) do nothing;
