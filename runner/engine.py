"""
Candidate-call engine: sends a rendered prompt to a real provider using the
EXACT parameters production uses for that (use_case, version, provider)
combination -- see schemas/SCHEMAS.md. This is deliberately NOT built on a
generic wrapper (LangChain etc.) for the candidate-call path: JSON-mode
mechanism, temperature, and token limits are decoding-time behavior, and a
generic abstraction risks silently changing what the model actually
generates, which would undermine the entire point of comparing prompts
faithfully. LangChain is reserved for the Auto-Judge call only (step 4),
which has no production equivalent it needs to match.

Deliberate scope decision: production's cross-provider auto-fallback
(v1 record-evaluator's gemini_pro -> gemini_flash -> claude_sonnet priority;
v2's core42_gpt-5.1 -> core42_fallback) is NOT replicated here. Silently
substituting a different model on failure would confound "which prompt is
better" with "which model happened to answer" -- not something a prompt
comparison tool should hide. What IS replicated: retrying the SAME model on
a JSON-parse/validation failure (up to MAX_RETRIES), since that's a real
per-(prompt, model) reliability signal worth surfacing. If retries exhaust,
the failure is returned as a first-class result, not papered over.
"""

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Type

from dotenv import load_dotenv
from pydantic import BaseModel

from .json_utils import (
    JSONParseError,
    JSONValidationError,
    validate_and_parse_comparison_json,
    validate_and_parse_json,
    validate_and_parse_summary_json,
)
from .models import (
    ComparisonResponse,
    CriterionEvaluationResponse,
    PillarSummaryResult,
    RubricEvaluationResponse,
    SummaryAttempt,
    SummaryResponse,
)

load_dotenv()

MAX_RETRIES = 3

# Core42 deployments differ in which token-limit kwarg they accept -- verified
# against azure-functions-unified/*/core/evaluator.py's CORE42_TOKEN_PARAM.
CORE42_TOKEN_PARAM = {
    "core42_gpt-5.1": "max_completion_tokens",
    "core42_gpt-4.1": "max_tokens",
    "core42_fallback": "max_tokens",
}


class AllRetriesExhaustedError(Exception):
    """The model produced unparseable/invalid output on every retry attempt."""


class ModelTruncatedError(Exception):
    """Core42 returned finish_reason == 'length' -- production treats this as a failure, not a valid (truncated) answer."""


@dataclass
class CallResult:
    raw_text: str
    attempts: int
    latency_ms: float
    model_key: str


# ============================================================
# Client setup
# ============================================================

def setup_clients() -> Dict[str, Dict[str, Any]]:
    """
    Builds whichever clients have API keys configured. Env var names match
    production exactly (see schemas/SCHEMAS.md) so a .env copied from the
    real deployment's settings works here unchanged.
    """
    clients: Dict[str, Dict[str, Any]] = {}

    google_key = os.environ.get("GOOGLE_API_KEY")
    if google_key:
        from google import genai
        gemini_client = genai.Client(api_key=google_key)
        clients["gemini_flash"] = {"client": gemini_client, "model": "gemini-2.5-flash"}
        clients["gemini_pro"] = {"client": gemini_client, "model": "gemini-2.5-pro"}

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        from anthropic import Anthropic
        clients["claude_sonnet"] = {"client": Anthropic(api_key=anthropic_key), "model": "claude-sonnet-4-20250514"}

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        clients["gpt4o"] = {"client": None, "model": "gpt-4o", "_openai_key": openai_key}

    core42_key = os.environ.get("core42_api_key")
    core42_base_url = os.environ.get("core42_base_url")
    if core42_key and core42_base_url:
        from openai import OpenAI
        core42_client = OpenAI(
            base_url=core42_base_url,
            api_key=core42_key,
            default_headers={"api-key": core42_key},
        )
        if os.environ.get("core42_gpt51_model"):
            clients["core42_gpt-5.1"] = {"client": core42_client, "model": os.environ["core42_gpt51_model"]}
        if os.environ.get("core42_gpt41_model"):
            clients["core42_gpt-4.1"] = {"client": core42_client, "model": os.environ["core42_gpt41_model"]}
        if os.environ.get("core42_fallback_model"):
            clients["core42_fallback"] = {"client": core42_client, "model": os.environ["core42_fallback_model"]}

    return clients


