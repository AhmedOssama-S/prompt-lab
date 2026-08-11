"""Storage and validation for business-team prompt drafts.

A draft is a proposed replacement for exactly ONE stored prompt file, based on
a baseline version. It never touches `prompts/` and never reaches production --
approving one is a signal to a developer, not a deploy. The developer view
(app_pages/handoff.py) turns an approved draft into "here is the file to change
and here is the diff".

On-disk layout, one directory per draft:

    drafts/<draft_id>/
        meta.json    -- provenance, status, test evidence
        draft.txt    -- the proposed prompt text, raw

Two files rather than one JSON blob on purpose: `draft.txt` sits next to the
real thing in the same encoding, so a developer can diff it directly with any
tool (`diff drafts/<id>/draft.txt prompts/<use_case>/<version>/<file>`) instead
of un-escaping Arabic out of a JSON string.
"""

import difflib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import supabase_backend

DRAFTS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = DRAFTS_DIR.parent / "prompts"


def _use_supabase() -> bool:
    """True when drafts should be read/written through Supabase instead of
    local files -- set SUPABASE_URL and SUPABASE_KEY (a service_role key,
    not anon) to enable. See supabase_backend.py and schema.sql."""
    return supabase_backend.is_configured()

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_APPLIED = "applied"

STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED, STATUS_APPLIED)

STATUS_LABELS = {
    STATUS_DRAFT: "Draft — still being worked on",
    STATUS_APPROVED: "Approved — waiting for a developer to apply it",
    STATUS_REJECTED: "Rejected — kept for the record",
    STATUS_APPLIED: "Applied — a developer has put this in the codebase",
}


class DraftValidationError(Exception):
    """The draft text can't be used as a prompt template."""


def storage_is_ephemeral() -> bool:
    """True when drafts written here will not survive an app restart.

    Streamlit Community Cloud runs apps in a container with a non-persistent
    filesystem -- its own docs say local storage "may be deleted at any time"
    -- and it reboots on inactivity, redeploy, or resource pressure. Drafts are
    plain files, so on Community Cloud a business user's proposal silently
    disappears somewhere between writing it and a developer picking it up.

    None of that applies once Supabase is configured -- drafts are database
    rows at that point, not files, so the container's own filesystem is
    irrelevant and this always returns False regardless of host.

    Otherwise detected via Community Cloud's repo mount path, with an
    explicit override for any other ephemeral host (containers, PaaS dynos).
    Set PROMPT_LAB_PERSISTENT_STORAGE=1 to suppress the warning when the
    filesystem really is durable (a VM, a mounted volume).
    """
    if _use_supabase():
        return False
    if os.environ.get("PROMPT_LAB_PERSISTENT_STORAGE") == "1":
        return False
    if os.environ.get("PROMPT_LAB_EPHEMERAL_STORAGE") == "1":
        return True
    return "/mount/src" in str(DRAFTS_DIR).replace("\\", "/")


EPHEMERAL_WARNING = (
    "**Proposals saved here will be lost when the app restarts.** This deployment has "
    "temporary storage, so anything written now disappears on the next reboot, redeploy, "
    "or idle timeout. Copy any wording you want to keep somewhere safe, and tell whoever "
    "set this up — see DEPLOY.md, 'Making drafts survive'."
)


# ---------------------------------------------------------------- discovery

def editable_prompt_files() -> Dict[str, Dict[str, List[str]]]:
    """{use_case: {version: [relative file name, ...]}} for every .txt under prompts/.

    Only .txt files are offered. `final_retry_operations.json` is deliberately
    excluded -- it holds the six tiered edit instructions per language, which are
    prompt *fragments* but not templates, and editing them safely needs a
    different UI than a textarea.

    Attempt Comparator has no per-version split (its text is byte-identical
    between v1 and v2), so its files land under the pseudo-version "shared".
    """
    out: Dict[str, Dict[str, List[str]]] = {}
    for use_case_dir in sorted(PROMPTS_DIR.iterdir()):
        if not use_case_dir.is_dir():
            continue
        versions: Dict[str, List[str]] = {}
        for txt in sorted(use_case_dir.rglob("*.txt")):
            rel = txt.relative_to(use_case_dir)
            version = rel.parts[0] if len(rel.parts) > 1 else "shared"
            name = "/".join(rel.parts[1:]) if len(rel.parts) > 1 else rel.name
            versions.setdefault(version, []).append(name)
        if versions:
            out[use_case_dir.name] = versions
    return out


