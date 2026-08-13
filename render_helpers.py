"""Shared rendering helpers for the Compare page."""

import pandas as pd
import streamlit as st

from runner.judge import quote_found_in_output
from runner.judge_models import JudgeVerdict
from runner.models import (
    ComparisonResponse,
    CriterionEvaluationResponse,
    PillarSummaryResult,
    RubricEvaluationResponse,
)


def _bullet_list(items: list[str]) -> None:
    for item in items:
        st.markdown(f"- {item}")


def render_record_result(result: RubricEvaluationResponse) -> None:
    tab_ar, tab_en = st.tabs(["العربية", "English"])
    with tab_ar:
        st.markdown("**نقاط القوة**")
        _bullet_list(result.arabic_analysis.strengths)
        st.markdown("**مجالات التحسين**")
        _bullet_list(result.arabic_analysis.areas_for_improvement)
        st.markdown("**التوصيات**")
        _bullet_list(result.arabic_analysis.recommendations)
    with tab_en:
        st.markdown("**Strengths**")
        _bullet_list(result.english_analysis.strengths)
        st.markdown("**Areas for improvement**")
        _bullet_list(result.english_analysis.areas_for_improvement)
        st.markdown("**Recommendations**")
        _bullet_list(result.english_analysis.recommendations)


def render_report_pillar_result(result: CriterionEvaluationResponse) -> None:
    c1, c2 = st.columns(2)
    c1.metric("Achieved percentage", f"{result.achieved_percentage}%")
    c2.metric("Performance level", result.performance_level)
    st.markdown("**Rationale**")
    st.markdown(result.rationale)
    st.markdown("**Strengths**")
    _bullet_list(result.strengths)
    st.markdown("**Improvements**")
    _bullet_list(result.improvements)


def render_executive_summary_result(text: str) -> None:
    st.markdown(text)


def render_titles_status(titles: dict) -> None:
    """
    Attempt Comparator's two-stage call: shows whether title generation
    succeeded, and if so, the titles it produced -- so it's clear *why*
    the main output refers to achievements the way it does (by title, or
    by generic "Input N" phrasing when this step silently failed/was
    unavailable). See PHASE2_PLAN.md 1.6.
    """
    if not titles:
        st.caption(":material/info: No titles generated (gemini_flash not configured, or generation failed) -- "
                    "the main prompt falls back to \"Input N\" / \"المُدخل N\" phrasing, exactly like production.")
        return
    st.caption(":material/label: Achievement titles generated:")
    for idx in sorted(titles.keys()):
        t = titles[idx]
        st.caption(f"&nbsp;&nbsp;{idx}. {t['ar']} / {t['en']}")


def render_comparison_result(result: ComparisonResponse, record_id) -> None:
    st.caption(f":material/emoji_events: Winning record_id (as returned by the model, untyped -- see schemas/SCHEMAS.md): `{record_id}`")
    tab_ar, tab_en = st.tabs(["العربية", "English"])
    with tab_ar:
        st.markdown("**تحليل فردي**")
        st.markdown(result.arabic_analysis.individual_analysis)
        st.markdown("**تحليل مقارن**")
        st.markdown(result.arabic_analysis.comparative_analysis)
        st.markdown("**الترتيب النهائي**")
        st.markdown(result.arabic_analysis.final_ranking)
        st.markdown("**أفضل الممارسات**")
        st.markdown(result.arabic_analysis.best_practices)
    with tab_en:
        st.markdown("**Individual analysis**")
        st.markdown(result.english_analysis.individual_analysis)
        st.markdown("**Comparative analysis**")
        st.markdown(result.english_analysis.comparative_analysis)
        st.markdown("**Final ranking**")
        st.markdown(result.english_analysis.final_ranking)
        st.markdown("**Best practices**")
        st.markdown(result.english_analysis.best_practices)


def render_call_meta(attempts: int, latency_ms: float, model_key: str) -> None:
    st.caption(f":material/bolt: {model_key} · {attempts} attempt(s) · {latency_ms:.0f} ms")


# ---------- Pillar Summarizer ----------

_STAGE_LABELS = {
    "overall": "Overall (attempt 1)",
    "retry": "Adaptive retry",
    "final_retry": "Final retry (mechanical edit)",
}

_BRANCH_LABELS = {
    "expand": "expand",
    "condense": "condense",
    "reduce": "reduce",
}

_TIER_LABELS = {
    "le40": "≤40 over — delete/merge 2 sentences",
    "le80": "≤80 over — delete 4 sentences",
    "gt80": ">80 over — compress all + delete 4",
    "le30": "≤30 under — add 1 sentence",
    "le70": "≤70 under — add 2 sentences",
    "gt70": ">70 under — expand 2 + add 2",
}

_OUTCOME_LABELS = {
    "in_range": ("In range — returned early", "success"),
    "best_effort_not_too_long": ("Never converged — returned closest attempt that wasn't over the limit", "warning"),
    "best_effort_closest": ("Never converged — every attempt overshot, returned the closest", "error"),
}


