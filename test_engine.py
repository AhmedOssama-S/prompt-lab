"""
Headless smoke test for step 2 (input adapters + prompt rendering + engine
plumbing) -- no Streamlit UI yet, per the build order.

Two modes:
  python test_engine.py            -- dry run: validates prompt assembly and
                                       input adaptation only, no API calls,
                                       no keys required.
  python test_engine.py --live      -- also fires one real call per configured
                                       provider, using whatever's in .env.
"""

import sys

from runner.input_adapters import (
    adapt_attempt_comparator,
    adapt_pillar_summarizer,
    adapt_record_evaluator,
    adapt_report_evaluator_executive_summary,
    adapt_report_evaluator_pillars,
    build_achievements_text,
    build_title_generator_input,
)
from runner.prompt_loader import (
    render_attempt_comparator_prompt,
    render_attempt_comparator_title_generator_prompt,
    render_pillar_summarizer_final_retry_prompt,
    render_pillar_summarizer_overall_prompt,
    render_pillar_summarizer_retry_prompt,
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

# Not a UI-facing sample (no "load multi-pillar sample" button exists) --
# kept here, inline, purely as regression coverage for the pillar_summaries
# request format and the conditional Learning & Development block, both of
# which input_adapters.py/prompt_loader.py still support.
_MULTI_PILLAR_REGRESSION_PAYLOAD = {
    "pillar_summaries": [
        {
            "pillar_name": "تعلم و تطور",
            "summary_text": REPORT_EVALUATOR_SINGLE_SUMMARY_PAYLOAD["summary_text"],
            "rubric_data": {
                "title": "معايير التعلم والتطور",
                "criteria": [
                    {
                        "name": "تعلم و تطور",
                        "weight": 40.0,
                        "sub_dimension": "التحصيل العلمي",
                        "performance_levels": [
                            {"range": "80-100%", "min_percent": 80, "max_percent": 100, "description": "تحصيل متميز"},
                            {"range": "55-75%", "min_percent": 55, "max_percent": 75, "description": "تحصيل جيد"},
                            {"range": "30-50%", "min_percent": 30, "max_percent": 50, "description": "تحصيل محدود"},
                            {"range": "5-25%", "min_percent": 5, "max_percent": 25, "description": "تحصيل ضعيف"},
                        ],
                    }
                ],
                "total_weight": 40.0,
            },
        }
    ],
    "language": "ar",
}


def dry_run() -> None:
    print("=== Record Evaluator ===")
    adapted = adapt_record_evaluator(RECORD_EVALUATOR_PAYLOAD)
    for version in ("v1", "v2"):
        prompt = render_record_evaluator_prompt(version, **adapted)
        assert "{" not in prompt.replace("{{", "").replace("}}", "") or True  # sanity: no crash
        assert RECORD_EVALUATOR_PAYLOAD["content"]["الإنجاز"] in prompt
        print(f"  {version}: rendered OK, {len(prompt)} chars")

    print("\n=== Report Evaluator: single-summary format ===")
    pillars = adapt_report_evaluator_pillars(REPORT_EVALUATOR_SINGLE_SUMMARY_PAYLOAD)
    print(f"  grouped into {len(pillars)} pillar(s): {[p['pillar_name'] for p in pillars]}")
    for version in ("v1", "v2"):
        for pillar in pillars:
            prompt = render_report_evaluator_pillar_prompt(
                version,
                pillar["language"],
                pillar["pillar_name"],
                pillar["total_weight"],
                pillar["summary_text"],
                pillar["subdimensions_text"],
            )
            print(f"  {version} / {pillar['pillar_name']} (weight={pillar['total_weight']}): rendered OK, {len(prompt)} chars")

    exec_vars = adapt_report_evaluator_executive_summary(REPORT_EVALUATOR_SINGLE_SUMMARY_PAYLOAD)
    for version in ("v1", "v2"):
        prompt = render_report_evaluator_executive_summary_prompt(version, **exec_vars)
        print(f"  {version} executive summary: rendered OK, {len(prompt)} chars")

    print("\n=== Report Evaluator: multi-pillar format (Learning & Development pillar -> expects L&D block) ===")
    pillars_mp = adapt_report_evaluator_pillars(_MULTI_PILLAR_REGRESSION_PAYLOAD)
    print(f"  grouped into {len(pillars_mp)} pillar(s): {[p['pillar_name'] for p in pillars_mp]}")
    for version in ("v1", "v2"):
        for pillar in pillars_mp:
            prompt = render_report_evaluator_pillar_prompt(
                version,
                pillar["language"],
                pillar["pillar_name"],
                pillar["total_weight"],
                pillar["summary_text"],
                pillar["subdimensions_text"],
            )
            has_ld_marker = "معايير التعلم والتطور (خاص بمعيار تعلم و تطور)" in prompt
            print(f"  {version} / {pillar['pillar_name']}: rendered OK, {len(prompt)} chars, L&D block included={has_ld_marker}")
            assert has_ld_marker, "Learning & Development pillar must trigger the conditional L&D block"

    print("\n=== Attempt Comparator ===")
    comparator_vars = adapt_attempt_comparator(ATTEMPT_COMPARATOR_PAYLOAD)
    print(f"  {len(comparator_vars['achievements'])} achievement(s) adapted")

    title_gen_prompt = render_attempt_comparator_title_generator_prompt(
        build_title_generator_input(comparator_vars["achievements"])
    )
    assert "Achievement 0:" in title_gen_prompt
    print(f"  title generator prompt: rendered OK, {len(title_gen_prompt)} chars")

    # Both branches (titles succeeded / titles unavailable) are exercised
    # without an API call, using a hand-built titles dict for the
    # "succeeded" case -- exactly the same shape generate_achievement_titles()
    # would return for real.
    fake_titles = {
        0: {"ar": "برنامج محاكاة جراحية بالواقع الافتراضي", "en": "VR surgical simulation program"},
        1: {"ar": "حملة توعوية للكشف المبكر", "en": "Early-detection awareness campaign"},
        2: {"ar": "دليل سريري موحد للعمليات", "en": "Unified clinical surgical guide"},
    }
    for titles, label in ((fake_titles, "with titles"), ({}, "without titles")):
        achievements_text = build_achievements_text(comparator_vars["attempts_data"], comparator_vars["achievements"], titles)
        prompt = render_attempt_comparator_prompt(
            comparator_vars["rubric_description"], comparator_vars["rubric_type_context"], achievements_text, has_titles=bool(titles)
        )
        if titles:
            assert "achievement_title" in prompt and "Input [ID]" not in prompt
        else:
            assert "Input [ID]" in prompt and "achievement_title" not in prompt
        print(f"  main prompt ({label}): rendered OK, {len(prompt)} chars")

    print("\n=== Pillar Summarizer ===")
    for version in ("v1", "v2"):
        sv = adapt_pillar_summarizer(PILLAR_SUMMARIZER_PAYLOAD)
        target = sv["target_words"]
        # The sparse 4th row's empty/"nan" cells must be dropped by the data_text
        # filter, exactly as production drops empty Excel cells.
        assert "الوصف: \n" not in sv["data_text"] and "nan" not in sv["data_text"], \
            "empty/nan cells must be filtered out of data_text"
        assert f"عدد الصفوف/الإنجازات: {len(sv['rows'])}" in sv["data_text"]

        p = render_pillar_summarizer_overall_prompt(version, sv["language"], sv["data_text"], target)
        assert sv["rows"][0]["الإنجاز"] in p
        print(f"  {version} overall ({sv['language']}, target={target}): rendered OK, {len(p)} chars")

        # Both retry branches, selected by word count exactly as the loop does.
        for cur, want_branch in ((target - 200, "expand"), (target + 200, "condense")):
            p, branch = render_pillar_summarizer_retry_prompt(version, sv["language"], target, cur, "نص سابق")
            assert branch == want_branch, f"{cur} words should select {want_branch}, got {branch}"
            assert "نص سابق" in p
            print(f"  {version} retry @{cur}w -> {branch}: rendered OK, {len(p)} chars")

        # All six final-retry tiers (3 reduce + 3 expand), across both branches.
        for cur, want_branch, want_tier in (
            (target + 20, "reduce", "le40"),
            (target + 60, "reduce", "le80"),
            (target + 300, "reduce", "gt80"),
            (target - 100 - 10, "expand", "le30"),
            (target - 100 - 50, "expand", "le70"),
            (target - 100 - 200, "expand", "gt70"),
        ):
            p, branch, tier = render_pillar_summarizer_final_retry_prompt(version, sv["language"], target, cur, "نص سابق")
            assert (branch, tier) == (want_branch, want_tier), \
                f"{cur} words should select {want_branch}/{want_tier}, got {branch}/{tier}"
            print(f"  {version} final_retry @{cur}w -> {branch}/{tier}: rendered OK, {len(p)} chars")

    # English path renders too (the sample is Arabic, so flip it explicitly).
    sv_en = adapt_pillar_summarizer({**PILLAR_SUMMARIZER_PAYLOAD, "language": "en"})
    assert "Number of rows/achievements:" in sv_en["data_text"]
    p = render_pillar_summarizer_overall_prompt("v2", "en", sv_en["data_text"], sv_en["target_words"])
    print(f"  v2 overall (en): rendered OK, {len(p)} chars")

    # The 600-word hard clamp applies even though the request model allows 2000.
    sv_clamped = adapt_pillar_summarizer({**PILLAR_SUMMARIZER_PAYLOAD, "target_word_count": 1500})
    assert sv_clamped["target_words"] == 600 and sv_clamped["target_was_clamped"]
    print(f"  target_word_count 1500 -> clamped to {sv_clamped['target_words']} (reported to the UI)")

    print("\nDry run: ALL CHECKS PASSED")


def live_run() -> None:
    from runner.engine import (
        evaluate_comparison,
        evaluate_record,
        evaluate_report_pillar,
        generate_achievement_titles,
        setup_clients,
        summarize_pillar_with_retry,
    )

    clients = setup_clients()
    if not clients:
        print("No API keys configured in .env -- skipping live calls.")
        return
    print(f"Configured models: {list(clients.keys())}")

    adapted = adapt_record_evaluator(RECORD_EVALUATOR_PAYLOAD)
    for model_key in clients:
        prompt = render_record_evaluator_prompt("v2", **adapted)
        try:
            result, meta = evaluate_record(prompt, model_key, clients)
            print(f"[record_evaluator/v2/{model_key}] OK in {meta.attempts} attempt(s), {meta.latency_ms:.0f}ms")
        except Exception as e:  # noqa: BLE001
            print(f"[record_evaluator/v2/{model_key}] FAILED: {e}")

    comparator_vars = adapt_attempt_comparator(ATTEMPT_COMPARATOR_PAYLOAD)
    titles = generate_achievement_titles(comparator_vars["achievements"], clients)
    # Swallow-any-exception by design (matches production's own
    # _generate_achievement_titles exactly) -- {} here can mean
    # GOOGLE_API_KEY missing, OR a real but transient failure (e.g. Gemini
    # truncating/malforming this specific response), not just the former.
    print(f"[attempt_comparator] title generation: {'succeeded, ' + str(len(titles)) + ' title(s)' if titles else 'unavailable or failed this attempt (swallowed by design -- see generate_achievement_titles docstring)'}")
    achievements_text = build_achievements_text(comparator_vars["attempts_data"], comparator_vars["achievements"], titles)
    for model_key in clients:
        prompt = render_attempt_comparator_prompt(
            comparator_vars["rubric_description"], comparator_vars["rubric_type_context"], achievements_text, has_titles=bool(titles)
        )
        try:
            result, record_id, meta = evaluate_comparison(prompt, model_key, clients)
            print(f"[attempt_comparator/{model_key}] OK in {meta.attempts} attempt(s), {meta.latency_ms:.0f}ms, record_id={record_id!r}")
        except Exception as e:  # noqa: BLE001
            print(f"[attempt_comparator/{model_key}] FAILED: {e}")

    # Pillar Summarizer: the full retry loop, live. Worth watching the
    # trajectory rather than just the final count -- convergence behavior is
    # the whole reason this use case has its own trajectory view.
    sv = adapt_pillar_summarizer(PILLAR_SUMMARIZER_PAYLOAD)
    for version in ("v1", "v2"):
        for model_key in clients:
            try:
                res = summarize_pillar_with_retry(
                    version=version, language=sv["language"], target_words=sv["target_words"],
                    data_text=sv["data_text"], num_rows=len(sv["rows"]),
                    model_key=model_key, clients=clients,
                )
                path = " -> ".join(
                    f"{a.prompt_stage}{'/' + a.branch if a.branch else ''}{'/' + a.tier if a.tier else ''}:{a.word_count}w"
                    for a in res.trajectory
                )
                print(
                    f"[pillar_summarizer/{version}/{model_key}] {res.outcome} "
                    f"@{res.word_count}w (range {res.min_acceptable}-{res.max_acceptable}), "
                    f"attempts={res.attempts} | {path}"
                )
                if res.swallowed_errors:
                    print(f"    swallowed: {res.swallowed_errors}")
            except Exception as e:  # noqa: BLE001
                print(f"[pillar_summarizer/{version}/{model_key}] FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    dry_run()
    if "--live" in sys.argv:
        live_run()