def read_stored_prompt(use_case: str, version: str, file_name: str) -> str:
    return _stored_path(use_case, version, file_name).read_text(encoding="utf-8")


def _stored_path(use_case: str, version: str, file_name: str) -> Path:
    base = PROMPTS_DIR / use_case
    return base / file_name if version == "shared" else base / version / file_name


def override_key(use_case: str, version: str, file_name: str) -> Tuple[str, ...]:
    """The exact *parts tuple prompt_loader._read() will be called with, so a
    draft can be handed to runner.prompt_overrides.prompt_override()."""
    parts = [use_case] if version == "shared" else [use_case, version]
    parts.extend(file_name.split("/"))
    return tuple(parts)


# ---------------------------------------------------------------- validation

_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def placeholders(text: str) -> set:
    """Placeholder names in a template, ignoring {{escaped}} literal braces."""
    # Blank out escaped braces first so {{"summary": ...}} in a JSON example
    # can't be misread as a placeholder named `"summary"`.
    return set(_PLACEHOLDER_RE.findall(text.replace("{{", "\0").replace("}}", "\0")))


def validate_draft(draft_text: str, baseline_text: str) -> List[str]:
    """Raises DraftValidationError on anything that would break a run.
    Returns a list of non-fatal warnings.

    The failure this exists to prevent: a single unescaped `{` or `}` typed into
    the editor. Python's .format() would raise mid-run, after the model call in
    some paths, producing a confusing failure far from its cause.
    """
    problems = []

    try:
        draft_text.format(**{name: "x" for name in placeholders(draft_text)})
    except (KeyError, IndexError, ValueError) as e:
        problems.append(
            f"The text isn't a valid template ({type(e).__name__}: {e}). "
            "Most likely a stray { or } — literal braces must be doubled as {{ and }}."
        )
    if problems:
        raise DraftValidationError("\n\n".join(problems))

    baseline_ph = placeholders(baseline_text)
    draft_ph = placeholders(draft_text)

    missing = baseline_ph - draft_ph
    if missing:
        # Fatal: the engine passes these by name. A dropped placeholder means
        # the model silently never sees that input -- e.g. losing {data_text}
        # from a summarizer prompt leaves it summarizing nothing at all.
        raise DraftValidationError(
            "This version drops placeholder(s) the original uses: "
            + ", ".join(f"{{{p}}}" for p in sorted(missing))
            + ".\n\nThose get filled in with real data at run time, so removing one means the model "
              "never sees that information. Put it back, or ask a developer if it's genuinely no longer needed."
        )

    warnings = []
    added = draft_ph - baseline_ph
    if added:
        # Fatal too, but worth a distinct message: .format() raises KeyError for
        # a name the engine doesn't supply.
        raise DraftValidationError(
            "This version adds placeholder(s) that don't exist: "
            + ", ".join(f"{{{p}}}" for p in sorted(added))
            + ".\n\nThe tool only knows how to fill in: "
            + ", ".join(f"{{{p}}}" for p in sorted(baseline_ph))
            + ".\n\nIf you meant to write a literal curly brace, double it: {{ or }}."
        )

    if not draft_text.strip():
        raise DraftValidationError("The prompt is empty.")

    ratio = len(draft_text) / max(len(baseline_text), 1)
    if ratio < 0.5:
        warnings.append(f"This is {100 - ratio * 100:.0f}% shorter than the current version — double-check nothing was deleted by accident.")
    elif ratio > 2.0:
        warnings.append(f"This is {ratio:.1f}× longer than the current version.")

    if "‏" in draft_text or "‎" in draft_text:
        warnings.append("Contains invisible left/right-to-left marks — usually harmless, but they can come from pasting out of Word.")

    return warnings


