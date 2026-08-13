"""
Compare page: upload a real request payload, pick a (version, model) for
Side A and Side B, run both, inspect outputs side by side. An auto-judge
(runner/judge.py) automatically scores both outputs against the use case's
own rule checklist right after both sides complete -- no separate page, no
extra click. A manual "your call" pick is still available alongside it and
gets logged to results/runs.jsonl together with the judge's verdict.
"""

import json

import streamlit as st

from drafts import store as draft_store
from render_helpers import (
    render_attempt_trajectory,
    render_call_meta,
    render_comparison_result,
    render_executive_summary_result,
    render_judge_verdict,
    render_record_result,
    render_report_pillar_result,
    render_summary_result,
    render_titles_status,
)
from results.logger import append_run
from runner.engine import (
    AllRetriesExhaustedError,
    CallResult,
    ModelTruncatedError,
    PillarSummarizerClaudeUnsupportedError,
    evaluate_comparison,
    evaluate_record,
    evaluate_report_pillar,
    generate_achievement_titles,
    generate_executive_summary,
    setup_clients,
    summarize_pillar_with_retry,
)
from runner.input_adapters import (
    adapt_attempt_comparator,
    adapt_pillar_summarizer,
    adapt_record_evaluator,
    adapt_report_evaluator_executive_summary,
    adapt_report_evaluator_pillars,
    build_achievements_text,
)
from runner.json_utils import JSONParseError, JSONValidationError
from runner.judge import NoJudgeModelAvailable, run_judge
from runner.prompt_overrides import prompt_override
from runner.prompt_loader import (
    render_attempt_comparator_prompt,
    render_record_evaluator_prompt,
    render_report_evaluator_executive_summary_prompt,
    render_report_evaluator_pillar_prompt,
)
from sample_payloads import (
    ATTEMPT_COMPARATOR_PAYLOAD,
    PILLAR_SUMMARIZER_PAYLOAD,
    RECORD_EVALUATOR_PAYLOAD,
    REPORT_EVALUATOR_SINGLE_SUMMARY_PAYLOAD,
)

st.title(":material/compare_arrows: Compare")
st.caption(
    "Run the same real input through two (version, model) combinations and see the outputs side by side. "
    "Prompt text and per-provider call parameters are reproduced exactly from production -- see schemas/SCHEMAS.md."
)

# Every (use_case, version) offers the SAME full model roster for that use
# case -- deliberately wider than production, so v1's prompt text can be
# tested against Core42 (and v2's against Claude/GPT-4o) even though that
# exact pairing never shipped. This is intentionally NOT "only combinations
# that exist in production" -- it's "any provider we know how to call
# correctly, against any version's prompt text." The per-provider call
# parameters (temperature, JSON mode, token limits) still come from
# schemas/SCHEMAS.md and never change based on which version's prompt is
# being sent through them -- see engine.py's dispatch functions.
#
# claude_sonnet is deliberately absent from every roster below. It was a real
# production provider for v1 of Record/Report Evaluator and Attempt Comparator,
# and engine.py still implements those call paths exactly (so schemas/SCHEMAS.md
# stays honest and test_engine.py --live still exercises them if an
# ANTHROPIC_API_KEY is present) -- it is simply not offered in the UI. Two
# reasons: v1's Pillar Summarizer Claude path is broken in production and can
# never succeed (see engine.py::_call_pillar_summarizer), and v2 dropped Claude
# from all four use cases, so nothing currently shipping uses it.
_RECORD_EVALUATOR_MODELS = ["gemini_flash", "gemini_pro", "core42_gpt-5.1", "core42_gpt-4.1"]
_REPORT_EVALUATOR_MODELS = ["gemini_flash", "gemini_pro", "gpt4o", "core42_gpt-5.1", "core42_gpt-4.1"]
# Attempt Comparator never had a GPT-4o path in either version -- same
# roster as Record Evaluator otherwise (prompt text is version-independent
# here too, see schemas/SCHEMAS.md provenance note).
_ATTEMPT_COMPARATOR_MODELS = _RECORD_EVALUATOR_MODELS
# Pillar Summarizer had the same v1 roster as Report Evaluator before v2
# swapped to Core42.
_PILLAR_SUMMARIZER_MODELS = _REPORT_EVALUATOR_MODELS

