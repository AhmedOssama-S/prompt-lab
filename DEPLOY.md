# Deploying Prompt Lab

The app itself is deploy-ready: `requirements.txt` covers every import, secrets
are read from environment variables (which Streamlit populates from its secrets
store), and `.streamlit/config.toml` is committed.

**Read "Three things to settle first" before you deploy.** Two of them affect
whether the deployment is safe; one affects whether the Prompts → Handoff
workflow works at all.

---

## Three things to settle first

### 1. The app has no login

Anyone who can open the URL can run comparisons — spending your Core42 and
Gemini budget — and can approve or reject prompt proposals. There is no user
account system.

Streamlit Community Cloud can put authentication in front of it, but **only for
apps deployed from a private GitHub repo**: a public repo means a public app.
For a private app you add viewers by email, and they sign in with Google OAuth
or a single-use emailed link.

> **Deploy from a private repo and use the viewer allow-list.** A public repo
> here means an open door to your API spend.

### 2. Drafts do not survive a restart on Community Cloud

Community Cloud's filesystem is ephemeral — its own docs say locally stored data
"may be deleted at any time" — and apps reboot on inactivity, redeploy, or
resource pressure. Prompt Lab stores drafts as files under `drafts/`.

So on Community Cloud: a business user writes a proposal, the app sleeps
overnight, **the proposal is gone.** The Compare page is unaffected (it holds
nothing but the current session), but the Prompts → Handoff workflow — the whole
point of those two pages — breaks.

The app detects this and shows a red warning on both pages. See
[Making drafts survive](#making-drafts-survive) below.

### 3. Data residency

Community Cloud is US-hosted. Nomination content typed into the payload box, and
every prompt and model response, passes through it. This is the same open
question from the LangSmith discussion, and it has not been answered yet. If
nomination data cannot leave the region, Community Cloud is not an option and
you want [self-hosting](#option-b-self-host-recommended-here).

---

## Option A — Streamlit Community Cloud

Good for: a quick shared demo of the Compare page.
Not good for: the proposal workflow (see #2), or regulated data (see #3).

Steps that need your accounts — I can't do these for you, they require signing
in and entering API keys:

1. **Create a private GitHub repo** and push this folder as the repo root
   (`streamlit_app.py` must be at the top level).

   ```bash
   cd prompt-lab && git init && git add . && git commit -m "Prompt Lab"
   ```

   Then add your remote and push. Verify `.env` is **not** in the commit —
   it's gitignored, but check:

   ```bash
   git ls-files | grep -E "^\.env$|secrets\.toml$" && echo "STOP: secrets staged" || echo "clean"
   ```

2. **Deploy** at [share.streamlit.io](https://share.streamlit.io) → *Create app*
   → pick the repo, branch, and `streamlit_app.py`.

3. **Add secrets**: App menu → *Settings* → *Secrets*. Paste the contents of
   `.streamlit/secrets.toml.example` with real values filled in.

   Keep every key top-level. Streamlit exposes root-level secrets as environment
   variables but sectioned ones only through `st.secrets`, and
   `setup_clients()` reads `os.environ` — so putting these under a `[section]`
   makes every provider silently show as "not configured".

4. **Restrict access**: App menu → *Settings* → *Sharing* → add viewer emails.

5. Confirm the Prompts page shows the ephemeral-storage warning. If it doesn't,
   detection failed — set `PROMPT_LAB_EPHEMERAL_STORAGE = "1"` in secrets.

## Option B — Self-host (recommended here)

Running it on a machine you control resolves all three concerns at once: you can
put it behind your existing SSO or network boundary, the filesystem is real so
drafts persist, and nomination data never leaves your infrastructure.

Anywhere that runs Python works — an internal VM, Azure App Service, a container
on your own cluster:

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py --server.port 8501 --server.address 0.0.0.0
```

Provide the same keys as environment variables (not a secrets file), put it
behind your reverse proxy / SSO, and set `PROMPT_LAB_PERSISTENT_STORAGE=1` so
the storage warning stops showing.

Given this app has no login of its own, an authenticating reverse proxy is doing
real work here — don't expose the port directly.

---

## Making drafts survive

Only relevant if you deploy somewhere ephemeral.

### Implemented: Supabase

`drafts/store.py` transparently switches from local files to a Supabase table
the moment `SUPABASE_URL` and `SUPABASE_KEY` are both set — no other code
changes, and nothing breaks if you leave them unset (local files are still
the default, e.g. for self-hosting or local dev).

Setup, done once:

1. Create a free Supabase project at [supabase.com](https://supabase.com).
2. Open the SQL editor and run [`drafts/schema.sql`](drafts/schema.sql) — it
   creates the one table this needs (`prompt_drafts`).
3. Project Settings → API: copy the **Project URL** and the **`service_role`
   secret key** — not the `anon` key. This app has no per-user login of its
   own (see "The app has no login" above), so there's no real identity for a
   row-level-security policy to key off; `service_role` bypasses RLS
   entirely rather than pretending a policy adds protection it can't.
4. Add both as **top-level** Streamlit secrets (App menu → Settings →
   Secrets), the same way the provider keys already are — see the sectioned-
   vs-top-level warning under Option A, step 3:
   ```
   SUPABASE_URL = "https://xxxxx.supabase.co"
   SUPABASE_KEY = "eyJ..."
   ```
5. Reload the app. The red ephemeral-storage warning on the Prompts and
   Handoff pages should disappear — `storage_is_ephemeral()` returns `False`
   once Supabase is configured, regardless of the host's own filesystem.

Treat `SUPABASE_KEY` like any other credential in this project: it goes in
secrets, never in a commit. Unlike the provider API keys, it's not scoped to
one feature — it's full read/write access to this Supabase project's tables.

### Alternatives, if Supabase doesn't fit

- **Self-host instead** (Option B). No code change at all; the filesystem is
  real, so local files just work.
- **Commit drafts to GitHub from the app.** Drafts are already plain files
  by design (mirrored into `draft_text` as one field in the Supabase table,
  but still generated the same way), and the repo is already where
  developers pick them up — write through the GitHub API with a fine-grained
  token instead of to a database. Gives anyone who can reach the app commit
  access to that repo, so pair it with the viewer allow-list. Nobody has
  built this path; Supabase covers the same need with less new code.

`results/runs.jsonl` has the same ephemerality caveat and is **not** covered
by the Supabase switch above — it's a separate file, still local-only. It's
a convenience log rather than a workflow artifact, so losing it costs you
history, not work; worth revisiting the same way if that stops being true.

---

## Before you deploy

```bash
python test_engine.py
```

Dry run, no API keys needed. Validates prompt assembly and input adaptation for
all four use cases. If this fails, the deployment will too.
