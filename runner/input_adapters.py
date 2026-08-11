"""
Turns a real Medals-AI request payload (shaped exactly like the actual
Azure Function request body -- see schemas/SCHEMAS.md) into the variables
each prompt template actually needs. This is where fidelity to production
either holds or breaks, since these are the exact transformations
`core/evaluator.py` / `core/comparator.py` perform before building their prompts.
"""

import json
from typing import Any, Dict, List, Optional, TypedDict


# ============================================================
# Record Evaluator
# ============================================================

def adapt_record_evaluator(payload: Dict[str, Any]) -> Dict[str, str]:
    """
    payload: {"content": {...}, "rubric_data": {...}, "record_id": ..., ["model": ...]}

    Production interpolates the raw dicts directly into an f-string
    (`{rubric_data}` / `{input_content}` with no !r or format spec), which
    means Python calls plain str() on them -- e.g. {'key': 'value'}, not
    pretty-printed JSON. Replicated verbatim here; do NOT "improve" this
    to json.dumps(..., indent=2) or ensure_ascii=False, since that would
    send the model different-looking text than production actually does.
    """
    if "content" not in payload or "rubric_data" not in payload:
        raise ValueError("payload must contain 'content' and 'rubric_data'")

    return {
        "input_content": str(payload["content"]),
        "rubric_data": str(payload["rubric_data"]),
    }


# ============================================================
# Report Evaluator
# ============================================================

class PillarEvalVars(TypedDict):
    pillar_name: str
    total_weight: float
    summary_text: str
    subdimensions_text: str
    language: str