def render_summary_result(result: PillarSummaryResult) -> None:
    """The final text, plus the word-count verdict that drove the retry loop."""
    delta = result.word_count - result.target_words
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Word count", result.word_count,
        delta=f"{delta:+d} vs target", delta_color="off" if result.trajectory and result.trajectory[0].in_range else "normal",
    )
    c2.metric("Target range", f"{result.min_acceptable}–{result.max_acceptable}")
    c3.metric("Attempts", result.attempts)

    label, kind = _OUTCOME_LABELS.get(result.outcome, (result.outcome, "info"))
    getattr(st, kind)(f"{label} (attempt {result.returned_attempt} of {len(result.trajectory)} completed)")

    st.markdown(f"**Summary** · reported language: `{result.detected_language}`")
    st.markdown(result.summary)


def render_attempt_trajectory(result: PillarSummaryResult) -> None:
    """How the loop converged (or didn't) -- the comparison-worthy part.

    A prompt version that lands in range on attempt 1 is meaningfully better
    than one that only gets there after the mechanical final-retry edit, even
    when both produce an acceptable final word count. That difference is
    invisible in the output text alone, which is why it gets its own panel.
    """
    if not result.trajectory:
        st.caption("No attempt completed.")
        return

    rows = []
    for a in result.trajectory:
        detail = _STAGE_LABELS.get(a.prompt_stage, a.prompt_stage)
        if a.branch:
            detail += f" · {_BRANCH_LABELS.get(a.branch, a.branch)}"
        if a.tier:
            detail += f" · {_TIER_LABELS.get(a.tier, a.tier)}"
        rows.append({
            "#": a.attempt,
            "Stage": detail,
            "Words": a.word_count,
            "Δ target": a.word_count - result.target_words,
            "In range": "yes" if a.in_range else "no",
            "Returned": "◀" if a.attempt == result.returned_attempt else "",
            "ms": round(a.latency_ms),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    if result.target_words != result.max_acceptable:  # defensive; they're equal by construction
        st.caption("Note: max_acceptable is the target itself — overshooting is always a miss.")

    remapped = [a for a in result.trajectory if a.remapped_from]
    if remapped:
        keys = ", ".join(sorted({f"`{a.remapped_from}`" for a in remapped}))
        st.warning(
            f"Model returned the report under the wrong JSON key ({keys}) on "
            f"{len(remapped)} attempt(s); v2's alternate-key rename rescued it. "
            "Counts as an instruction-following failure, not a clean run."
        )

    if result.swallowed_errors:
        st.warning(
            "Attempt(s) failed and were silently absorbed by production's retry loop:\n\n"
            + "\n".join(f"- {e}" for e in result.swallowed_errors)
        )

    for a in result.trajectory:
        with st.expander(f"Attempt {a.attempt} — prompt sent & raw response ({a.word_count} words)"):
            st.markdown("**Prompt**")
            st.code(a.prompt, language="text")
            st.markdown("**Raw response**")
            st.code(a.raw_text, language="json")


def render_judge_verdict(verdict: JudgeVerdict, model_key: str, output_a: dict, output_b: dict) -> None:
    st.subheader(":material/gavel: Auto-judge verdict")
    st.caption(f"Judged by {model_key} against this use case's own rule checklist (see prompts/*/judge_criteria.yaml)")

    winner_label = {"A": "Side A wins", "B": "Side B wins", "tie": "Tie"}.get(verdict.winner, verdict.winner)
    c1, c2, c3 = st.columns(3)
    c1.metric("Side A score", f"{verdict.overall_score_a:.0f}/100")
    c2.metric("Side B score", f"{verdict.overall_score_b:.0f}/100")
    c3.metric("Verdict", winner_label)

    st.markdown("**Recommendation**")
    st.markdown(verdict.recommendation)

    with st.expander("Per-criterion scores"):
        df = pd.DataFrame([
            {"Criterion": cs.criterion, "A": cs.score_a, "B": cs.score_b, "Notes": cs.notes}
            for cs in verdict.criterion_scores
        ])
        st.dataframe(df, width="stretch", hide_index=True)

        cited = [cs for cs in verdict.criterion_scores if cs.quote_a or cs.quote_b]
        if cited:
            st.markdown("**Cited lines**")
            st.caption(
                "The exact excerpt each score is pointing to, checked against that side's actual output -- "
                "not just the judge's word for it."
            )
            for cs in cited:
                with st.container(border=True):
                    st.markdown(f"**{cs.criterion}** — A: {cs.score_a}/10 · B: {cs.score_b}/10")
                    for side_label, quote, output in (("A", cs.quote_a, output_a), ("B", cs.quote_b, output_b)):
                        if not quote:
                            continue
                        verified = quote_found_in_output(quote, output)
                        if verified:
                            st.caption(f":material/check_circle: Side {side_label} — found verbatim in the output")
                        else:
                            st.caption(
                                f":material/help: Side {side_label} — couldn't locate this verbatim in the output "
                                "(the judge may have paraphrased or translated it)"
                            )
                        st.code(quote, language="text")
