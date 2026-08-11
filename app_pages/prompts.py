"""Prompts page: write a new version of a prompt, test it, and propose it.

Audience is the business/content team, not developers. Nothing here writes to
`prompts/` or reaches production -- approving a draft raises a flag that a
developer picks up on the Handoff page. That separation is deliberate: the
people who know what a good nomination summary reads like are not the people
who should be editing a live deployment.
"""

import streamlit as st

from drafts import store

st.title(":material/edit_note: Prompts")
st.caption(
    "Write a new version of a prompt, try it against the current one, and propose it if it's better. "
    "Nothing here changes the live system — an approved proposal goes to a developer to apply."
)

if store.storage_is_ephemeral():
    st.error(store.EPHEMERAL_WARNING, icon=":material/warning:")


def _author() -> str:
    """Remembered across pages so nobody retypes it every save."""
    return st.session_state.get("author_name", "")


# ---------------------------------------------------------------- labels

_USE_CASE_LABELS = {
    "record_evaluator": "Record Evaluator",
    "report_evaluator": "Report Evaluator",
    "attempt_comparator": "Attempt Comparator",
    "pillar_summarizer": "Pillar Summarizer",
}

_FILE_HINTS = {
    "overall_": "First attempt — writes the summary from scratch",
    "retry_expand_": "Used when the first attempt came out too short",
    "retry_condense_": "Used when the first attempt came out too long",
    "final_retry_expand_": "Last resort — mechanical edit to lengthen",
    "final_retry_reduce_": "Last resort — mechanical edit to shorten",
    "pillar_eval/main_": "Main body of the pillar evaluation",
    "pillar_eval/ld_block_": "Extra block, only for the Learning & Development pillar",
    "pillar_eval/shared_tail_": "Shared ending, appended to every pillar evaluation",
    "executive_summary/": "The report's executive summary",
    "main_prompt": "The main comparison prompt",
    "title_generator": "Generates short titles for each achievement first",
    "system_message": "Instruction sent to the model before the prompt itself",
    "terminology_rule_with_titles": "Wording rule used when titles were generated successfully",
    "terminology_rule_without_titles": "Wording rule used when title generation failed",
}


def _file_hint(name: str) -> str:
    for key, hint in _FILE_HINTS.items():
        if name.startswith(key) or key in name:
            lang = " (Arabic)" if name.endswith("_ar.txt") or name.endswith("/ar.txt") else (
                " (English)" if name.endswith("_en.txt") or name.endswith("/en.txt") else "")
            return hint + lang
    return ""


def _describe(use_case: str, version: str, file_name: str) -> str:
    uc = _USE_CASE_LABELS.get(use_case, use_case)
    if version == "shared":
        return f"{uc} · {file_name}"
    return f"{uc} · {version} · {file_name}"


tab_new, tab_existing = st.tabs(["Write a new version", "Proposals"])


# ================================================================ new draft

with tab_new:
    tree = store.editable_prompt_files()

    c1, c2, c3 = st.columns(3)
    with c1:
        uc_label = st.selectbox(
            "Which part of the system?",
            [_USE_CASE_LABELS.get(k, k) for k in tree],
            key="draft_uc",
        )
        use_case = next(k for k in tree if _USE_CASE_LABELS.get(k, k) == uc_label)
    with c2:
        versions = list(tree[use_case])
        version = st.selectbox(
            "Version to base it on",
            versions,
            index=len(versions) - 1,  # newest version is the sensible default
            key="draft_version",
            help="Start from the version currently in use, unless you're deliberately reviving an older one.",
        )
    with c3:
        file_name = st.selectbox("Which prompt?", tree[use_case][version], key="draft_file")

    hint = _file_hint(file_name)
    if hint:
        st.caption(f":material/info: {hint}")

    baseline = store.read_stored_prompt(use_case, version, file_name)
    ph = sorted(store.placeholders(baseline))
    if ph:
        st.caption(
            ":material/data_object: Placeholders filled in automatically at run time — keep them exactly as they are: "
            + " ".join(f"`{{{p}}}`" for p in ph)
        )

    # Reset the editor when the target file changes, otherwise the textarea keeps
    # showing the previous file's text under a new heading.
    target_key = (use_case, version, file_name)
    if st.session_state.get("_draft_target") != target_key:
        st.session_state["_draft_target"] = target_key
        st.session_state["draft_text"] = baseline

    st.text_area(
        "Your version",
        key="draft_text",
        height=380,
        help="Edit freely. Curly braces that aren't placeholders must be doubled — {{ and }}.",
    )

    draft_text = st.session_state.get("draft_text", "")
    changed = draft_text != baseline

    if changed:
        with st.expander("What you changed", expanded=False):
            st.code(store.diff_against_stored(draft_text, baseline, f"{use_case}/{version}/{file_name}"), language="diff")

    # Live validation, so problems surface while editing rather than on save.
    validation_error = None
    warnings = []
    if changed:
        try:
            warnings = store.validate_draft(draft_text, baseline)
        except store.DraftValidationError as e:
            validation_error = str(e)

    if validation_error:
        st.error(validation_error, icon=":material/error:")
    for w in warnings:
        st.warning(w, icon=":material/warning:")

    st.divider()
    m1, m2 = st.columns(2)
    with m1:
        title = st.text_input("Give it a short name", key="draft_title", placeholder="e.g. Shorter closing sentence")
        author = st.text_input("Your name", value=_author(), key="draft_author")
    with m2:
        rationale = st.text_area(
            "What are you trying to improve?", key="draft_rationale", height=110,
            placeholder="e.g. The closing sentence keeps mentioning Vision 2031 even when the source doesn't.",
        )

    can_save = bool(changed and title.strip() and author.strip() and not validation_error)
    if not changed:
        st.caption("Make a change above to save it as a proposal.")
    elif validation_error:
        st.caption("Fix the problem above to save.")
    elif not (title.strip() and author.strip()):
        st.caption("Add a short name and your name to save.")

    if st.button("Save as a proposal", type="primary", icon=":material/save:", disabled=not can_save):
        st.session_state["author_name"] = author.strip()
        try:
            draft = store.save_new(
                title=title, use_case=use_case, version=version, file_name=file_name,
                author=author, draft_text=draft_text, rationale=rationale,
            )
        except store.DraftValidationError as e:
            st.error(str(e), icon=":material/error:")
        else:
            st.session_state["test_draft_id"] = draft.draft_id
            st.success(
                f"Saved as **{draft.title}**. Next: open **Compare** and pick "
                f"*Draft: {draft.title}* as one side to see it against the current version.",
                icon=":material/check_circle:",
            )


