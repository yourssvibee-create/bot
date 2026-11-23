-- Bot 7 — channels + pack metadata + refresh tracking.
-- Applies on top of 001_bot7_init.sql.

-- ---- bot7_packs extensions ----
alter table bot7_packs
  add column if not exists title              text,
  add column if not exists description        text,
  add column if not exists price_cents        int,
  add column if not exists price_kind         text,     -- one_time | monthly
  add column if not exists metro_tier         int,
  add column if not exists refresh_tier       text default 'one_time',  -- one_time | monthly
  add column if not exists sample_csv_path    text,
  add column if not exists status             text default 'draft',     -- draft | ready | listed | error
  add column if not exists error_note         text,
  add column if not exists last_refreshed_at  timestamptz,
  -- Gumroad
  add column if not exists gumroad_product_id    text,
  add column if not exists gumroad_listed_at     timestamptz,
  add column if not exists gumroad_last_pushed_at timestamptz,
  -- Payhip
  add column if not exists payhip_product_id    text,
  add column if not exists payhip_listed_at     timestamptz,
  add column if not exists payhip_last_pushed_at timestamptz,
  -- Whop (monthly refresh subscription tier)
  add column if not exists whop_product_id     text,
  add column if not exists whop_listed_at      timestamptz,
  add column if not exists whop_last_pushed_at timestamptz;

create index if not exists bot7_packs_status_idx on bot7_packs (status);
create index if not exists bot7_packs_refreshed_idx on bot7_packs (last_refreshed_at desc nulls last);

-- ---- bot7_runs: dedup concurrent appends ----
-- A manual workflow_dispatch overlapping cron would both insert a row.
-- Use (started_at, status) as a soft idempotency key — harmless duplicates
-- ignored on the ON CONFLICT path.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'bot7_runs_started_status_uniq'
  ) then
    alter table bot7_runs
      add constraint bot7_runs_started_status_uniq
      unique (started_at, status);
  end if;
end $$;