VALID_MODELS = {
    ("record_evaluator", "v1"): _RECORD_EVALUATOR_MODELS,
    ("record_evaluator", "v2"): _RECORD_EVALUATOR_MODELS,
    ("report_evaluator", "v1"): _REPORT_EVALUATOR_MODELS,
    ("report_evaluator", "v2"): _REPORT_EVALUATOR_MODELS,
    ("attempt_comparator", "v1"): _ATTEMPT_COMPARATOR_MODELS,
    ("attempt_comparator", "v2"): _ATTEMPT_COMPARATOR_MODELS,
    ("pillar_summarizer", "v1"): _PILLAR_SUMMARIZER_MODELS,
    ("pillar_summarizer", "v2"): _PILLAR_SUMMARIZER_MODELS,
}

# Combos production never actually paired -- shown as an informational note,
# not a warning, since they're fully supported here, just untested in the
# real deployment. Report Evaluator's v1+Core42 has one genuine behavioral
# note: v1 never received a JSON-format system message for ANY non-Gemini
# provider in production, and that omission is preserved for Core42 too
# (see runner/prompt_loader.py::load_report_evaluator_core42_system_message) --
# so v1+Core42 gets no system message, while v2+Core42 does.
_NON_NATIVE_COMBO_NOTES = {
    ("record_evaluator", "v1", "core42_gpt-5.1"): "Never paired in production (v1 predates Core42) -- testing v1's prompt text against this model.",
    ("record_evaluator", "v1", "core42_gpt-4.1"): "Never paired in production (v1 predates Core42) -- testing v1's prompt text against this model.",
    ("report_evaluator", "v1", "core42_gpt-5.1"): "Never paired in production. Also: v1 never gets a JSON-format system message for any non-Gemini provider -- that's preserved here, so this call gets none either (unlike v2+Core42).",
    ("report_evaluator", "v1", "core42_gpt-4.1"): "Never paired in production. Also: v1 never gets a JSON-format system message for any non-Gemini provider -- that's preserved here, so this call gets none either (unlike v2+Core42).",
    ("report_evaluator", "v2", "gpt4o"): "Never paired in production (v2 dropped Claude/GPT-4o) -- testing v2's prompt text (which has explicit JSON-key instructions built in) against this model.",
    ("attempt_comparator", "v1", "core42_gpt-5.1"): "Never paired in production (v1 predates Core42) -- testing v1's prompt text against this model.",
    ("attempt_comparator", "v1", "core42_gpt-4.1"): "Never paired in production (v1 predates Core42) -- testing v1's prompt text against this model.",
    ("pillar_summarizer", "v2", "gpt4o"): "Never paired in production (v2 dropped Claude/GPT-4o) -- testing v2's prompt text against this model.",
}

# Combos that don't just lack a production precedent but reliably FAIL. Shown as
# a warning rather than an info caption, before the run, so the failure is
# expected rather than surprising. The run is still allowed -- the failure IS
# the finding.
_KNOWN_FAILING_COMBOS = {
    ("pillar_summarizer", "v1", "core42_gpt-5.1"): (
        "Expected to fail. Core42 returns the report under \"report\", not \"summary\" -- v1's prompt never "
        "names the key and v1 has no rename fallback, so it fails on attempt 1. v2 fixed both. "
        "Run it to see the failure, or pick v2 / a Gemini model to get output."
    ),
    ("pillar_summarizer", "v1", "core42_gpt-4.1"): (
        "Expected to fail. Core42 returns the report under \"report\", not \"summary\" -- v1's prompt never "
        "names the key and v1 has no rename fallback, so it fails on attempt 1. v2 fixed both. "
        "Run it to see the failure, or pick v2 / a Gemini model to get output."
    ),
}


@st.cache_resource
def get_clients():
    return setup_clients()


clients = get_clients()
if not clients:
    st.warning(
        "No API keys configured. Copy `.env.example` to `.env` and fill in at least one provider's key, "
        "then restart the app. You can still explore the form below.",
        icon=":material/warning:",
    )

# ---------- Use case + input ----------

