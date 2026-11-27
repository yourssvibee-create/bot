-- Bot 7 — pending-edit state for the Telegram approve/edit workflow.
-- Applies on top of 003_bot7_approval.sql.

-- Per-pack: which field is currently being edited (title/price/description),
-- the Telegram chat it was prompted from, and the prompt message id so
-- the poller can match a reply back to its pack.
alter table bot7_packs
  add column if not exists edit_pending_field    text,
  add column if not exists edit_prompt_chat_id   text,
  add column if not exists edit_prompt_message_id text;

-- Single-chat convenience index — the poller looks up the pending pack
-- by (chat_id, prompt_message_id) off the user's reply.
create index if not exists bot7_packs_edit_pending_idx
  on bot7_packs (edit_prompt_chat_id, edit_prompt_message_id)
  where edit_pending_field is not null;
