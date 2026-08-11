"""
JSON extraction/validation, mirrored from the real
core/utils.py::validate_and_parse_json in both azure-functions/ and
azure-functions-unified/ -- same markdown-fence stripping behavior, same
"invalid JSON or wrong shape = retry-worthy failure" semantics, so a
prompt version that produces malformed output here would have failed
identically in production instead of being penalized by a different,
stricter (or looser) parser than the real one.
"""

import json
import re
from typing import Any, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class JSONParseError(Exception):
    """Raised when the response isn't valid JSON, or is empty."""


class JSONValidationError(Exception):
    """Raised when the response is valid JSON but doesn't match the expected shape."""


def extract_json_str(response_text: str) -> str:
    """Strip a ```json ... ``` or ``` ... ``` markdown fence if present, else return as-is."""
    if not response_text:
        raise JSONParseError("Empty response received from AI model")

    json_str = response_text.strip()

    if "```json" in response_text:
        start = response_text.find("```json") + 7
        end = response_text.rfind("```")
        if end > start:
            json_str = response_text[start:end].strip()
        else:
            raise JSONParseError("Malformed markdown code block in response")
    elif "```" in response_text:
        start = response_text.find("```") + 3
        end = response_text.rfind("```")
        if end > start:
            json_str = response_text[start:end].strip()

    return json_str


def validate_and_parse_json(response_text: str, schema: Type[T]) -> T:
    """Extract JSON from response_text and validate it against `schema`. Raises on failure."""
    json_str = extract_json_str(response_text)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise JSONParseError(f"JSON decode error: {e}") from e

    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise JSONValidationError(f"Response did not match expected shape: {e}") from e


def validate_and_parse_comparison_json(response_text: str):
    """
    Attempt Comparator only -- different validation shape than
    validate_and_parse_json() above, because production's own
    core/utils.py does something different for this one use case:
    record_id is popped out of the raw dict BEFORE the rest is validated
    against ComparisonResponse, and trusted as-is (Any type), never
    cross-checked against the achievement ids that were actually sent in.
    Identical logic on v1 and v2. Returns (ComparisonResponse, record_id).
    """
    from .models import ComparisonResponse  # local import: avoids a models<->json_utils import cycle

    json_str = extract_json_str(response_text)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise JSONParseError(f"JSON decode error: {e}") from e

    if not isinstance(data, dict):
        raise JSONValidationError("Response did not match expected shape: expected a JSON object")

    record_id: Any = data.pop("record_id", None)
    if not record_id:
        raise JSONValidationError("record_id is required in the response")

    try:
        parsed = ComparisonResponse.model_validate(data)
    except ValidationError as e:
        raise JSONValidationError(f"Response did not match expected shape: {e}") from e

    return parsed, record_id


# ---------- Pillar Summarizer ----------
# This use case does NOT share extract_json_str() above. Its production parser
# (core/utils.py::validate_and_parse_summary_json) takes a different route
# entirely: strip formatting characters, try json.loads, and on failure fall
# back to slicing between the first "{" and last "}" -- it never looks for a
# markdown fence. Using the shared helper here would quietly change which
# malformed responses survive, which is exactly the signal being measured.