_USE_CASE_LABELS = {
    "Record Evaluator": "record_evaluator",
    "Report Evaluator": "report_evaluator",
    "Attempt Comparator": "attempt_comparator",
    "Pillar Summarizer": "pillar_summarizer",
}
use_case_label = st.segmented_control(
    "Use case", list(_USE_CASE_LABELS.keys()), default="Record Evaluator"
)
use_case = _USE_CASE_LABELS.get(use_case_label, "record_evaluator")

prompt_part = "pillar_eval"
if use_case == "report_evaluator":
    prompt_part_label = st.segmented_control(
        "Prompt part", ["Pillar evaluation", "Executive summary"], default="Pillar evaluation"
    )
    prompt_part = "pillar_eval" if prompt_part_label == "Pillar evaluation" else "executive_summary"


def _load_sample(sample: dict) -> None:
    st.session_state["payload_text"] = json.dumps(sample, ensure_ascii=False, indent=2)


def _default_sample_for(use_case: str) -> dict:
    # report_evaluator's single-summary sample is the only UI-facing sample
    # and works for BOTH prompt parts (pillar_eval and executive_summary
    # both accept the same summary_text/rubric_data shape). The multi-pillar
    # (pillar_summaries) request format is still supported by
    # input_adapters.py if a caller pastes one in directly -- it's just no
    # longer offered as a one-click sample here.
    if use_case == "record_evaluator":
        return RECORD_EVALUATOR_PAYLOAD
    if use_case == "attempt_comparator":
        return ATTEMPT_COMPARATOR_PAYLOAD
    if use_case == "pillar_summarizer":
        return PILLAR_SUMMARIZER_PAYLOAD
    return REPORT_EVALUATOR_SINGLE_SUMMARY_PAYLOAD


# Auto-fill a sample the moment the use case changes (including on first
# load), so the field is never empty -- switching use case is a value change
# just like any other widget, and gets the same "there's always something
# valid to run" treatment. Only fires on an actual change, so it never
# clobbers edits made while staying on the same use case.
if st.session_state.get("_last_use_case") != use_case:
    st.session_state["_last_use_case"] = use_case
    st.session_state["payload_text"] = json.dumps(_default_sample_for(use_case), ensure_ascii=False, indent=2)


def _judge_supported(use_case: str, prompt_part: str) -> bool:
    """Auto-judge criteria exist for record_evaluator, attempt_comparator,
    pillar_summarizer, and report_evaluator's pillar evaluation (see
    prompts/*/judge_criteria.yaml) -- not report_evaluator's executive summary."""
    return use_case in ("record_evaluator", "attempt_comparator", "pillar_summarizer") or (
        use_case == "report_evaluator" and prompt_part == "pillar_eval"
    )


def _build_judge_context(
    use_case: str,
    prompt_part: str,
    record_vars: dict | None,
    pillar_choice: dict | None,
    comparator_vars: dict | None = None,
    summarizer_vars: dict | None = None,
) -> str:
    if use_case == "record_evaluator":
        return f"Content to evaluate:\n{record_vars['input_content']}\n\nRubric:\n{record_vars['rubric_data']}"
    if use_case == "attempt_comparator":
        return (
            f"Achievements being compared:\n{comparator_vars['achievements']}\n\n"
            f"Rubric context:\n{comparator_vars['rubric_description']}{comparator_vars['rubric_type_context']}"
        )
    if use_case == "pillar_summarizer":
        # The judge needs the SOURCE rows to check the grounding and
        # no-fabricated-facts criteria at all -- without them it can only judge
        # style. data_text is exactly what the model itself was shown.
        return (
            f"Pillar: {summarizer_vars['pillar_name']}\n"
            f"Target word count: {summarizer_vars['target_words']} "
            f"(acceptable range {summarizer_vars['target_words'] - 100}-{summarizer_vars['target_words']})\n"
            f"Requested language: {summarizer_vars['language']}\n\n"
            f"Source rows the summary must be grounded in:\n{summarizer_vars['data_text']}"
        )
    return (
        f"Pillar: {pillar_choice['pillar_name']} (total weight {pillar_choice['total_weight']}%)\n\n"
        f"Summary text:\n{pillar_choice['summary_text']}\n\n"
        f"Sub-dimensions:\n{pillar_choice['subdimensions_text']}"
    )


