"""
Loads and assembles prompt text from prompt-lab/prompts/.

Record Evaluator prompts are single static files. Report Evaluator prompts
are assembled from fragments at runtime -- main body + a conditional
Learning & Development block + a shared tail -- mirroring exactly how the
real `_create_pillar_evaluation_prompt` builds it in
core/evaluator.py (both azure-functions/ and azure-functions-unified/):
the L&D block is appended ONLY when pillar_name is "تعلم و تطور" or
"Learning & Development", verbatim, nothing else conditional.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Tuple

from . import prompt_overrides

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Exact match required -- this is the real code's own condition, not a
# fuzzy/case-insensitive check. A pillar named anything else never gets
# the L&D block, in production or here.
LD_PILLAR_NAMES = {"تعلم و تطور", "Learning & Development"}


def _read(*parts: str) -> str:
    """Single choke point for every prompt fragment this module loads.

    Consults runner/prompt_overrides.py first so a business-team draft can be
    swapped in for one file without changing anything else about the run.
    Falls through to the stored file when no override is active, which is the
    normal case.
    """
    override = prompt_overrides.lookup(parts)
    if override is not None:
        return override
    path = PROMPTS_DIR.joinpath(*parts)
    return path.read_text(encoding="utf-8")


# ---------- Record Evaluator ----------

def load_record_evaluator_prompt(version: str) -> str:
    """Returns the raw template (still containing {input_content} / {rubric_data})."""
    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown version: {version}")
    return _read("record_evaluator", f"{version}.txt")


def render_record_evaluator_prompt(version: str, input_content: str, rubric_data: str) -> str:
    """
    Safe to .format() as a whole: this template's JSON example block uses
    {{ }} escaping (matching the real source, which is one single f-string
    top to bottom for this use case -- verified in schemas/SCHEMAS.md).
    """
    return load_record_evaluator_prompt(version).format(input_content=input_content, rubric_data=rubric_data)


# ---------- Report Evaluator ----------

def load_report_evaluator_pillar_prompt(version: str, language: str, pillar_name: str) -> str:
    """
    Returns the RAW assembled template (main body + conditional L&D block +
    shared tail), still containing {pillar_name} / {total_weight} /
    {summary_text} / {subdimensions_text} -- for inspection/debugging only.
    Do NOT call .format() on this directly: see render_report_evaluator_pillar_prompt().
    """
    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown version: {version}")
    if language not in ("ar", "en"):
        raise ValueError(f"Unknown language: {language}")

    main = _read("report_evaluator", version, "pillar_eval", f"main_{language}.txt")
    prompt = main

    if pillar_name in LD_PILLAR_NAMES:
        prompt += _read("report_evaluator", version, "pillar_eval", f"ld_block_{language}.txt")

    prompt += _read("report_evaluator", version, "pillar_eval", f"shared_tail_{language}.txt")
    return prompt


def render_report_evaluator_pillar_prompt(
    version: str, language: str, pillar_name: str, total_weight: float, summary_text: str, subdimensions_text: str
) -> str:
    """
    Correctly rendered version of the above -- .format() is applied ONLY to
    the main fragment, then the (unformatted) conditional L&D block and
    shared tail are appended after, exactly matching production's own code:
    the main body is built as an f-string with real substitutions, while the
    L&D block and shared tail are appended afterwards via plain (non-f)
    triple-quoted strings.

    This distinction matters: the shared tail (v2) contains a literal JSON
    example block with single braces, e.g. a line like
    achieved_percentage: <number>, wrapped in { and } as plain text, that is
    NOT meant to be a substitution target -- it's literal text in a plain
    string in the real source, not part of an f-string. Calling .format() on
    the full main+ld_block+tail concatenation would raise KeyError on that
    block. Formatting the main fragment alone and concatenating the rest raw
    reproduces production's actual behavior exactly.
    """
    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown version: {version}")
    if language not in ("ar", "en"):
        raise ValueError(f"Unknown language: {language}")

    main = _read("report_evaluator", version, "pillar_eval", f"main_{language}.txt")
    rendered = main.format(
        pillar_name=pillar_name,
        total_weight=total_weight,
        summary_text=summary_text,
        subdimensions_text=subdimensions_text,
    )

    if pillar_name in LD_PILLAR_NAMES:
        rendered += _read("report_evaluator", version, "pillar_eval", f"ld_block_{language}.txt")

    rendered += _read("report_evaluator", version, "pillar_eval", f"shared_tail_{language}.txt")
    return rendered


def load_report_evaluator_executive_summary_prompt(version: str, language: str) -> str:
    """Returns the raw template, still containing {summary_text} / {criteria_list}."""
    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown version: {version}")
    if language not in ("ar", "en"):
        raise ValueError(f"Unknown language: {language}")
    return _read("report_evaluator", version, "executive_summary", f"{language}.txt")


def render_report_evaluator_executive_summary_prompt(version: str, language: str, summary_text: str, criteria_list: str) -> str:
    """Safe to .format() as a whole -- this template has no literal JSON example block."""
    return load_report_evaluator_executive_summary_prompt(version, language).format(
        summary_text=summary_text, criteria_list=criteria_list
    )


def load_report_evaluator_core42_system_message(version: str) -> str | None:
    """
    v2 only: the system message prepended for Core42 structured calls.
    v1 has no equivalent for this use case -- returns None, and callers
    must NOT invent one, since the whole point is faithfully reproducing
    that v1's Claude/GPT-4o path gets zero JSON-formatting guidance (see
    schemas/SCHEMAS.md's fidelity-critical finding).
    """
    if version == "v2":
        return _read("report_evaluator", "v2", "system_message_core42.txt").strip()
    return None


# ---------- Attempt Comparator ----------
# Unlike Report Evaluator, there is no per-version branch anywhere in this
# section: the prompt text (main body, both terminology-rule variants, the
# title-generator prompt, and the JSON-forcing system message) is verified
# byte-identical between v1 and v2 for this use case -- see the provenance
# note in schemas/SCHEMAS.md. Only the request schema and provider roster
# differ by version, both handled in runner/engine.py, not here.

def render_attempt_comparator_title_generator_prompt(all_content: str) -> str:
    return _read("attempt_comparator", "title_generator.txt").format(all_content=all_content)


def load_attempt_comparator_system_message() -> str:
    return _read("attempt_comparator", "system_message.txt").strip()


def render_attempt_comparator_prompt(
    rubric_description: str, rubric_type_context: str, achievements_text: str, has_titles: bool
) -> str:
    """
    Safe to .format() as a whole: production builds this prompt as one
    single f-string top to bottom (verified against core/comparator.py),
    so main_prompt.txt's literal JSON example block uses {{ }} escaping,
    matching record_evaluator's pattern rather than report_evaluator's
    fragment-concatenation one.
    """
    terminology_rule_file = "terminology_rule_with_titles.txt" if has_titles else "terminology_rule_without_titles.txt"
    terminology_rule = _read("attempt_comparator", terminology_rule_file)
    return _read("attempt_comparator", "main_prompt.txt").format(
        rubric_description=rubric_description,
        rubric_type_context=rubric_type_context,
        achievements_text=achievements_text,
        terminology_rule=terminology_rule,
    )


# ---------- Pillar Summarizer ----------
# Three prompt templates per language per version, selected by the retry loop's
# stage and by how far the previous attempt's word count missed. Every template
# is one single f-string in production (core/utils.py::create_*_prompt), so each
# is safe to .format() whole -- the record_evaluator/attempt_comparator pattern,
# not report_evaluator's fragment concatenation.
#
# All 20 files were extracted programmatically from the real prompt builders and
# verified to round-trip byte-for-byte (scratchpad/psx/extract.py), rather than
# hand-transcribed.

# Tier thresholds, verbatim from create_final_retry_prompt. The operation string
# each one selects lives in final_retry_operations.json alongside the templates.
_REDUCE_TIERS = ((40, "le40"), (80, "le80"))   # else -> "gt80"
_EXPAND_TIERS = ((30, "le30"), (70, "le70"))   # else -> "gt70"

_PS_TOLERANCE = 100  # min_acceptable = target - 100, matching the loop's default


def _check_ps_args(version: str, language: str) -> None:
    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown version: {version}")
    if language not in ("ar", "en"):
        raise ValueError(f"Unknown language: {language}")


@lru_cache(maxsize=None)
def _cached_final_retry_operations(version: str) -> dict:
    return json.loads(PROMPTS_DIR.joinpath("pillar_summarizer", version, "final_retry_operations.json").read_text(encoding="utf-8"))


def _final_retry_operations(version: str) -> dict:
    # Cache only the on-disk read. Going through _read() under lru_cache would
    # make the first call's result outlive any prompt_override() scope, silently
    # serving stale text. The Prompts page doesn't currently expose this file
    # for editing, but the caching bug would be invisible if it ever did.
    parts = ("pillar_summarizer", version, "final_retry_operations.json")
    override = prompt_overrides.lookup(parts)
    if override is not None:
        return json.loads(override)
    return _cached_final_retry_operations(version)


def render_pillar_summarizer_overall_prompt(version: str, language: str, data_text: str, target_words: int) -> str:
    """Attempt 1. `data_text` comes from input_adapters.build_pillar_data_text()."""
    _check_ps_args(version, language)
    return _read("pillar_summarizer", version, f"overall_{language}.txt").format(
        data_text=data_text,
        min_words=target_words - _PS_TOLERANCE,
        max_words=target_words,
        target_minus_50=target_words - 50,
    )


def render_pillar_summarizer_retry_prompt(
    version: str, language: str, target_words: int, current_word_count: int, previous_summary: str
) -> Tuple[str, str]:
    """Attempt 2. Returns (prompt, branch) where branch is "expand" or "condense".

    Branch condition is production's exactly: `current_word_count < min_words`
    selects expansion, everything else (including an exactly-on-target count that
    somehow reached here) selects condensation.

    Note production passes `all_data` into create_retry_prompt but never
    interpolates it -- the retry prompts work purely off the previous summary.
    Not plumbed through here for that reason.
    """
    _check_ps_args(version, language)
    min_words = target_words - _PS_TOLERANCE
    branch = "expand" if current_word_count < min_words else "condense"
    prompt = _read("pillar_summarizer", version, f"retry_{branch}_{language}.txt").format(
        min_words=min_words,
        max_words=target_words,
        target_minus_50=target_words - 50,
        current_word_count=current_word_count,
        word_diff_abs=abs(target_words - current_word_count),
        previous_summary=previous_summary,
    )
    return prompt, branch


def render_pillar_summarizer_final_retry_prompt(
    version: str, language: str, target_words: int, current_word_count: int, previous_summary: str
) -> Tuple[str, str, str]:
    """Attempt 3 (the last attempt). Returns (prompt, branch, tier).

    branch is "reduce" (overshoot) or "expand" (undershoot); tier names which of
    the three escalating operation strings was injected.
    """
    _check_ps_args(version, language)
    min_words = target_words - _PS_TOLERANCE
    ops = _final_retry_operations(version)

    if current_word_count > target_words:
        branch = "reduce"
        over = current_word_count - target_words
        tier = next((name for bound, name in _REDUCE_TIERS if over <= bound), "gt80")
        prompt = _read("pillar_summarizer", version, f"final_retry_reduce_{language}.txt").format(
            min_words=min_words,
            max_words=target_words,
            over=over,
            operation=ops["reduce"][language][tier],
            previous_summary=previous_summary,
        )
    else:
        branch = "expand"
        under = min_words - current_word_count
        tier = next((name for bound, name in _EXPAND_TIERS if under <= bound), "gt70")
        # `under` is deliberately absent from the format call: production computes
        # it only to pick the tier and never prints it in this branch, unlike the
        # reduce branch which prints "reduced by [N words]".
        prompt = _read("pillar_summarizer", version, f"final_retry_expand_{language}.txt").format(
            min_words=min_words,
            max_words=target_words,
            operation=ops["expand"][language][tier],
            previous_summary=previous_summary,
        )
    return prompt, branch, tier
