"""Supabase-backed persistence for prompt drafts.

Used instead of local files when SUPABASE_URL/SUPABASE_KEY are configured --
see store.py's _use_supabase(). This is the fix for Streamlit Community
Cloud's ephemeral filesystem (DEPLOY.md, "Making drafts survive"): a draft
written here is a database row, not a file, so it survives the container
restarting.

Table: prompt_drafts -- see schema.sql for the DDL to run once in the
Supabase SQL editor before this module is used.

Uses the service_role key, not anon: this app has no per-user auth of its
own (author is a free-text field, not a real identity), so row-level
security policies would add no real access control here -- service_role
just bypasses that layer entirely, the same way a single shared API key
already gates every other provider in this project.
"""

import os
from typing import Any, Dict, List, Optional

TABLE = "prompt_drafts"

_client = None


def is_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL")) and bool(os.environ.get("SUPABASE_KEY"))


def _get_client():
    global _client
    if _client is None:
        from supabase import create_client  # local import: only needed when configured

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client


def save(row: Dict[str, Any]) -> None:
    _get_client().table(TABLE).upsert(row).execute()


def load(draft_id: str) -> Optional[Dict[str, Any]]:
    res = _get_client().table(TABLE).select("*").eq("draft_id", draft_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


def load_all() -> List[Dict[str, Any]]:
    res = _get_client().table(TABLE).select("*").order("draft_id", desc=True).execute()
    return res.data or []


def delete(draft_id: str) -> None:
    _get_client().table(TABLE).delete().eq("draft_id", draft_id).execute()