def _summary_call_meta(result, model_key: str) -> CallResult:
    """Pillar Summarizer returns a PillarSummaryResult, not a (parsed, CallResult)
    pair, because its "attempts" are word-count retries with different prompts
    rather than same-call retries. Synthesize the CallResult the shared
    render_call_meta()/_serialize() helpers expect: total latency across all
    attempts, and the raw text of whichever attempt was actually returned."""
    returned = next((a for a in result.trajectory if a.attempt == result.returned_attempt), None)
    return CallResult(
        raw_text=returned.raw_text if returned else "",
        attempts=result.attempts,
        latency_ms=sum(a.latency_ms for a in result.trajectory),
        model_key=model_key,
    )


def _summary_judge_payload(result) -> dict:
    """What the auto-judge sees for a Pillar Summarizer side."""
    return {
        "summary": result.summary,
        "word_count": result.word_count,
        "reported_language": result.detected_language,
        "target_range": [result.min_acceptable, result.max_acceptable],
        "attempts_used": len(result.trajectory),
        "outcome": result.outcome,
        "convergence_trajectory": [
            {
                "attempt": a.attempt,
                "stage": a.prompt_stage,
                "branch": a.branch,
                "tier": a.tier,
                "word_count": a.word_count,
                "in_range": a.in_range,
                "wrong_json_key_used": a.remapped_from,
            }
            for a in result.trajectory
        ],
    }


def _serialize(result_entry: dict) -> dict:
    """Turns a side_results[label] entry (pydantic model / str + CallResult dataclass) into a JSON-safe dict for runs.jsonl."""
    if result_entry["kind"] == "error":
        return {"kind": "error", "error": result_entry["error"]}

    parsed = result_entry["parsed"]
    parsed_out = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
    meta = result_entry["meta"]
    out = {
        "kind": result_entry["kind"],
        "parsed": parsed_out,
        "attempts": meta.attempts,
        "latency_ms": meta.latency_ms,
        "raw_text": meta.raw_text,
    }
    if result_entry["kind"] == "comparison":
        out["record_id"] = result_entry["record_id"]
        out["titles"] = result_entry["titles"]
    return out


st.button(
    "Reset to sample",
    icon=":material/download:",
    on_click=_load_sample,
    args=(_default_sample_for(use_case),),
)

payload_text = st.text_area(
    "Request payload (JSON, shaped like the real request body)",
    key="payload_text",
    height=220,
)

try:
    payload = json.loads(payload_text)
    payload_error = None
except json.JSONDecodeError as e:
    payload = None
    payload_error = str(e)

if payload_error:
    st.error(f"Invalid JSON: {payload_error}", icon=":material/error:")

# For Report Evaluator, resolve the payload into per-pillar variables (or
# executive-summary variables) up front, so pillar selection and rendering
# both work off the same adapted data.
pillar_choice = None
adapted_pillars = []
if payload is not None and use_case == "report_evaluator":
    try:
        if prompt_part == "pillar_eval":
            adapted_pillars = adapt_report_evaluator_pillars(payload)
            if not adapted_pillars:
                st.error("No pillars found in this payload.", icon=":material/error:")
            elif len(adapted_pillars) == 1:
                pillar_choice = adapted_pillars[0]
            else:
                names = [p["pillar_name"] for p in adapted_pillars]
                chosen_name = st.selectbox("Pillar to evaluate", names)
                pillar_choice = next(p for p in adapted_pillars if p["pillar_name"] == chosen_name)
        else:
            exec_vars = adapt_report_evaluator_executive_summary(payload)
    except (ValueError, KeyError) as e:
        st.error(f"Payload doesn't match the expected schema: {e}", icon=":material/error:")
        payload = None
elif payload is not None and use_case == "record_evaluator":
    try:
        record_vars = adapt_record_evaluator(payload)
    except ValueError as e:
        st.error(f"Payload doesn't match the expected schema: {e}", icon=":material/error:")
        payload = None
