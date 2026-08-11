-- Run this once in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- before setting SUPABASE_URL/SUPABASE_KEY. Mirrors drafts/store.py's Draft
-- dataclass exactly, plus draft_text (kept in a separate draft.txt file in the
-- local-disk backend; merged into one row here since Postgres handles long
-- text fine and there's no diff-with-any-tool need once it's in a database).

create table if not exists prompt_drafts (
    draft_id          text primary key,
    title             text not null,
    use_case          text not null,
    version           text not null,
    file_name         text not null,
    author            text not null,
    status            text not null default 'draft',
    rationale         text not null default '',
    draft_text        text not null,
    created_at        text not null,
    updated_at        text not null,
    status_note       text not null default '',
    status_changed_by text not null default '',
    status_changed_at text not null default '',
    test_evidence     jsonb,
    history           jsonb not null default '[]'::jsonb
);

-- RLS is on by default for new Supabase tables. This app connects with the
-- service_role key (see supabase_backend.py's docstring for why), which
-- bypasses RLS entirely -- so no policy is required for the app itself to
-- work. Row Level Security stays enabled below only so that anyone who
-- later adds the anon/public key to this project doesn't get silent,
-- unrestricted access to every draft by default.
alter table prompt_drafts enable row level security;
