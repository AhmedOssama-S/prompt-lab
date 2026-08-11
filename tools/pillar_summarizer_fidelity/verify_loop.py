"""Differential test of the retry loop: prompt-lab's port vs production's.

Both are driven by the SAME scripted sequence of fake model responses, so any
divergence in early-exit, best-tracking, give-up preference, prompt-stage
selection, or attempts-reporting shows up as a mismatch. No network calls.

Each scenario is a list of per-attempt outcomes: either an int (produce a
summary with that many words) or an Exception instance (that attempt fails).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

from _source import assert_v1_branch_agnostic, materialize

assert_v1_branch_agnostic()
materialize()

from runner import engine as lab_engine  # noqa: E402 -- needs the sys.path setup above


TARGET = 575          # min_acceptable = 475, max_acceptable = 575
LANG = "ar"
DATA_TEXT = "عدد الصفوف/الإنجازات: 2\n\n--- الإنجاز/الصف 1 ---\nkey: value\n\n"

SCENARIOS = {
    # name: [attempt1, attempt2, attempt3]
    "hit_first_try":            [500],
    "hit_on_second":            [300, 520],
    "hit_on_third":             [300, 320, 500],
    "never_in_range_all_short": [100, 200, 300],
    "never_in_range_all_long":  [900, 800, 700],
    "mixed_prefers_not_long":   [580, 300, 590],   # 580 is closer, but 300 is not-too-long -> 300 wins
    "exact_min_boundary":       [475],             # inclusive lower bound
    "exact_max_boundary":       [575],             # inclusive upper bound
    "one_under_min":            [474, 474, 474],
    "one_over_max":             [576, 576, 576],
    "best_not_previous":        [480 - 100, 900, 470],  # attempt2 worse than attempt1 -> attempt3 built from attempt1
    "fail_first_aborts":        [ValueError("boom")],
    "fail_second_swallowed":    [300, ValueError("boom"), 500],
    "fail_second_and_third":    [300, ValueError("b1"), ValueError("b2")],
    "fail_all_but_first":       [900, RuntimeError("x"), RuntimeError("y")],
}


def make_summary(n_words):
    return " ".join(["كلمة"] * n_words)


# ---------------------------------------------------------------- production

def run_production(pkg, script):
    """Drive the real _generate_summary_with_retry with a stubbed model call."""
    mod = __import__(f"{pkg}.summarizer", fromlist=["summarizer"])
    cls = mod.PillarSummarizer

    inst = cls.__new__(cls)          # bypass __init__ (needs real API keys)
    inst.request_id = None
    inst.clients = {"gemini_flash": {"client": None, "model": "stub"}}
    inst._detected_languages = {}

    calls = []

    def fake_gemini(prompt, client_info):
        i = len(calls)
        calls.append(prompt)
        outcome = script[i] if i < len(script) else 0
        if isinstance(outcome, Exception):
            raise outcome
        return make_summary(outcome), "ar"

    inst._generate_with_gemini = fake_gemini

    try:
        out = inst._generate_summary_with_retry(
            all_data=[{"key": "value"}], model_key="gemini_flash",
            target_words=TARGET, pillar_name="P", language=LANG,
        )
    except Exception as e:
        return ("raised", type(e).__name__), calls

    # v1 returns 4-tuple, v2 returns 5-tuple (extra attempt_word_counts)
    summary, wc, attempts, lang = out[0], out[1], out[2], out[3]
    return ("ok", wc, attempts, lang), calls


# ---------------------------------------------------------------------- lab

def run_lab(version, script):
    calls = []

    def fake_call(prompt, model_key, clients):
        i = len(calls)
        calls.append(prompt)
        outcome = script[i] if i < len(script) else 0
        if isinstance(outcome, Exception):
            raise outcome
        import json as _json
        return _json.dumps({"summary": make_summary(outcome), "language": "ar"}, ensure_ascii=False)

    orig = lab_engine._call_pillar_summarizer
    lab_engine._call_pillar_summarizer = fake_call
    try:
        res = lab_engine.summarize_pillar_with_retry(
            version=version, language=LANG, target_words=TARGET,
            data_text=DATA_TEXT, num_rows=1,
            model_key="gemini_flash", clients={"gemini_flash": {}},
        )
    except Exception as e:
        return ("raised", type(e).__name__), calls
    finally:
        lab_engine._call_pillar_summarizer = orig

    return ("ok", res.word_count, res.attempts, res.detected_language), calls, res


def classify_stage(prompt, lang="ar"):
    """Identify which prompt template produced this text, from its own wording."""
    if "أنت كاتب تقارير حكومية رسمية" in prompt or "You are an official government report writer" in prompt \
            or "You are a formal government report writer" in prompt:
        return "overall"
    if "محاولة نهائية" in prompt or "Final attempt" in prompt:
        return "final_retry"
    if "أقصر من المطلوب" in prompt or "shorter than required" in prompt:
        return "retry_expand"
    if "أطول من المطلوب" in prompt or "longer than required" in prompt:
        return "retry_condense"
    return "?"


def main():
    failures = []
    for version, pkg in (("v1", "ps_v1"), ("v2", "ps_v2")):
        for name, script in SCENARIOS.items():
            prod_out, prod_calls = run_production(pkg, script)
            lab_result = run_lab(version, script)
            lab_out, lab_calls = lab_result[0], lab_result[1]

            prod_stages = [classify_stage(p) for p in prod_calls]
            lab_stages = [classify_stage(p) for p in lab_calls]

            ok = (prod_out == lab_out) and (prod_stages == lab_stages)
            if not ok:
                failures.append((version, name, prod_out, lab_out, prod_stages, lab_stages))
            status = "OK " if ok else "FAIL"
            print(f"[{status}] {version}/{name:26s} out={lab_out!s:28s} stages={'>'.join(lab_stages)}")
            if not ok:
                print(f"        prod out={prod_out!s:28s} stages={'>'.join(prod_stages)}")

    print()
    if failures:
        print(f"{len(failures)} MISMATCH(ES)")
        sys.exit(1)
    print(f"All {len(SCENARIOS) * 2} loop scenarios matched production "
          f"(final word count, attempts reported, language, and prompt-stage sequence)")


if __name__ == "__main__":
    main()