elif payload is not None and use_case == "attempt_comparator":
    try:
        comparator_vars = adapt_attempt_comparator(payload)
    except ValueError as e:
        st.error(f"Payload doesn't match the expected schema: {e}", icon=":material/error:")
        payload = None
elif payload is not None and use_case == "pillar_summarizer":
    # Production fans all pillars out concurrently; Prompt Lab runs one at a
    # time (PHASE2_PLAN.md section 0) and lets the user pick which -- same
    # pattern as Report Evaluator's multi-pillar payloads above.
    try:
        _pillars = payload.get("pillar_data") or []
        _idx = 0
        if len(_pillars) > 1:
            _names = [f"{i + 1}. {p.get('pillar_name', '(unnamed)')}" for i, p in enumerate(_pillars)]
            _idx = _names.index(st.selectbox("Pillar to summarize", _names))
        summarizer_vars = adapt_pillar_summarizer(payload, _idx)
        if summarizer_vars["target_was_clamped"]:
            st.info(
                f"target_word_count {summarizer_vars['requested_target_words']} was clamped to "
                f"{summarizer_vars['target_words']} — production applies a hard 600-word ceiling before "
                "anything else, even though the request model accepts up to 2000.",
                icon=":material/info:",
            )
    except ValueError as e:
        st.error(f"Payload doesn't match the expected schema: {e}", icon=":material/error:")
        payload = None

st.divider()

# ---------- Side A / Side B pickers ----------

# Proposals from the Prompts page become selectable alongside v1/v2. A draft
# replaces exactly ONE prompt file and inherits everything else from the version
# it was written against, so it runs through the identical pipeline -- same
# fragments, same retry loop, same call parameters. That is the point: a draft
# has to be judged on what it would actually do in production.
_drafts_for_use_case = [d for d in draft_store.load_all() if d.use_case == use_case]
_draft_by_label = {f"Draft: {d.title}": d for d in _drafts_for_use_case}

col_a, col_b = st.columns(2)
sides = {}
for label, col in (("A", col_a), ("B", col_b)):
    with col:
        st.subheader(f"Side {label}")

        version_options = ["v1", "v2"] + list(_draft_by_label)
        default_version = "v1" if label == "A" else "v2"
        # A draft selected on the Prompts page pre-loads into side B, so
        # "save then test" is one click rather than a hunt through a dropdown.
        pending = st.session_state.get("test_draft_id")
        if label == "B" and pending:
            match = next((lbl for lbl, d in _draft_by_label.items() if d.draft_id == pending), None)
            if match and st.session_state.get("version_B") not in _draft_by_label:
                st.session_state["version_B"] = match
            st.session_state.pop("test_draft_id", None)

        version_choice = st.selectbox(
            f"Version ({label})", version_options,
            key=f"version_{label}",
            index=version_options.index(st.session_state.get(f"version_{label}", default_version))
            if st.session_state.get(f"version_{label}", default_version) in version_options else version_options.index(default_version),
        )

        draft = _draft_by_label.get(version_choice)
        # Model rosters and combo notes are keyed by the real version, so a
        # draft resolves to whichever version it was written against.
        version = draft.version if draft else version_choice
        if draft and version == "shared":
            # record_evaluator/attempt_comparator store prompts outside a
            # version folder; their rosters are keyed v1/v2 identically, so
            # either works for looking up the model list.
            version = "v1"

        model_options = VALID_MODELS[(use_case, version)]
        unavailable = [m for m in model_options if m not in clients]
        model = st.selectbox(
            f"Model ({label})",
            model_options,
            key=f"model_{label}",
            help=f"Not configured (missing API key): {', '.join(unavailable)}" if unavailable else None,
        )
        sides[label] = {"version": version, "model": model, "draft": draft, "version_label": version_choice}

        if draft:
            st.caption(
                f":material/edit_note: Proposal by {draft.author}, based on {draft.version} · "
                f"replaces `{draft.file_name}` · everything else runs as {draft.version} does."
            )

        failing_note = _KNOWN_FAILING_COMBOS.get((use_case, version, model))
        combo_note = _NON_NATIVE_COMBO_NOTES.get((use_case, version, model))
        if failing_note:
            st.warning(failing_note, icon=":material/warning:")
        elif combo_note:
            st.caption(f":material/info: {combo_note}")

