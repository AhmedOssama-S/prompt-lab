"""Handoff page: approved prompt proposals, in a form a developer can act on.

This is the other half of the Prompts page. The business team approves wording;
this page turns each approval into "change this exact file, here is the diff,
here is the evidence it was better". Applying it is a manual, deliberate step --
Prompt Lab never writes to the Medals-AI repo.

Note the two-hop path: a change lands in prompt-lab/prompts/ (so future
comparisons baseline against it) AND in the Medals-AI function that actually
ships. Missing the second is the easy mistake, so both are spelled out.
"""

import streamlit as st

from drafts import store

st.title(":material/engineering: Handoff")
st.caption(
    "Approved prompt changes waiting to be applied to the codebase. "
    "Prompt Lab does not modify any project — everything here is a manual change for a developer to make."
)

if store.storage_is_ephemeral():
    st.error(store.EPHEMERAL_WARNING, icon=":material/warning:")

# Where each use case's prompts live in the real repo. Attempt Comparator's text
# is byte-identical across generations, and Pillar Summarizer/Report Evaluator
# keep v1 and v2 in different function folders -- so the mapping isn't uniform.
_MEDALS_PATHS = {
    ("record_evaluator", "shared"): [
        "azure-functions/record-evaluator/core/evaluator.py",
        "azure-functions-unified/record-evaluator/core/evaluator.py",
    ],
    ("report_evaluator", "v1"): ["azure-functions/report-evaluator/core/evaluator.py"],
    ("report_evaluator", "v2"): ["azure-functions-unified/report-evaluator/core/evaluator.py"],
    ("attempt_comparator", "shared"): [
        "azure-functions/attempt-comparator/core/comparator.py",
        "azure-functions-unified/attempt-comparator/core/comparator.py",
    ],
    ("pillar_summarizer", "v1"): ["azure-functions/pillar-summarizer/core/utils.py"],
    ("pillar_summarizer", "v2"): ["azure-functions-unified/pillar-summarizer/core/utils.py"],
}

_BUILDER_HINT = {
    "overall_": "create_overall_summary_prompt()",
    "retry_expand_": "create_retry_prompt(), expansion branch",
    "retry_condense_": "create_retry_prompt(), condensation branch",
    "final_retry_expand_": "create_final_retry_prompt(), undershoot branch",
    "final_retry_reduce_": "create_final_retry_prompt(), overshoot branch",
    "pillar_eval/": "_create_pillar_evaluation_prompt()",
    "executive_summary/": "the executive-summary prompt builder",
    "main_prompt": "_create_comparison_prompt()",
    "title_generator": "_generate_achievement_titles()",
    "system_message": "the system message constant",
    "terminology_rule": "the terminology-rule branch inside _create_comparison_prompt()",
}


def _builder_hint(file_name: str) -> str:
    for key, hint in _BUILDER_HINT.items():
        if file_name.startswith(key) or key in file_name:
            lang = "Arabic" if "_ar" in file_name or file_name.endswith("/ar.txt") else (
                "English" if "_en" in file_name or file_name.endswith("/en.txt") else "")
            return f"{hint}{f' — {lang} branch' if lang else ''}"
    return "the corresponding prompt builder"


approved = [d for d in store.load_all() if d.status == store.STATUS_APPROVED]
applied = [d for d in store.load_all() if d.status == store.STATUS_APPLIED]

if not approved:
    st.success("Nothing waiting. No approved proposals to apply.", icon=":material/check_circle:")
else:
    st.info(
        f"**{len(approved)}** approved change{'s' if len(approved) > 1 else ''} waiting to be applied.",
        icon=":material/assignment:",
    )

for d in approved:
    with st.container(border=True):
        st.subheader(d.title)
        st.caption(
            f"Approved by {d.status_changed_by or d.author} on "
            f"{(d.status_changed_at or d.updated_at)[:16].replace('T', ' ')} UTC · originally written by {d.author}"
        )
        if d.rationale:
            st.markdown(f"**Goal:** {d.rationale}")
        if d.status_note:
            st.markdown(f"**Approval note:** {d.status_note}")

        if d.is_stale():
            st.warning(
                "The prompt file changed after this was approved. Re-read the diff before applying — "
                "it may already be partly done, or may now conflict.",
                icon=":material/warning:",
            )

        ev = d.test_evidence or {}
        if ev.get("tested_at"):
            c = st.columns(4)
            c[0].metric("Auto-judge", ev.get("judge_winner_label", "—"))
            c[1].metric("Proposed", ev.get("score_draft", "—"))
            c[2].metric("Current", ev.get("score_baseline", "—"))
            c[3].metric("Model tested", ev.get("model", "—"))
        else:
            st.warning("Approved without a recorded test run.", icon=":material/science:")

        st.markdown("**1. Update Prompt Lab's copy** — so future comparisons baseline against the new text:")
        st.code(f"{d.target_path}", language="text")

        medals = _MEDALS_PATHS.get((d.use_case, d.version), [])
        st.markdown(
            f"**2. Update the shipping code** — the prompt lives inline in "
            f"`{_builder_hint(d.file_name)}`, not in a text file:"
        )
        if medals:
            st.code("\n".join(f"Medals-AI/{p}" for p in medals), language="text")
            if len(medals) > 1:
                st.caption(
                    ":material/warning: Two files — this prompt's text is identical across both generations, "
                    "so changing only one silently splits them apart."
                )
        else:
            st.caption("No mapping recorded for this use case/version; find the prompt builder by searching for its text.")

        st.markdown("**Diff**")
        st.code(d.diff() or "(identical — nothing to apply)", language="diff")

        with st.expander("Full proposed text (copy this)"):
            st.code(d.text(), language="text")
            st.caption(
                "Remember the escaping difference: in the shipping code these prompts are f-strings, so a literal "
                "curly brace is written `{{` / `}}` — exactly as it appears above. Placeholders like `{min_words}` "
                "stay single-braced."
            )

        n1, n2 = st.columns([3, 1])
        with n1:
            note = st.text_input("Note (optional)", key=f"h_note_{d.draft_id}",
                                 placeholder="e.g. applied in commit abc1234")
        with n2:
            st.write("")
            if st.button("Mark as applied", key=f"h_ok_{d.draft_id}", type="primary", icon=":material/task_alt:"):
                store.set_status(d, store.STATUS_APPLIED, by=st.session_state.get("author_name", "developer"), note=note)
                st.rerun()

if applied:
    st.divider()
    with st.expander(f"Previously applied ({len(applied)})"):
        for d in applied:
            st.markdown(
                f"- **{d.title}** — `{d.target_path}` · applied "
                f"{(d.status_changed_at or d.updated_at)[:10]}"
                + (f" · {d.status_note}" if d.status_note else "")
            )