# ================================================================ proposals

with tab_existing:
    all_drafts = store.load_all()
    if not all_drafts:
        st.info("No proposals yet. Write one in the first tab.", icon=":material/info:")
        st.stop()

    status_filter = st.segmented_control(
        "Show", ["All", "Draft", "Approved", "Rejected", "Applied"], default="All", key="draft_status_filter"
    ) or "All"
    shown = all_drafts if status_filter == "All" else [d for d in all_drafts if d.status == status_filter.lower()]
    if not shown:
        st.caption("Nothing with that status.")

    _STATUS_ICON = {
        store.STATUS_DRAFT: ":material/edit:",
        store.STATUS_APPROVED: ":material/verified:",
        store.STATUS_REJECTED: ":material/cancel:",
        store.STATUS_APPLIED: ":material/task_alt:",
    }

    for d in shown:
        tested = bool((d.test_evidence or {}).get("tested_at"))
        header = f"{_STATUS_ICON.get(d.status, '')} **{d.title}** — {_describe(d.use_case, d.version, d.file_name)}"
        with st.expander(header, expanded=False):
            st.caption(
                f"{store.STATUS_LABELS.get(d.status, d.status)} · by {d.author} · updated {d.updated_at[:16].replace('T', ' ')} UTC"
            )
            if d.rationale:
                st.markdown(f"**Goal:** {d.rationale}")

            if d.is_stale():
                st.warning(
                    "The current version of this prompt has changed since this proposal was written. "
                    "The comparison below is against the *new* current version, so re-check it still makes sense.",
                    icon=":material/warning:",
                )

            ev = d.test_evidence or {}
            if tested:
                cols = st.columns(3)
                cols[0].metric("Auto-judge", ev.get("judge_winner_label", "—"))
                cols[1].metric("This version", ev.get("score_draft", "—"))
                cols[2].metric("Current version", ev.get("score_baseline", "—"))
                st.caption(f"Tested {ev['tested_at'][:16].replace('T', ' ')} UTC on {ev.get('model', '?')}")
            else:
                st.warning("Not tested yet — run it on the Compare page before approving.", icon=":material/science:")

            st.code(d.diff() or "(identical to the current version)", language="diff")

            if d.status_note:
                st.caption(f":material/sticky_note_2: {d.status_note}")

            st.divider()
            a1, a2, a3 = st.columns([2, 1, 1])
            with a1:
                note = st.text_input("Note (optional)", key=f"note_{d.draft_id}", placeholder="Why approve or reject?")
            with a2:
                st.write("")
                if d.status != store.STATUS_APPROVED:
                    if st.button("Approve", key=f"ok_{d.draft_id}", type="primary", icon=":material/verified:",
                                 disabled=not tested,
                                 help=None if tested else "Test it on the Compare page first."):
                        store.set_status(d, store.STATUS_APPROVED, by=_author() or d.author, note=note)
                        st.rerun()
            with a3:
                st.write("")
                if d.status != store.STATUS_REJECTED:
                    if st.button("Reject", key=f"no_{d.draft_id}", icon=":material/cancel:"):
                        store.set_status(d, store.STATUS_REJECTED, by=_author() or d.author, note=note)
                        st.rerun()

            if d.status == store.STATUS_APPROVED:
                st.success(
                    "Approved. A developer will see this on the **Handoff** page with the exact file to change.",
                    icon=":material/check_circle:",
                )
