"""Differential test: prompt-lab's ported summary-JSON parser vs production's.

Feeds the same responses to both and asserts identical outcomes (parsed values,
or the same failure category). Covers the paths that actually differ between
implementations: formatting-strip, brace-slice fallback, missing-language
default, and v2's alternate-key rename.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

from _source import assert_v1_branch_agnostic, materialize

assert_v1_branch_agnostic()
materialize()

from runner import json_utils as lab  # noqa: E402 -- needs the sys.path setup above


CASES = [
    ("plain", '{"summary": "نص التقرير", "language": "ar"}'),
    ("english", '{"summary": "Report body here", "language": "en"}'),
    ("missing_language", '{"summary": "no language key"}'),
    ("prose_wrapped", 'Here is the JSON you asked for:\n{"summary": "wrapped", "language": "en"}\nHope that helps!'),
    ("markdown_fence", '```json\n{"summary": "fenced", "language": "en"}\n```'),
    ("bold_markers", '{"summary": "**bold** text", "language": "en"}'),
    ("underscores", '{"summary": "snake_case word", "language": "en"}'),
    ("emoji", '{"summary": "done \U0001F680 shipped", "language": "en"}'),
    ("alt_key_report", '{"report": "under the wrong key", "language": "en"}'),
    ("alt_key_arabic", '{"\u0627\u0644\u062a\u0642\u0631\u064a\u0631": "\u0646\u0635", "language": "ar"}'),
    ("alt_key_text", '{"text": "as text key", "language": "en"}'),
    ("not_an_object", '["a", "b"]'),
    ("garbage", 'I cannot produce JSON for this request.'),
    ("empty", ''),
    ("truncated", '{"summary": "cut off mid'),
]


def outcome_lab(text, version):
    try:
        parsed, remapped = lab.validate_and_parse_summary_json(text, version)
        return ("ok", parsed.summary, parsed.language)
    except lab.JSONParseError:
        return ("parse_error",)
    except lab.JSONValidationError:
        return ("validation_error",)


def outcome_prod(text, mod):
    from ps_v1.exceptions import JSONParseError as PE1, JSONValidationError as VE1
    try:
        r = mod.validate_and_parse_summary_json(text)
        return ("ok", r.summary, r.language)
    except Exception as e:
        name = type(e).__name__
        if "Parse" in name:
            return ("parse_error",)
        if "Validation" in name:
            return ("validation_error",)
        return ("other", name)


# Documented, intentional divergence -- see the comment on the isinstance guard
# in runner/json_utils.py::validate_and_parse_summary_json. Production has no
# guard for a non-object payload and dies with a bare AttributeError from its
# own logging call; the port raises JSONValidationError instead. Both are caught
# by the same generic handler in the retry loop, so the attempt outcome is
# identical. Listed here so it stays an explicit exception, not silent drift.
EXPECTED_DIVERGENCES = {
    ("v1", "not_an_object"): (("validation_error",), ("other", "AttributeError")),
    ("v2", "not_an_object"): (("validation_error",), ("other", "AttributeError")),
}


def main():
    failures = []
    for version, pkg in (("v1", "ps_v1"), ("v2", "ps_v2")):
        mod = __import__(f"{pkg}.utils", fromlist=["utils"])
        for name, text in CASES:
            got = outcome_lab(text, version)
            want = outcome_prod(text, mod)
            if EXPECTED_DIVERGENCES.get((version, name)) == (got, want):
                status = "DIV"
            elif got == want:
                status = "OK "
            else:
                status = "FAIL"
                failures.append((version, name, got, want))
            print(f"[{status}] {version}/{name:18s} lab={got!r:60s} prod={want!r}")
    print()
    if failures:
        print(f"{len(failures)} MISMATCH(ES):")
        for v, n, got, want in failures:
            print(f"  {v}/{n}\n    lab : {got!r}\n    prod: {want!r}")
        sys.exit(1)
    print(f"All {len(CASES) * 2} differential checks PASSED "
          f"({len(EXPECTED_DIVERGENCES)} documented divergence(s), marked DIV)")


if __name__ == "__main__":
    main()