run_clicked = st.button("Run comparison", type="primary", icon=":material/play_arrow:", width="stretch", disabled=payload is None)

# ---------- Run ----------

if run_clicked:
    st.session_state["last_use_case"] = use_case
    st.session_state["last_prompt_part"] = prompt_part
    st.session_state["last_payload"] = payload
    st.session_state["last_pillar"] = pillar_choice
    st.session_state["side_results"] = {}
    st.session_state["winner_logged"] = False

    for label, cfg in sides.items():
        version, model, draft = cfg["version"], cfg["model"], cfg["draft"]
        version_label = cfg["version_label"]
        # A draft swaps in exactly one prompt file, for the duration of this
        # side's run only. Empty when no draft is selected, so the normal path
        # goes through prompt_override() as a no-op rather than a second branch.
        overrides = (
            {draft_store.override_key(draft.use_case, draft.version, draft.file_name): draft.text()}
            if draft else {}
        )
        try:
            if model not in clients:
                raise ValueError(f"Model '{model}' is not configured (missing API key)")

            with prompt_override(overrides):
                if use_case == "record_evaluator":
                    prompt = render_record_evaluator_prompt(version, **record_vars)
                    parsed, meta = evaluate_record(prompt, model, clients)
                    st.session_state["side_results"][label] = {
                        "kind": "record", "parsed": parsed, "meta": meta,
                        "version": version_label, "model": model}

                elif use_case == "attempt_comparator":
                    # Each side runs its OWN independent title-generation attempt,
                    # exactly like production's compare() would for two separate
                    # real requests -- not shared between sides A and B, even
                    # though the achievements being compared are the same, since
                    # the two sides are independent pipelines end to end (see
                    # ARCHITECTURE.md's diagram). Always gemini_flash, regardless
                    # of which model this side is testing (see PHASE2_PLAN.md 1.5).
                    titles = generate_achievement_titles(comparator_vars["achievements"], clients)
                    achievements_text = build_achievements_text(
                        comparator_vars["attempts_data"], comparator_vars["achievements"], titles
                    )
                    prompt = render_attempt_comparator_prompt(
                        comparator_vars["rubric_description"],
                        comparator_vars["rubric_type_context"],
                        achievements_text,
                        has_titles=bool(titles),
                    )
                    parsed, record_id, meta = evaluate_comparison(prompt, model, clients)
                    st.session_state["side_results"][label] = {
                        "kind": "comparison", "parsed": parsed, "record_id": record_id, "titles": titles,
                        "meta": meta, "version": version_label, "model": model,
                    }

                elif use_case == "pillar_summarizer":
                    # No prompt is rendered here: this use case's 3 attempts each
                    # send a DIFFERENT prompt, chosen from the previous attempt's
                    # word count, so the engine renders them inside the loop --
                    # which is also why a draft has to be applied via the override
                    # rather than by passing rendered text in.
                    summary_result = summarize_pillar_with_retry(
                        version=version,
                        language=summarizer_vars["language"],
                        target_words=summarizer_vars["target_words"],
                        data_text=summarizer_vars["data_text"],
                        num_rows=len(summarizer_vars["rows"]),
                        model_key=model,
                        clients=clients,
                    )
                    st.session_state["side_results"][label] = {
                        "kind": "summary", "parsed": summary_result,
                        "meta": _summary_call_meta(summary_result, model),
                        "version": version_label, "model": model,
                    }

                elif prompt_part == "pillar_eval":
                    prompt = render_report_evaluator_pillar_prompt(
                        version,
                        pillar_choice["language"],
                        pillar_choice["pillar_name"],
                        pillar_choice["total_weight"],
                        pillar_choice["summary_text"],
                        pillar_choice["subdimensions_text"],
                    )
                    parsed, meta = evaluate_report_pillar(prompt, model, clients, version)
                    st.session_state["side_results"][label] = {
                        "kind": "report_pillar", "parsed": parsed, "meta": meta,
                        "version": version_label, "model": model}

                else:  # executive_summary
                    prompt = render_report_evaluator_executive_summary_prompt(
                        version, exec_vars["language"], exec_vars["summary_text"], exec_vars["criteria_list"])
                    text, meta = generate_executive_summary(prompt, model, clients, version)
                    st.session_state["side_results"][label] = {
                        "kind": "executive_summary", "parsed": text, "meta": meta,
                        "version": version_label, "model": model}

            st.session_state["side_results"][label]["draft_id"] = draft.draft_id if draft else None

        except (AllRetriesExhaustedError, ModelTruncatedError, PillarSummarizerClaudeUnsupportedError,
                JSONParseError, JSONValidationError, ValueError) as e:
            st.session_state["side_results"][label] = {
                "kind": "error", "error": str(e), "version": version_label, "model": model,
                "draft_id": draft.draft_id if draft else None}
        except Exception as e:  # noqa: BLE001 -- surface any unexpected error per-side rather than crashing the page
            st.session_state["side_results"][label] = {
                "kind": "error", "error": f"Unexpected error: {e}", "version": version_label, "model": model,
                "draft_id": draft.draft_id if draft else None}

    # ---------- Auto-judge (automatic, inline -- runs right after both sides complete) ----------
    st.session_state["judge_verdict"] = None
    st.session_state["judge_error"] = None
    st.session_state["judge_model_used"] = None
    st.session_state["judge_outputs"] = None

    both_ok = all(r["kind"] != "error" for r in st.session_state["side_results"].values())
    if both_ok and _judge_supported(use_case, prompt_part):
        try:
            context = _build_judge_context(
                use_case, prompt_part,
                record_vars if use_case == "record_evaluator" else None,
                pillar_choice,
                comparator_vars if use_case == "attempt_comparator" else None,
                summarizer_vars if use_case == "pillar_summarizer" else None,
            )
            result_a = st.session_state["side_results"]["A"]
            result_b = st.session_state["side_results"]["B"]
            output_a = result_a["parsed"].model_dump()
            output_b = result_b["parsed"].model_dump()
            if use_case == "attempt_comparator":
                # record_id isn't a ComparisonResponse field (see models.py) --
                # merge it back in so the judge can actually see which
                # achievement each side picked, matching record_id_validity
                # in judge_criteria.yaml.
                output_a = {**output_a, "record_id": result_a["record_id"]}
                output_b = {**output_b, "record_id": result_b["record_id"]}
            elif use_case == "pillar_summarizer":
                # A full PillarSummaryResult carries every attempt's prompt and
                # raw text -- far more than the judge needs, and enough to blow
                # the context window on a 3-attempt run. Trim to the final text
                # plus the convergence facts the rubric actually scores
                # (word_count_discipline reads the trajectory; json_key_compliance
                # reads remapped_from).
                output_a = _summary_judge_payload(result_a["parsed"])
                output_b = _summary_judge_payload(result_b["parsed"])
            verdict, judge_model = run_judge(use_case, context, output_a, output_b, clients)
            st.session_state["judge_verdict"] = verdict
            st.session_state["judge_model_used"] = judge_model
            # Kept alongside the verdict (not just a local variable) because
            # render_judge_verdict() needs them to verify quote_a/quote_b on
            # every rerun of this page, not just the run that computed them.
            st.session_state["judge_outputs"] = {"A": output_a, "B": output_b}

            # Attach the verdict to any draft that was on either side, so the
            # Prompts page can require evidence before approval and the Handoff
            # page can show a developer why the change was accepted. Recorded
            # from the draft's own perspective -- "did MY side win" -- rather
            # than as raw A/B, which is meaningless once you leave this page.
            for side_label, other_label in (("A", "B"), ("B", "A")):
                entry = st.session_state["side_results"][side_label]
                if not entry.get("draft_id"):
                    continue
                won = verdict.winner == side_label
                store_draft = draft_store.load(entry["draft_id"])
                draft_store.record_test(store_draft, {
                    "judge_winner_label": "This version won" if won else (
                        "Tie" if verdict.winner == "tie" else "Current version won"),
                    "score_draft": round(getattr(verdict, f"overall_score_{side_label.lower()}")),
                    "score_baseline": round(getattr(verdict, f"overall_score_{other_label.lower()}")),
                    "model": entry["model"],
                    "compared_against": st.session_state["side_results"][other_label]["version"],
                    "judge_model": judge_model,
                    "recommendation": verdict.recommendation,
                })
        except NoJudgeModelAvailable as e:
            st.session_state["judge_error"] = str(e)
        except Exception as e:  # noqa: BLE001 -- judge failure shouldn't hide the two side results that did succeed
            st.session_state["judge_error"] = f"Auto-judge failed: {e}"