def _group_criteria(criteria: List[Dict[str, Any]], fallback_pillar_name: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Mirrors the real grouping loop exactly: split criterion["name"] on
    " - ", first segment is the pillar name; if there's no " - ", fall
    back to `fallback_pillar_name` if given (multi-pillar format, where
    the pillar name is already known from the enclosing pillar_summaries
    entry), else fall back to the full criterion name (single-summary
    format, where there's no other pillar name available).
    """
    pillars: Dict[str, List[Dict[str, Any]]] = {}
    for criterion in criteria:
        name = criterion["name"]
        if " - " in name:
            pillar_name = name.split(" - ")[0].strip()
        else:
            pillar_name = fallback_pillar_name if fallback_pillar_name is not None else name

        pillars.setdefault(pillar_name, []).append(criterion)
    return pillars


def _build_subdimensions_text(sub_dimensions: List[Dict[str, Any]], language: str) -> str:
    """
    Byte-for-byte port of the loop in `_create_pillar_evaluation_prompt`
    (both azure-functions/ and azure-functions-unified/, identical in both).
    """
    text = ""
    if language == "ar":
        for i, subdim in enumerate(sub_dimensions, 1):
            label = subdim.get("sub_dimension") or subdim["name"]
            text += f"\n### البعد الفرعي {i}: {label} (الوزن: {subdim['weight']}%)\n"
            text += "\n**مستويات الأداء**:\n"
            for j, level in enumerate(subdim["performance_levels"], 1):
                text += f"{j}. **المستوى {level['range']}**: {level['description']}\n"
            text += "\n"
    else:
        for i, subdim in enumerate(sub_dimensions, 1):
            label = subdim.get("sub_dimension") or subdim["name"]
            text += f"\n### Sub-dimension {i}: {label} (Weight: {subdim['weight']}%)\n"
            text += "\n**Performance Levels**:\n"
            for j, level in enumerate(subdim["performance_levels"], 1):
                text += f"{j}. **Level {level['range']}**: {level['description']}\n"
            text += "\n"
    return text


def adapt_report_evaluator_pillars(payload: Dict[str, Any]) -> List[PillarEvalVars]:
    """
    payload: either the single-summary format
      {"summary_text": ..., "rubric_data": {"criteria": [...], ...}, "language": "ar"}
    or the multi-pillar format
      {"pillar_summaries": [{"pillar_name": ..., "summary_text": ..., "rubric_data": {...}}, ...], "language": "ar"}

    Returns one entry per grouped pillar, ready to render through
    prompt_loader.load_report_evaluator_pillar_prompt(version, language, pillar_name).
    """
    language = payload.get("language", "ar")
    results: List[PillarEvalVars] = []

    if "pillar_summaries" in payload and payload["pillar_summaries"]:
        for ps in payload["pillar_summaries"]:
            grouped = _group_criteria(ps["rubric_data"]["criteria"], fallback_pillar_name=ps["pillar_name"])
            for pillar_name, sub_dimensions in grouped.items():
                results.append(PillarEvalVars(
                    pillar_name=pillar_name,
                    total_weight=sum(sd["weight"] for sd in sub_dimensions),
                    summary_text=ps["summary_text"],
                    subdimensions_text=_build_subdimensions_text(sub_dimensions, language),
                    language=language,
                ))
        return results

    if "summary_text" in payload and "rubric_data" in payload:
        grouped = _group_criteria(payload["rubric_data"]["criteria"], fallback_pillar_name=None)
        for pillar_name, sub_dimensions in grouped.items():
            results.append(PillarEvalVars(
                pillar_name=pillar_name,
                total_weight=sum(sd["weight"] for sd in sub_dimensions),
                summary_text=payload["summary_text"],
                subdimensions_text=_build_subdimensions_text(sub_dimensions, language),
                language=language,
            ))
        return results

    raise ValueError(
        "payload must contain either (summary_text + rubric_data) for single-summary format "
        "or pillar_summaries for multi-pillar format"
    )


class ExecutiveSummaryVars(TypedDict):
    summary_text: str
    criteria_list: str
    language: str


def adapt_report_evaluator_executive_summary(payload: Dict[str, Any]) -> ExecutiveSummaryVars:
    """
    Mirrors `_generate_executive_summary`'s inputs: summary_text is the
    combined text across pillars for multi-pillar format (joined the same
    way production does, "**{pillar_name}**:\n{summary_text}" blocks
    separated by "\n\n---\n\n"), or the single summary_text otherwise.
    criteria_list is the pillar names joined with "، " (Arabic) or ", "
    (English) -- note the Arabic comma, not a plain ",".
    """
    language = payload.get("language", "ar")

    if "pillar_summaries" in payload and payload["pillar_summaries"]:
        pillar_names = [ps["pillar_name"] for ps in payload["pillar_summaries"]]
        combined = "\n\n---\n\n".join(
            f"**{ps['pillar_name']}**:\n{ps['summary_text']}" for ps in payload["pillar_summaries"]
        )
        summary_text = combined
    elif "summary_text" in payload and "rubric_data" in payload:
        grouped = _group_criteria(payload["rubric_data"]["criteria"], fallback_pillar_name=None)
        pillar_names = list(grouped.keys())
        summary_text = payload["summary_text"]
    else:
        raise ValueError(
            "payload must contain either (summary_text + rubric_data) for single-summary format "
            "or pillar_summaries for multi-pillar format"
        )

    separator = "، " if language == "ar" else ", "
    return ExecutiveSummaryVars(
        summary_text=summary_text,
        criteria_list=separator.join(pillar_names),
        language=language,
    )


# ============================================================
# Attempt Comparator
# ============================================================

class AttemptComparatorVars(TypedDict):
    attempts_data: Dict[str, Any]
    achievements: List[Dict[str, Any]]
    rubric_description: str
    rubric_type_context: str


def adapt_attempt_comparator(payload: Dict[str, Any]) -> AttemptComparatorVars:
    """
    payload: {"attempts_data": {"rubric_type": ..., "achievements": [{"content": [...]}, ...]}, "rubric_data": {...}}

    Byte-for-byte port of the first half of `_create_comparison_prompt`
    (identical on v1 and v2): rubric_description/rubric_type_context are
    resolved here since they don't depend on whether title generation
    succeeds; achievements_text (which DOES depend on that) is built
    separately by build_achievements_text() once titles are known.

    Note the two different sources for what look like the same concept:
    rubric_type_context comes from rubric_data["rubric_type"], while the
    achievements payload's OWN embedded "rubric_type" field (built later,
    only when titles succeed) comes from attempts_data["rubric_type"] --
    a real production quirk, not a typo, replicated as-is.
    """
    if "attempts_data" not in payload or "rubric_data" not in payload:
        raise ValueError("payload must contain 'attempts_data' and 'rubric_data'")

    attempts_data = payload["attempts_data"]
    rubric_data = payload["rubric_data"]
    achievements = attempts_data.get("achievements", [])
    if not achievements:
        raise ValueError("attempts_data.achievements must be a non-empty list")

    rubric_type = rubric_data.get("rubric_type") if isinstance(rubric_data, dict) else None
    rubric_text = str(rubric_data) if rubric_data else "General quality assessment criteria."

    return AttemptComparatorVars(
        attempts_data=attempts_data,
        achievements=achievements,
        rubric_description=f"RUBRIC: {rubric_text}",
        rubric_type_context=f" (Type: {rubric_type})" if rubric_type else "",
    )


def build_title_generator_input(achievements: List[Dict[str, Any]]) -> str:
    """
    Byte-for-byte port of the loop inside `_generate_achievement_titles`
    (identical on v1 and v2): each achievement's `content` list of
    {key: value} dicts is flattened into "key: value" lines (values
    truncated to 300 chars), one block per achievement labeled
    "Achievement {i}:", joined with a blank line between achievements.
    """
    items = []
    for i, ach in enumerate(achievements):
        content_text = ""
        for content_item in ach.get("content", []):
            if isinstance(content_item, dict):
                for k, v in content_item.items():
                    v_str = str(v)[:300] if len(str(v)) > 300 else str(v)
                    content_text += f"{k}: {v_str}\n"
        items.append(f"Achievement {i}:\n{content_text.strip()}")
    return "\n\n".join(items)


def build_achievements_text(
    attempts_data: Dict[str, Any], achievements: List[Dict[str, Any]], titles: Dict[int, Dict[str, str]]
) -> str:
    """
    Byte-for-byte port of the titles-conditional branch inside
    `_create_comparison_prompt` (identical on v1 and v2). `titles` empty
    (title generation unavailable or failed) -> the raw attempts_data dict
    serialized as-is, unsanitized -- whatever extra fields the caller sent
    (e.g. achievement_id) leak straight into the prompt in this branch,
    exactly like production. `titles` non-empty -> achievements stripped
    down to {achievement_title, content} only.
    """
    if titles:
        sanitized_achievements = []
        for i, ach in enumerate(achievements):
            sanitized_achievements.append({
                "achievement_title": titles.get(i, {"ar": f"الإنجاز {i + 1}", "en": f"Achievement {i + 1}"}),
                "content": ach.get("content", []),
            })
        sanitized_data = {
            "rubric_type": attempts_data.get("rubric_type", ""),
            "achievements": sanitized_achievements,
        }
        return json.dumps(sanitized_data, ensure_ascii=False, indent=2)
    return json.dumps(attempts_data, ensure_ascii=False, indent=2)


# ============================================================
# Pillar Summarizer
# ============================================================

# Hard ceiling applied before anything else, regardless of what the request
# asked for -- generate_summaries() does `if target_word_count > 600: = 600`.
# The request model separately allows up to 2000, so a caller CAN ask for 1200
# and silently get 600; that gap is production's, and is surfaced in the UI
# rather than smoothed over.
PILLAR_SUMMARIZER_MAX_TARGET = 600


class PillarSummarizerVars(TypedDict):
    pillar_name: str
    rows: List[Dict[str, Any]]
    data_text: str
    target_words: int
    language: str
    requested_target_words: int
    target_was_clamped: bool


def _build_pillar_data_text(all_data: List[Dict[str, Any]], language: str) -> str:
    """
    Byte-for-byte port of the data_text loop inside create_overall_summary_prompt
    (identical on v1 and v2, only the two labels differ by language).

    The value filter is production's exactly: a cell is skipped when it is
    falsy, blank after stripping, OR the literal string "nan" case-insensitively
    -- the last one because this data originates from Excel via pandas, where an
    empty cell arrives as the float nan and stringifies to "nan".
    """
    header = "عدد الصفوف/الإنجازات: " if language == "ar" else "Number of rows/achievements: "
    row_label = "--- الإنجاز/الصف " if language == "ar" else "--- Achievement/Row "

    data_text = f"{header}{len(all_data)}\n\n"
    for i, row_data in enumerate(all_data, 1):
        data_text += f"{row_label}{i} ---\n"
        for key, value in row_data.items():
            if value and str(value).strip() and str(value).strip().lower() != "nan":
                data_text += f"{key}: {value}\n"
        data_text += "\n"
    return data_text


def adapt_pillar_summarizer(payload: Dict[str, Any], pillar_index: int = 0) -> PillarSummarizerVars:
    """
    payload: {"candidate_info": {...}?, "pillar_data": [{"pillar_name": ..., "rows": [...]}, ...],
              "target_word_count": 575, "language": "ar"|"en", ["model": ...]}

    Production fans every pillar out across a ThreadPoolExecutor and summarizes
    them concurrently. Prompt Lab deliberately does not replicate that (see
    PHASE2_PLAN.md §0): concurrency changes nothing about the prompt or the
    per-pillar result, and running one pillar at a time keeps the comparison
    legible. `pillar_index` picks which one -- the same "let the user choose a
    pillar" pattern already used for Report Evaluator.

    `candidate_info` is accepted and ignored, exactly as production does for
    this call: it is echoed back in the HTTP response but never reaches any
    prompt. Sending it changes nothing about the generated summary.
    """
    pillar_data = payload.get("pillar_data")
    if not pillar_data:
        raise ValueError("payload must contain a non-empty 'pillar_data' list")
    if not 0 <= pillar_index < len(pillar_data):
        raise ValueError(f"pillar_index {pillar_index} out of range for {len(pillar_data)} pillar(s)")

    pillar = pillar_data[pillar_index]
    rows = pillar.get("rows") or []
    if not rows:
        raise ValueError(f"pillar '{pillar.get('pillar_name', '?')}' must have at least one row")

    language = payload.get("language", "ar")
    if language not in ("ar", "en"):
        raise ValueError(f"language must be 'ar' or 'en', got {language!r}")

    requested = payload.get("target_word_count", 575)
    if not isinstance(requested, int) or not 100 <= requested <= 2000:
        raise ValueError(f"target_word_count must be an int in [100, 2000], got {requested!r}")
    target_words = min(requested, PILLAR_SUMMARIZER_MAX_TARGET)

    return PillarSummarizerVars(
        pillar_name=pillar.get("pillar_name", ""),
        rows=rows,
        data_text=_build_pillar_data_text(rows, language),
        target_words=target_words,
        language=language,
        requested_target_words=requested,
        target_was_clamped=target_words != requested,
    )