def diff_against_stored(draft_text: str, baseline_text: str, file_label: str) -> str:
    return "".join(difflib.unified_diff(
        baseline_text.splitlines(keepends=True),
        draft_text.splitlines(keepends=True),
        fromfile=f"a/{file_label}  (current)",
        tofile=f"b/{file_label}  (proposed)",
    ))


# ---------------------------------------------------------------- model + CRUD

@dataclass
class Draft:
    draft_id: str
    title: str
    use_case: str
    version: str          # the baseline this is proposed against
    file_name: str
    author: str
    status: str
    rationale: str = ""
    created_at: str = ""
    updated_at: str = ""
    status_note: str = ""
    status_changed_by: str = ""
    status_changed_at: str = ""
    # Evidence from the Compare run that justified approval, if any.
    test_evidence: Optional[Dict[str, Any]] = None
    history: List[Dict[str, str]] = field(default_factory=list)

    @property
    def target_path(self) -> str:
        """Repo-relative path of the file a developer needs to change."""
        return str(_stored_path(self.use_case, self.version, self.file_name).relative_to(PROMPTS_DIR.parent)).replace("\\", "/")

    def text(self) -> str:
        """The draft's proposed text.

        Not a dataclass field on purpose: draft_text is set as a plain
        instance attribute by load()/load_all()/save_new() when the
        Supabase backend supplies it directly on the row, so it never gets
        serialized into local meta.json or accepted as a Draft(**meta)
        constructor kwarg -- only the local-disk backend still needs to
        read draft.txt itself.
        """
        cached = getattr(self, "draft_text", None)
        if cached is not None:
            return cached
        return (DRAFTS_DIR / self.draft_id / "draft.txt").read_text(encoding="utf-8")

    def baseline_text(self) -> str:
        return read_stored_prompt(self.use_case, self.version, self.file_name)

    def diff(self) -> str:
        return diff_against_stored(self.text(), self.baseline_text(), self.target_path)

    def is_stale(self) -> bool:
        """True if the stored file changed since this draft was written --
        i.e. a developer already applied something, or another draft landed.
        The diff shown to a developer would otherwise be against a baseline
        that no longer exists."""
        return self.baseline_text() != (self.test_evidence or {}).get("baseline_at_save", self.baseline_text())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(title: str) -> str:
    norm = unicodedata.normalize("NFKD", title)
    ascii_ish = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return ascii_ish[:40] or "draft"