# Verbatim port of core/utils.py::clean_response_formatting's emoji pattern.
# Built from explicit code points rather than inline literals: the source's
# class includes ZWJ (200D) and VARIATION SELECTOR-16 (FE0F), which are
# invisible in an editor and silently corruptible on copy/paste. Each entry is
# (start, end) inclusive, in the same order as the original character class.
_EMOJI_RANGES = [
    (0x1F600, 0x1F64F),  # emoticons
    (0x1F300, 0x1F5FF),  # symbols & pictographs
    (0x1F680, 0x1F6FF),  # transport & map symbols
    (0x1F1E0, 0x1F1FF),  # flags (iOS)
    (0x2500, 0x2BEF),    # chinese char
    (0x2702, 0x27B0),
    (0x24C2, 0x1F251),
    (0x1F926, 0x1F937),
    (0x10000, 0x10FFFF),
    (0x2640, 0x2642),
    (0x2600, 0x2B55),
    (0x200D, 0x200D),    # zero-width joiner
    (0x23CF, 0x23CF),
    (0x23E9, 0x23E9),
    (0x231A, 0x231A),
    (0xFE0F, 0xFE0F),    # variation selector-16 ("dingbats" in the original comment)
    (0x3030, 0x3030),
    (0x1F900, 0x1F9FF),  # Supplemental Symbols and Pictographs
    (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-A
]

_EMOJI_PATTERN = re.compile(
    "[" + "".join(
        chr(lo) if lo == hi else f"{chr(lo)}-{chr(hi)}" for lo, hi in _EMOJI_RANGES
    ) + "]+",
    flags=re.UNICODE,
)

# The original also has a second, redundant explicit-symbol sweep after the
# range pass. Same construction rationale.
_EXTRA_SYMBOLS = [0x1F3AF, 0x1F527, 0x2705, 0x1F680, 0x1F4AA, 0x26A0, 0xFE0F,
                  0x1F4CB, 0x1F4CA, 0x1F916, 0x1F1F8, 0x1F1E6, 0x1F1FA, 0x1F1F8]
_EXTRA_SYMBOL_PATTERN = re.compile("[" + "".join(chr(c) for c in _EXTRA_SYMBOLS) + "]")

# v2 only. Core42 uses loose json_object mode (no enforced schema, unlike
# Gemini's response_schema), so production added a rename pass for models that
# emit the report under the wrong key. Order matters -- first hit wins.
_ALT_SUMMARY_KEYS = ("report", "summary_text", "text", "التقرير", "التقرير_الحكومي", "النص")


def clean_response_formatting(response_text: str) -> str:
    """Verbatim port of core/utils.py::clean_response_formatting (identical v1/v2).

    Note this strips '*' and '_' from the *report body*, not just from markdown
    fences -- an underscore inside the generated Arabic/English text is removed
    too. That is production's real behavior and a genuine (if surprising) part
    of what a prompt version's output goes through before anyone reads it.
    """
    if not response_text:
        return ""

    cleaned_text = response_text.replace("**", "")
    cleaned_text = cleaned_text.replace("*", "")
    cleaned_text = cleaned_text.replace("__", "")
    cleaned_text = cleaned_text.replace("_", "")
    cleaned_text = _EMOJI_PATTERN.sub(r"", cleaned_text)
    cleaned_text = _EXTRA_SYMBOL_PATTERN.sub("", cleaned_text)
    return cleaned_text.strip()


def validate_and_parse_summary_json(response_text: str, version: str):
    """
    Pillar Summarizer only. Mirrors core/utils.py::validate_and_parse_summary_json.

    `version` selects the one real behavioral difference: v2 renames an
    alternate summary key to "summary" before validating, v1 has no such
    fallback and fails instead. That difference is prompt-quality signal --
    v2's prompt spells out the exact key name, so a v2 model that still gets
    it wrong got rescued by code, and the UI should be able to say so.
    """
    from .models import SummaryResponse  # local import: avoids a models<->json_utils cycle

    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown version: {version}")

    cleaned = clean_response_formatting(response_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Production's fallback: slice between the first "{" and last "}".
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            try:
                data = json.loads(cleaned[start_idx:end_idx + 1])
            except json.JSONDecodeError:
                raise JSONParseError(f"Failed to parse JSON: {e}") from e
        else:
            raise JSONParseError(f"Failed to parse JSON: {e}") from e

    # The ONE deliberate divergence from production in this function. If a model
    # returns a JSON array instead of an object, production has no guard here and
    # dies with a bare AttributeError from its own logging call. Both that and
    # this JSONValidationError are caught by the same generic `except Exception`
    # in the retry loop (neither is a RateLimit/Auth/Timeout error, which are the
    # only ones re-raised), so the attempt fails and the loop proceeds identically
    # either way -- verified in scratchpad/psx/verify_parser.py, where this is the
    # only case out of 30 that differs, and only in exception type, not outcome.
    if not isinstance(data, dict):
        raise JSONValidationError("Response did not match expected shape: expected a JSON object")

    remapped_from = None
    if version == "v2" and "summary" not in data:
        for alt_key in _ALT_SUMMARY_KEYS:
            if alt_key in data and isinstance(data[alt_key], str):
                data["summary"] = data.pop(alt_key)
                remapped_from = alt_key
                break

    # Production injects the default BEFORE validating, so a missing key is
    # silently treated as Arabic rather than being a validation failure.
    if "language" not in data:
        data["language"] = "ar"

    # By far the most common real failure for this use case, and a raw Pydantic
    # "Field required [type=missing]" dump explains none of it. Same exception
    # type and same control flow as the generic path below -- only the message
    # differs, so loop behavior stays identical to production.
    if "summary" not in data:
        other_keys = [k for k in data if k != "language"]
        present = ", ".join(f'"{k}"' for k in other_keys) or "(no content key at all)"
        # Only claim v2 would have rescued this if v2's rename list actually
        # covers the key the model used. Core42 has been observed emitting
        # "content", which is NOT in that list -- v2 fails on it too.
        rescuable = [k for k in other_keys if k in _ALT_SUMMARY_KEYS and isinstance(data[k], str)]
        why_common = (
            "\n\nMost likely with Core42 or another provider whose JSON mode enforces no schema. Gemini is "
            "unaffected because its response_schema pins the key at the API level."
        )

        if version == "v1":
            if rescuable:
                v2_note = (
                    'v2 fixes this twice over -- its prompt shows the literal JSON shape and names "summary" '
                    f'explicitly, and its parser renames "{rescuable[0]}" automatically -- so this exact '
                    "response would have succeeded on v2."
                )
            else:
                v2_note = (
                    'v2 would very likely have avoided this (its prompt shows the literal JSON shape and names '
                    f'"summary" explicitly), but note v2 would NOT have rescued this exact response either: '
                    f"its rename list covers only {', '.join(_ALT_SUMMARY_KEYS)}."
                )
            raise JSONValidationError(
                f'The model returned its report under {present} instead of "summary", so v1 rejected it.\n\n'
                "This is production-faithful, not a bug in Prompt Lab. v1's prompt never states the key name, "
                f"and v1 has no rename fallback at all. {v2_note}{why_common}"
            )

        raise JSONValidationError(
            f'The model returned its report under {present} instead of "summary", despite v2\'s prompt naming '
            "the key explicitly -- and v2's rename fallback did not cover it (it handles only "
            f"{', '.join(_ALT_SUMMARY_KEYS)})." + why_common
        )

    try:
        parsed = SummaryResponse.model_validate(data)
    except ValidationError as e:
        raise JSONValidationError(f"Response did not match expected shape: {e}") from e

    return parsed, remapped_from