# ============================================================
# Provider calls -- one function per provider, params always passed
# explicitly by the use-case dispatcher below (never defaulted here),
# so record-evaluator and report-evaluator can never accidentally share
# a parameter profile they don't actually share in production.
# ============================================================

def _call_gemini(
    prompt: str,
    client_info: Dict[str, Any],
    *,
    temperature: Optional[float],
    max_output_tokens: Optional[int],
    response_schema: Optional[dict],
) -> str:
    from google.genai import types

    config_kwargs: Dict[str, Any] = {"response_mime_type": "application/json"}
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema

    # Record Evaluator's real call passes a plain dict as `config`; Report
    # Evaluator's passes a `types.GenerateContentConfig`. Both accept the
    # same kwargs, so build whichever shape matches: dict is simplest and
    # google-genai accepts either, so use dict uniformly here.
    response = client_info["client"].models.generate_content(
        model=client_info["model"],
        contents=prompt,
        config=config_kwargs,
    )
    return response.text


def _call_claude(
    prompt: str, client_info: Dict[str, Any], *, max_tokens: int, temperature: float, system: Optional[str] = None
) -> str:
    kwargs: Dict[str, Any] = {
        "model": client_info["model"],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system is not None:
        kwargs["system"] = system
    response = client_info["client"].messages.create(**kwargs)
    return response.content[0].text


def _call_gpt4o(prompt: str, client_info: Dict[str, Any], *, max_tokens: int, temperature: float) -> str:
    """Legacy openai==0.28.0-style call, matching v1 report-evaluator's actual (dated) SDK usage."""
    import openai
    openai.api_key = client_info["_openai_key"]
    response = openai.ChatCompletion.create(
        model=client_info["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content


def _call_core42(
    prompt: str,
    client_info: Dict[str, Any],
    model_key: str,
    *,
    temperature: float,
    token_limit: int,
    structured: bool,
    system_message: Optional[str],
) -> str:
    messages = [{"role": "user", "content": prompt}]
    if system_message is not None:
        messages = [{"role": "system", "content": system_message}, {"role": "user", "content": prompt}]

    token_param = CORE42_TOKEN_PARAM.get(model_key, "max_tokens")
    kwargs: Dict[str, Any] = {
        "model": client_info["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    kwargs[token_param] = token_limit
    if structured:
        kwargs["response_format"] = {"type": "json_object"}

    response = client_info["client"].chat.completions.create(**kwargs)
    raw = response.choices[0].message.content or ""
    is_truncated = getattr(response.choices[0], "finish_reason", None) == "length"
    if is_truncated:
        raise ModelTruncatedError(f"{model_key} response truncated (finish_reason=length)")
    if not raw.strip():
        raise JSONParseError(f"{model_key} returned empty response")
    return raw


# ============================================================
# Use-case dispatchers -- exact param profile per (use_case, version, provider),
# taken directly from schemas/SCHEMAS.md.
# ============================================================

def _call_record_evaluator(prompt: str, model_key: str, clients: Dict[str, Any]) -> str:
    if model_key not in clients:
        raise ValueError(f"Model '{model_key}' is not configured (no API key)")
    client_info = clients[model_key]

    if model_key in ("gemini_flash", "gemini_pro"):
        # v1 and v2 identical: no explicit temperature, no explicit token limit.
        return _call_gemini(prompt, client_info, temperature=None, max_output_tokens=None, response_schema=None)
    if model_key == "claude_sonnet":
        # Production only ever paired this with v1 (v2 dropped Claude), but
        # the call parameters don't depend on version -- Compare's
        # VALID_MODELS deliberately allows testing v2's prompt text here too.
        return _call_claude(prompt, client_info, max_tokens=3000, temperature=0.7)
    if model_key.startswith("core42_"):
        # Production only ever paired this with v2 (v1 predates Core42), but
        # same story -- record_evaluator's prompt text is identical between
        # versions anyway (see schemas/SCHEMAS.md), so this is a no-op distinction.
        return _call_core42(
            prompt, client_info, model_key,
            temperature=0.3, token_limit=16384, structured=True, system_message=None,
        )
    raise ValueError(f"Unknown model_key for record_evaluator: {model_key}")


def _call_report_evaluator(
    prompt: str,
    model_key: str,
    clients: Dict[str, Any],
    *,
    structured: bool,
    version: str,
) -> str:
    if model_key not in clients:
        raise ValueError(f"Model '{model_key}' is not configured (no API key)")
    client_info = clients[model_key]

    if model_key in ("gemini_flash", "gemini_pro"):
        # v1 and v2 identical: temperature=0.3, max_output_tokens=16384,
        # response_schema attached only for structured (pillar-eval) calls,
        # not for the executive summary.
        schema = CriterionEvaluationResponse.get_genai_schema() if structured else None
        return _call_gemini(prompt, client_info, temperature=0.3, max_output_tokens=16384, response_schema=schema)
    if model_key == "claude_sonnet":
        # Production only ever paired this with v1 (v2 dropped Claude/GPT-4o).
        # No JSON mode regardless of `structured` or which version's prompt
        # text is passed in -- production never gives Claude a response_schema
        # for this use case, and there's no reason to invent one just because
        # Compare now also allows sending v2's prompt text through this path.
        return _call_claude(prompt, client_info, max_tokens=4096, temperature=0.3)
    if model_key == "gpt4o":
        # Same story as Claude above: same caveat, no response_format available.
        return _call_gpt4o(prompt, client_info, max_tokens=4096, temperature=0.3)
    if model_key.startswith("core42_"):
        # Production only ever paired this with v2 (v1 predates Core42).
        # System message only attached for structured calls, matching
        # production's use_structured branch exactly -- and per
        # load_report_evaluator_core42_system_message's own docstring,
        # `version="v1"` correctly returns None here, since v1 never got a
        # JSON-format system message for ANY non-Gemini provider in
        # production. That omission is preserved even for this new
        # (never-shipped) v1+Core42 combination, not papered over.
        from .prompt_loader import load_report_evaluator_core42_system_message
        system_message = load_report_evaluator_core42_system_message(version) if structured else None
        return _call_core42(
            prompt, client_info, model_key,
            temperature=0.3, token_limit=16384, structured=structured, system_message=system_message,
        )
    raise ValueError(f"Unknown model_key for report_evaluator: {model_key}")


# ============================================================
# Retry orchestration + validation
# ============================================================

def _retry_same_model(call_fn, max_retries: int = MAX_RETRIES) -> Tuple[str, int, float]:
    """Calls call_fn() up to max_retries times, stopping at the first attempt
    whose raw text is non-empty (parse/shape validation happens by the caller,
    per use case, since only report-evaluator's executive summary is exempt
    from JSON validation)."""
    start = time.monotonic()
    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            raw = call_fn()
            return raw, attempt, (time.monotonic() - start) * 1000
        except Exception as e:  # noqa: BLE001 -- deliberately broad, mirrors production's catch-all retry
            last_error = e
            continue
    raise AllRetriesExhaustedError(
        f"Exhausted {max_retries} attempts on the same model. Last error: {last_error}"
    )


def evaluate_record(prompt: str, model_key: str, clients: Dict[str, Any], max_retries: int = MAX_RETRIES) -> Tuple[RubricEvaluationResponse, CallResult]:
    """Record Evaluator: call + validate against RubricEvaluationResponse, retrying the same model on parse/shape failure."""

    def attempt():
        raw = _call_record_evaluator(prompt, model_key, clients)
        validate_and_parse_json(raw, RubricEvaluationResponse)  # raises on failure -- feeds the retry loop
        return raw

    raw, attempts, latency_ms = _retry_same_model(attempt, max_retries)
    parsed = validate_and_parse_json(raw, RubricEvaluationResponse)
    return parsed, CallResult(raw_text=raw, attempts=attempts, latency_ms=latency_ms, model_key=model_key)


def evaluate_report_pillar(
    prompt: str,
    model_key: str,
    clients: Dict[str, Any],
    version: str,
    max_retries: int = MAX_RETRIES,
) -> Tuple[CriterionEvaluationResponse, CallResult]:
    """Report Evaluator pillar evaluation: structured JSON call + validation, retrying on failure."""

    def attempt():
        raw = _call_report_evaluator(prompt, model_key, clients, structured=True, version=version)
        validate_and_parse_json(raw, CriterionEvaluationResponse)
        return raw

    raw, attempts, latency_ms = _retry_same_model(attempt, max_retries)
    parsed = validate_and_parse_json(raw, CriterionEvaluationResponse)
    return parsed, CallResult(raw_text=raw, attempts=attempts, latency_ms=latency_ms, model_key=model_key)


def generate_executive_summary(
    prompt: str,
    model_key: str,
    clients: Dict[str, Any],
    version: str,
) -> Tuple[str, CallResult]:
    """Executive summary: plain text response, no JSON validation (production doesn't validate this one either)."""
    start = time.monotonic()
    raw = _call_report_evaluator(prompt, model_key, clients, structured=False, version=version)
    latency_ms = (time.monotonic() - start) * 1000
    cleaned = raw.replace("**", "").replace("*", "").replace("__", "").replace("_", "").strip()
    return cleaned, CallResult(raw_text=raw, attempts=1, latency_ms=latency_ms, model_key=model_key)


# ============================================================
# Attempt Comparator
# ============================================================
# Prompt text is byte-identical between v1 and v2 for this use case (see
# schemas/SCHEMAS.md provenance note) -- unlike Report Evaluator, there is
# no `version` parameter anywhere in this section. Only the provider
# roster differs by version in production (Claude -> Core42), and per §0's
# no-cross-model-fallback principle, Compare lets the user pick any
# configured model against either version's prompt anyway.

def _parse_titles_response(raw_text: str) -> Dict[int, Dict[str, str]]:
    """
    Best-effort port of the real title-parsing logic: strip a ```json/```
    markdown fence if present, slice from the first "[" to the last "]" (in
    case the model wrapped the array in prose), then tolerate the array
    being nested one level inside a dict (e.g. {"titles": [...]}) by taking
    the first list-valued entry. This function's caller wraps it in a
    swallow-any-exception try/except, exactly like production's own
    _generate_achievement_titles -- so being merely "close enough" here
    carries no real risk: any parse failure just means titles silently
    degrade to the "Input N" fallback, same as it would in production.
    """
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    data = json.loads(text)
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])

    titles: Dict[int, Dict[str, str]] = {}
    for item in data:
        titles[item["index"]] = {"ar": item["title_ar"], "en": item["title_en"]}
    return titles


def generate_achievement_titles(achievements: List[Dict[str, Any]], clients: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
    """
    Always uses gemini_flash, regardless of which model the comparison
    itself will run on -- production hardcodes this, so even a
    Core42-only Compare run needs GOOGLE_API_KEY configured to get real
    titles (see PHASE2_PLAN.md 1.5/1.6). No retry: a single attempt,
    wrapped in one swallow-any-exception try/except, exactly like
    production's _generate_achievement_titles -- on any failure this
    returns {} and the main comparison proceeds with the "Input N"
    fallback phrasing, never raising.
    """
    if "gemini_flash" not in clients or not achievements:
        return {}

    try:
        from .input_adapters import build_title_generator_input
        from .prompt_loader import render_attempt_comparator_title_generator_prompt

        prompt = render_attempt_comparator_title_generator_prompt(build_title_generator_input(achievements))
        token_budget = max(800, len(achievements) * 100)

        client_info = clients["gemini_flash"]
        response = client_info["client"].models.generate_content(
            model=client_info["model"],
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0.3, "max_output_tokens": token_budget},
        )
        return _parse_titles_response(response.text)
    except Exception:  # noqa: BLE001 -- deliberately broad, mirrors production's swallow-everything behavior exactly
        return {}


def _call_attempt_comparator(prompt: str, model_key: str, clients: Dict[str, Any]) -> str:
    if model_key not in clients:
        raise ValueError(f"Model '{model_key}' is not configured (no API key)")
    client_info = clients[model_key]

    if model_key in ("gemini_flash", "gemini_pro"):
        # Same as the title-gen call's provider family, but the main
        # comparison call itself sets no explicit temperature (provider
        # default), matching production exactly.
        return _call_gemini(prompt, client_info, temperature=None, max_output_tokens=None, response_schema=None)
    if model_key == "claude_sonnet":
        # Production only ever paired this with v1 (v2 dropped Claude) --
        # Compare allows it against either version's prompt text per §0,
        # since the text is identical between versions anyway here.
        from .prompt_loader import load_attempt_comparator_system_message
        return _call_claude(
            prompt, client_info, max_tokens=4000, temperature=0.7, system=load_attempt_comparator_system_message()
        )
    if model_key.startswith("core42_"):
        # Production only ever paired this with v2 (v1 predates Core42).
        from .prompt_loader import load_attempt_comparator_system_message
        return _call_core42(
            prompt, client_info, model_key,
            temperature=0.3, token_limit=16384, structured=True,
            system_message=load_attempt_comparator_system_message(),
        )
    raise ValueError(f"Unknown model_key for attempt_comparator: {model_key}")


def evaluate_comparison(
    prompt: str, model_key: str, clients: Dict[str, Any], max_retries: int = MAX_RETRIES
) -> Tuple[ComparisonResponse, Any, CallResult]:
    """Attempt Comparator: call + validate + extract record_id, retrying the same model on parse/shape failure."""

    def attempt():
        raw = _call_attempt_comparator(prompt, model_key, clients)
        validate_and_parse_comparison_json(raw)  # raises on failure -- feeds the retry loop
        return raw

    raw, attempts, latency_ms = _retry_same_model(attempt, max_retries)
    parsed, record_id = validate_and_parse_comparison_json(raw)
    return parsed, record_id, CallResult(raw_text=raw, attempts=attempts, latency_ms=latency_ms, model_key=model_key)


# ============================================================
# Pillar Summarizer
# ============================================================
# The only use case whose retry loop is driven by the CONTENT of the previous
# response rather than by whether it parsed. Each of the (up to) 3 attempts
# sends a DIFFERENT prompt, chosen from how far the best-so-far word count
# missed the target -- so unlike every other use case here, the prompts cannot
# be rendered by the caller up front and handed in. This function renders them.
#
# It also does NOT use _retry_same_model(): that helper retries an identical
# call until one parses. Production's loop here is different in a way that
# matters -- a JSON failure CONSUMES one of the three word-count attempts
# rather than being retried, and on attempt 1 it aborts the whole pillar
# outright. Replicated exactly below.


class PillarSummarizerClaudeUnsupportedError(Exception):
    """v1's Claude path for this use case cannot run -- see _call_pillar_summarizer."""


def _call_pillar_summarizer(prompt: str, model_key: str, clients: Dict[str, Any]) -> str:
    if model_key not in clients:
        raise ValueError(f"Model '{model_key}' is not configured (no API key)")
    client_info = clients[model_key]

    if model_key in ("gemini_flash", "gemini_pro"):
        # v1 and v2 identical: temperature 0.3 and native structured output via
        # response_schema. No max_output_tokens -- unlike report_evaluator's
        # Gemini path, which does cap at 16384.
        return _call_gemini(
            prompt, client_info,
            temperature=0.3, max_output_tokens=None, response_schema=SummaryResponse.get_genai_schema(),
        )
    if model_key == "claude_sonnet":
        # v1 only, and BROKEN in production: _generate_with_claude calls
        # messages.create(model=..., temperature=0.3, messages=[...]) with no
        # max_tokens, which the Anthropic SDK requires -- so every Claude call
        # on this use case raises TypeError before reaching the network. That
        # error is not one of the three re-raised types, so production's loop
        # swallows it on attempts 2-3 and aborts on attempt 1: selecting Claude
        # for Pillar Summarizer always fails, in production, today.
        #
        # Faithfully unsupported rather than silently "fixed" by inventing a
        # max_tokens: picking a value would produce output production cannot
        # produce, which is the one thing this tool must never do. Raised as a
        # named error so Compare can say why instead of surfacing a bare TypeError.
        raise PillarSummarizerClaudeUnsupportedError(
            "v1's Pillar Summarizer Claude path omits the SDK-required max_tokens argument, "
            "so it raises before making a request. This reproduces production, where selecting "
            "claude_sonnet for this use case always fails. Pick a Gemini or Core42 model instead."
        )
    if model_key == "gpt4o":
        # v1 only. Production omits max_tokens here too, but OpenAI treats it as
        # optional (defaults to the model maximum), so unlike Claude this one
        # genuinely runs. Passing max_tokens=None keeps that behavior.
        import openai
        openai.api_key = client_info["_openai_key"]
        response = openai.ChatCompletion.create(
            model=client_info["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content
    if model_key.startswith("core42_"):
        # v2 only. Note structured=True but NO system message -- unlike
        # report_evaluator v2 and attempt_comparator, this use case's Core42
        # path prepends nothing. Truncation is only a warning in production
        # here (not an error as in attempt_comparator), handled by the caller.
        return _call_core42(
            prompt, client_info, model_key,
            temperature=0.3, token_limit=16384, structured=True, system_message=None,
        )
    raise ValueError(f"Unknown model_key for pillar_summarizer: {model_key}")


def count_summary_words(text: str) -> int:
    """Port of core/utils.py::count_arabic_words -- a plain whitespace split.

    Despite the production name it is not Arabic-specific and does no
    punctuation handling: "word," and "word" both count as one token, and an
    Arabic clause joined by a tatweel or a non-breaking space counts as one
    word. Every word-count decision in the retry loop rests on this, so it
    must not be "improved" into a smarter tokenizer.
    """
    return len([w for w in text.strip().split() if len(w.strip()) > 0])


def summarize_pillar_with_retry(
    version: str,
    language: str,
    target_words: int,
    data_text: str,
    num_rows: int,
    model_key: str,
    clients: Dict[str, Any],
    max_retries: int = MAX_RETRIES,
    tolerance: int = 100,
) -> PillarSummaryResult:
    """Faithful port of _generate_summary_with_retry (both versions).

    The loop, exactly as production runs it:
      * attempt 1 -> the overall prompt
      * attempt == max_retries -> the final-retry (mechanical edit) prompt
      * anything between -> the adaptive retry prompt
    Attempts 2 and 3 are built from the BEST attempt so far, not the previous
    one -- these differ whenever attempt 2 lands further from target than
    attempt 1 did.

    Early exit the moment a word count lands in [target-tolerance, target].
    If the loop exhausts, prefer the closest attempt that is NOT over the
    maximum, even when an over-length attempt is numerically closer; fall back
    to the closest overall only when every attempt overshot.

    Temperature stays 0.3 on all three stages, including the final "mechanical
    edit" pass whose own prompt describes it as a constrained surgical edit.
    That is production's, and is deliberately not tuned here.
    """
    from .prompt_loader import (
        render_pillar_summarizer_final_retry_prompt,
        render_pillar_summarizer_overall_prompt,
        render_pillar_summarizer_retry_prompt,
    )

    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown version: {version}")

    min_acceptable = target_words - tolerance
    max_acceptable = target_words

    best_summary = ""
    best_word_count = 0
    best_distance = float("inf")
    best_language = "ar"
    best_attempt_no = 0

    trajectory: List[SummaryAttempt] = []
    swallowed: List[str] = []

    def finish(summary, word_count, language_out, attempts, outcome, returned_attempt):
        return PillarSummaryResult(
            summary=summary,
            word_count=word_count,
            num_rows=num_rows,
            attempts=attempts,
            detected_language=language_out,
            trajectory=trajectory,
            outcome=outcome,
            returned_attempt=returned_attempt,
            target_words=target_words,
            min_acceptable=min_acceptable,
            max_acceptable=max_acceptable,
            swallowed_errors=swallowed,
        )

    for attempt in range(1, max_retries + 1):
        try:
            if attempt == 1:
                prompt = render_pillar_summarizer_overall_prompt(version, language, data_text, target_words)
                stage, branch, tier = "overall", None, None
            elif attempt == max_retries:
                prompt, branch, tier = render_pillar_summarizer_final_retry_prompt(
                    version, language, target_words, best_word_count, best_summary
                )
                stage = "final_retry"
            else:
                prompt, branch = render_pillar_summarizer_retry_prompt(
                    version, language, target_words, best_word_count, best_summary
                )
                stage, tier = "retry", None

            start = time.monotonic()
            raw = _call_pillar_summarizer(prompt, model_key, clients)
            parsed, remapped_from = validate_and_parse_summary_json(raw, version)
            latency_ms = (time.monotonic() - start) * 1000

            summary = parsed.summary
            detected_language = parsed.language
            word_count = count_summary_words(summary)
            distance = abs(word_count - target_words)
            in_range = min_acceptable <= word_count <= max_acceptable

            trajectory.append(SummaryAttempt(
                attempt=attempt, prompt_stage=stage, branch=branch, tier=tier,
                word_count=word_count, distance=distance, in_range=in_range,
                language=detected_language, summary=summary, latency_ms=latency_ms,
                prompt=prompt, raw_text=raw, remapped_from=remapped_from,
            ))

            if distance < best_distance:
                best_summary, best_word_count = summary, word_count
                best_distance, best_language, best_attempt_no = distance, detected_language, attempt

            if in_range:
                return finish(summary, word_count, detected_language, attempt, "in_range", attempt)

        except Exception as e:  # noqa: BLE001 -- production's own catch-all
            # Production re-raises rate-limit/auth/timeout immediately and
            # swallows everything else, but ONLY once something has succeeded:
            # `if not best_summary: raise`. So a first-attempt failure aborts
            # the pillar entirely, while a later one just burns an attempt.
            if not best_summary:
                raise
            swallowed.append(f"attempt {attempt} ({stage}): {type(e).__name__}: {e}")

    # Loop exhausted without landing in range.
    #
    # v1 hard-codes attempts=max_retries here; v2 reports len(all_attempts),
    # i.e. only the generations that actually completed. Preserved because it
    # changes what a reviewer sees when a prompt fails to converge: on v1 a run
    # where attempt 2 errored still reports "3 attempts".
    attempts_reported = max_retries if version == "v1" else len(trajectory)

    not_too_long = [a for a in trajectory if a.word_count <= max_acceptable]
    if not_too_long:
        best_valid = min(not_too_long, key=lambda a: a.distance)
        return finish(
            best_valid.summary, best_valid.word_count, best_valid.language,
            attempts_reported, "best_effort_not_too_long", best_valid.attempt,
        )
    return finish(
        best_summary, best_word_count, best_language,
        attempts_reported, "best_effort_closest", best_attempt_no,
    )