def _new_id(title: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{_slugify(title)}"


def save_new(
    *, title: str, use_case: str, version: str, file_name: str,
    author: str, draft_text: str, rationale: str = "",
) -> Draft:
    baseline = read_stored_prompt(use_case, version, file_name)
    validate_draft(draft_text, baseline)  # raises

    draft = Draft(
        draft_id=_new_id(title), title=title.strip(), use_case=use_case, version=version,
        file_name=file_name, author=author.strip() or "(unnamed)", status=STATUS_DRAFT,
        rationale=rationale.strip(), created_at=_now(), updated_at=_now(),
        test_evidence={"baseline_at_save": baseline},
        history=[{"at": _now(), "by": author.strip() or "(unnamed)", "action": "created"}],
    )
    _write(draft, draft_text)
    return draft


def update_text(draft: Draft, draft_text: str, *, by: str) -> Draft:
    validate_draft(draft_text, draft.baseline_text())  # raises
    draft.updated_at = _now()
    draft.history.append({"at": _now(), "by": by or "(unnamed)", "action": "edited"})
    # Re-testing invalidates any prior approval evidence, but keep the baseline
    # snapshot so staleness detection still works.
    baseline_snapshot = (draft.test_evidence or {}).get("baseline_at_save", draft.baseline_text())
    draft.test_evidence = {"baseline_at_save": baseline_snapshot}
    if draft.status == STATUS_APPROVED:
        draft.status = STATUS_DRAFT
        draft.status_note = "Reset to draft: the text was edited after approval."
    _write(draft, draft_text)
    return draft


def set_status(draft: Draft, status: str, *, by: str, note: str = "",
               evidence: Optional[Dict[str, Any]] = None) -> Draft:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")
    draft.status = status
    draft.status_note = note.strip()
    draft.status_changed_by = by.strip() or "(unnamed)"
    draft.status_changed_at = _now()
    draft.updated_at = _now()
    if evidence:
        draft.test_evidence = {**(draft.test_evidence or {}), **evidence}
    draft.history.append({"at": _now(), "by": draft.status_changed_by, "action": status, "note": note.strip()})
    _write(draft, None)
    return draft


def record_test(draft: Draft, evidence: Dict[str, Any]) -> Draft:
    """Attach the outcome of a Compare run so approval has something behind it."""
    draft.test_evidence = {**(draft.test_evidence or {}), **evidence, "tested_at": _now()}
    draft.updated_at = _now()
    _write(draft, None)
    return draft


def _write(draft: Draft, draft_text: Optional[str]) -> None:
    if _use_supabase():
        # draft_text is None on metadata-only writes (set_status/record_test).
        # The caller's Draft was itself obtained via load()/load_all(), which
        # always populates draft.draft_text from the row -- so it's normally
        # already there. Only a stale/hand-built Draft would need the fetch.
        text = draft_text if draft_text is not None else getattr(draft, "draft_text", None)
        if text is None:
            existing = supabase_backend.load(draft.draft_id)
            if existing is None:
                raise ValueError(f"No existing row for draft {draft.draft_id!r} to carry draft_text forward from")
            text = existing["draft_text"]
        draft.draft_text = text
        row = asdict(draft)
        row["draft_text"] = text
        supabase_backend.save(row)
        return

    d = DRAFTS_DIR / draft.draft_id
    d.mkdir(parents=True, exist_ok=True)
    if draft_text is not None:
        draft.draft_text = draft_text
        (d / "draft.txt").write_text(draft_text, encoding="utf-8", newline="\n")
    payload = asdict(draft)
    (d / "meta.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _from_row(row: Dict[str, Any]) -> Draft:
    row = dict(row)
    text = row.pop("draft_text", None)
    draft = Draft(**row)
    draft.draft_text = text
    return draft


def load(draft_id: str) -> Draft:
    if _use_supabase():
        row = supabase_backend.load(draft_id)
        if row is None:
            raise FileNotFoundError(f"No draft {draft_id!r} in Supabase")
        return _from_row(row)
    meta = json.loads((DRAFTS_DIR / draft_id / "meta.json").read_text(encoding="utf-8"))
    return Draft(**meta)


def load_all() -> List[Draft]:
    if _use_supabase():
        out = []
        for row in supabase_backend.load_all():
            try:
                out.append(_from_row(row))
            except TypeError:
                continue  # a row with an unexpected shape shouldn't break the whole page
        return out
    out = []
    for d in sorted(DRAFTS_DIR.iterdir(), reverse=True):
        if d.is_dir() and (d / "meta.json").exists():
            try:
                out.append(load(d.name))
            except (json.JSONDecodeError, TypeError):
                continue  # a hand-edited/corrupt draft shouldn't break the whole page
    return out


def delete(draft_id: str) -> None:
    if _use_supabase():
        supabase_backend.delete(draft_id)
        return
    d = DRAFTS_DIR / draft_id
    for f in ("draft.txt", "meta.json"):
        (d / f).unlink(missing_ok=True)
    if d.exists() and not any(d.iterdir()):
        d.rmdir()
