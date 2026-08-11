# Pillar Summarizer fidelity tools

Three scripts that check Prompt Lab's Pillar Summarizer port against the real
Medals-AI source by importing production's own modules and running them
side by side. They need the Medals-AI git repo, not a deployed function — no
API keys, no network.

By default the repo is expected at `../../../../Medals-AI` (i.e. `sword/Medals-AI`).
Override with `MEDALS_AI_REPO=/path/to/Medals-AI`.

All three first assert that `azure-functions/pillar-summarizer/` is still
identical between `main` and `origin/core42-tests`. That is what makes "v1"
unambiguous for this use case — unlike Record Evaluator and Attempt Comparator,
where the same folder genuinely differs between branches (see the provenance
note in `schemas/SCHEMAS.md`). If it ever stops being true, the scripts stop
rather than silently comparing against the wrong thing.

## `extract.py` — regenerate the 20 prompt templates

```bash
python extract.py
```

Calls production's `create_overall_summary_prompt` / `create_retry_prompt` /
`create_final_retry_prompt` with sentinel values chosen so every interpolated
slot renders as a unique token, reverse-substitutes those back into
`{placeholder}` form, re-escapes the literal JSON-example braces, and then
asserts each result re-renders **byte-for-byte identical** to production's
output across both languages, both retry branches, and all six tiers
(18 round-trip checks per version).

Writes to `out/` rather than over `prompts/pillar_summarizer/`. Installing is a
separate, deliberate step:

```bash
diff -r out ../../prompts/pillar_summarizer --exclude=judge_criteria.yaml   # review first
cp -r out/v1 out/v2 ../../prompts/pillar_summarizer/
```

Run this after any upstream prompt change. A clean `diff` means the stored
templates are still exact.

## `verify_parser.py` — response parsing

Feeds 15 responses (well-formed, prose-wrapped, markdown-fenced, emoji,
underscores, alternate JSON keys, truncated, non-object, empty) to both
`runner/json_utils.py::validate_and_parse_summary_json` and production's, and
asserts identical outcomes for each, per version.

One case is a documented divergence, marked `DIV` in the output: on a non-object
payload production dies with a bare `AttributeError` from its own logging call
while the port raises `JSONValidationError`. Both are caught by the same generic
handler in the retry loop, so the attempt outcome is identical. It is listed
explicitly in the script so it stays an accepted exception rather than drift.

## `verify_loop.py` — the retry loop

Drives production's `_generate_summary_with_retry` and the port's
`summarize_pillar_with_retry` from the same scripted sequence of fake model
responses (no network), then compares the final word count, the `attempts`
figure, the reported language, and the sequence of prompt stages actually sent.

15 scenarios per version cover early exit, both retry branches, the inclusive
range boundaries, the give-up preference for a not-too-long attempt over a
numerically closer over-length one, "best so far" not being "previous", and the
error-handling asymmetry (a first-attempt failure aborts; a later one is
swallowed). It also pins the v1/v2 difference in what `attempts` reports on
give-up — v1 hard-codes 3, v2 reports the real count.