# ---------- Results ----------

if st.session_state.get("side_results"):
    st.divider()
    result_col_a, result_col_b = st.columns(2)
    for label, col in (("A", result_col_a), ("B", result_col_b)):
        result = st.session_state["side_results"].get(label)
        with col:
            st.subheader(f"Side {label} — {result['version']}")
            if result["kind"] == "error":
                st.error(result["error"], icon=":material/error:")
                continue

            if result["kind"] == "record":
                render_record_result(result["parsed"])
            elif result["kind"] == "report_pillar":
                render_report_pillar_result(result["parsed"])
            elif result["kind"] == "comparison":
                render_titles_status(result["titles"])
                render_comparison_result(result["parsed"], result["record_id"])
            elif result["kind"] == "summary":
                render_summary_result(result["parsed"])
                with st.expander("Attempt trajectory", expanded=True):
                    render_attempt_trajectory(result["parsed"])
            else:
                render_executive_summary_result(result["parsed"])

            render_call_meta(result["meta"].attempts, result["meta"].latency_ms, result["meta"].model_key)

            with st.expander("Raw output"):
                if result["kind"] in ("record", "report_pillar", "comparison"):
                    st.json(result["parsed"].model_dump())
                else:
                    st.text(result["parsed"])
                st.caption("Raw text returned by the model, before parsing:")
                st.code(result["meta"].raw_text, language="json" if result["kind"] != "executive_summary" else "text")

    both_succeeded = all(r["kind"] != "error" for r in st.session_state["side_results"].values())

    if both_succeeded and _judge_supported(st.session_state["last_use_case"], st.session_state["last_prompt_part"]):
        st.divider()
        if st.session_state.get("judge_verdict"):
            render_judge_verdict(
                st.session_state["judge_verdict"], st.session_state["judge_model_used"],
                st.session_state["judge_outputs"]["A"], st.session_state["judge_outputs"]["B"],
            )
        elif st.session_state.get("judge_error"):
            st.warning(st.session_state["judge_error"], icon=":material/warning:")

    if both_succeeded and not st.session_state.get("winner_logged"):
        st.divider()
        st.markdown("**Your call (optional)** — record your own pick alongside the auto-judge verdict:")

        def _log_winner(winner: str) -> None:
            # Read from session_state (not the `sides` local closed over at render
            # time) per Streamlit's own guidance: a widget's value in a callback
            # should come from st.session_state, since that reflects state at
            # callback-execution time rather than a snapshot from module scope.
            results = st.session_state["side_results"]
            last_pillar = st.session_state["last_pillar"]
            verdict = st.session_state.get("judge_verdict")
            append_run({
                "use_case": st.session_state["last_use_case"],
                "prompt_part": st.session_state["last_prompt_part"],
                "pillar_name": last_pillar["pillar_name"] if last_pillar else None,
                "side_a": {
                    "version": results["A"]["version"],
                    "model": results["A"]["model"],
                    "output": _serialize(results["A"]),
                },
                "side_b": {
                    "version": results["B"]["version"],
                    "model": results["B"]["model"],
                    "output": _serialize(results["B"]),
                },
                "manual_winner": winner,
                "judge_model": st.session_state.get("judge_model_used"),
                "judge_verdict": verdict.model_dump() if verdict else None,
            })
            st.session_state["winner_logged"] = True

        with st.container(horizontal=True):
            st.button("A is better", icon=":material/thumb_up:", on_click=_log_winner, args=("A",))
            st.button("Tie", icon=":material/balance:", on_click=_log_winner, args=("tie",))
            st.button("B is better", icon=":material/thumb_up:", on_click=_log_winner, args=("B",))

    if st.session_state.get("winner_logged"):
        st.success("Recorded to results/runs.jsonl", icon=":material/check_circle:")
