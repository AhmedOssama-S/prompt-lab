# Exact request/response schemas and provider-call parameters — Record Evaluator, Report Evaluator & Attempt Comparator

This is the fidelity reference for the runner engine. Everything here was verified directly against the source code (not READMEs — several of those are stale): Record/Report Evaluator on 2026-07-24, Attempt Comparator on 2026-07-27. Both branches referenced throughout: `main` for the v1 folders, `origin/core42-tests` for the v2 (`-unified`) folders (v2 only exists there).

---

## ⚠️ Provenance note (read this first)

`PROMPT_INVENTORY.md` labels its two sets **v1 = `azure-functions/`** and **v2 = `azure-functions-unified/`**. That's accurate for the *folder path*, but not necessarily for *which branch's version of that folder*:

- **Record Evaluator**: `azure-functions/record-evaluator/core/evaluator.py` is **different** between `main` and `core42-tests` (a "refactor terminology rules" commit updated it on `core42-tests` only). `PROMPT_INVENTORY.md`'s "v1" prompt text — with the GROUNDING & SCOPE RULES / TWO-FACET / CONCRETE ANCHOR sections — matches **`core42-tests`'s** version, not `main`'s. `main`'s actual current prompt is older and simpler (no grounding rules, no two-facet construction, no concrete-anchor requirement). `core/models.py` is identical on both branches, so only the prompt wording is affected, not the request/response shape.
- **Report Evaluator**: `azure-functions/report-evaluator/` has **zero diff** between `main` and `core42-tests` — no ambiguity here, one single "v1".
- **Attempt Comparator**: same issue as Record Evaluator, confirmed the same day it was built (PHASE2_PLAN.md documents the discovery in detail). `azure-functions/attempt-comparator/core/comparator.py` differs between `main` (older, simpler: terminology rule at position 9, no grounding/pioneering-classification language) and `core42-tests` (richer: terminology rule at position 11, full "EVALUATION INSTRUCTIONS" section) — `core42-tests`'s version is what's used here, matching `PROMPT_INVENTORY.md`. Once correctly sourced, **the prompt text turned out to be byte-identical between v1 and v2 for this use case too** — the real v1→v2 difference is entirely in `compare()`'s model-fallback logic and the provider-call functions (Claude → Core42), never the prompt itself. `prompts/attempt_comparator/` therefore has no per-version files at all — see that section below.
- **Pillar Summarizer**: re-checked for the same issue while building Attempt Comparator — **zero diff** between `main` and `core42-tests`, same as Report Evaluator. No ambiguity.

**Decision for this tool:** `prompts/record_evaluator/v1.txt` and Attempt Comparator's prompt files use `PROMPT_INVENTORY.md`'s text (i.e., the `core42-tests` branch's version), matching what's already been extracted and what will be compared against v2. If you actually want to benchmark against what's live on `main` right now, that's a *third*, older variant not yet captured anywhere in this tool — flag if you want it added as a `v0`.

---

## Record Evaluator

### v1 — `azure-functions/record-evaluator` (`core42-tests` branch version, matches `PROMPT_INVENTORY.md`)

**Request** (`EvaluationRequest`):
```json
{
  "content": { "...": "..." },
  "rubric_data": { "...": "..." },
  "record_id": "any-json-value"
}
```
- `content`, `rubric_data`: required JSON objects, no fixed sub-schema beyond "not empty."
- `record_id`: required, any JSON-serializable value.
- **No `model` field.** Model selection is automatic: `evaluate()` tries `gemini_pro` → `gemini_flash` → `claude_sonnet`, in that fixed priority order, using whichever have API keys configured. Each model gets up to 3 retries on JSON-parse failure before falling through to the next model in priority order. Rate-limit/auth errors skip retries and move to the next model immediately.

**Provider calls:**
| Provider | Params |
|---|---|
| Gemini Flash/Pro | `contents=prompt`, `config={"response_mime_type": "application/json"}` — **no explicit temperature** (API default) |
| Claude Sonnet | `max_tokens=3000`, `temperature=0.7`, single user message |

**Response** (`RubricEvaluationResponse`, wrapped in `EvaluationResponse`):
```json
{
  "success": true,
  "evaluation": {
    "arabic_analysis": { "strengths": [...], "areas_for_improvement": [...], "recommendations": [...] },
    "english_analysis": { "strengths": [...], "areas_for_improvement": [...], "recommendations": [...] }
  },
  "record_id": "...",
  "request_id": "...",
  "timestamp": "..."
}
```

### v2 — `azure-functions-unified/record-evaluator`

**Request** (`EvaluationRequest`):
```json
{
  "content": { "...": "..." },
  "rubric_data": { "...": "..." },
  "record_id": "any-json-value",
  "model": "gemini_flash | gemini_pro | core42_gpt-5.1 | core42_gpt-4.1"
}
```
- Identical to v1 except **`model` is now required**, validated against a strict 4-value enum. No automatic priority fallback across providers — the caller picks one explicitly. Only Core42 models get an automatic single fallback (to `core42_fallback`) on failure; Gemini models have none.

**Provider calls:**
| Provider | Params |
|---|---|
| Gemini Flash/Pro | Identical to v1 — `response_mime_type: application/json`, no explicit temperature |
| Core42 (gpt-5.1 / gpt-4.1 / fallback) | `temperature=0.3`, `response_format={"type": "json_object"}`, `stream=False`, token param set per deployment: `core42_gpt-5.1` → `max_completion_tokens`, `core42_gpt-4.1`/`core42_fallback` → `max_tokens`, both capped at **16384**. `finish_reason == "length"` is treated as a **failure**, not a valid (truncated) response. |

**Response:** identical shape to v1.

**Prompt text:** identical to v1 (verified byte-for-byte against `PROMPT_INVENTORY.md`) — the only real difference between v1 and v2 for this use case is the request schema (auto-priority vs. explicit model) and the addition of Core42 as a provider option, not the prompt wording itself.

---

## Report Evaluator

### v1 — `azure-functions/report-evaluator` (identical on `main` and `core42-tests` — no ambiguity)

**Request** (`EvaluationRequest`, single-summary XOR multi-pillar, exactly one must be provided):
```json
{
  "summary_text": "min 100 chars",
  "rubric_data": { "title": "...", "criteria": [ { "name": "...", "weight": 0.0, "sub_dimension": null, "performance_levels": [ {"range": "80-100%", "min_percent": 80, "max_percent": 100, "description": "..."} ] } ], "total_weight": 100.0 },
  "model": "gemini_flash | gemini_pro | claude_sonnet | gpt4o",
  "language": "ar | en"
}
```
or
```json
{
  "pillar_summaries": [ { "pillar_name": "...", "summary_text": "...", "rubric_data": { "...": "..." } } ],
  "model": "gemini_flash | gemini_pro | claude_sonnet | gpt4o",
  "language": "ar | en"
}
```
- `model` defaults to `gemini_flash` if omitted; `language` defaults to `ar`.

**Provider calls (per pillar/criterion evaluation):**
| Provider | Params |
|---|---|
| Gemini Flash/Pro | `temperature=0.3`, `max_output_tokens=16384`, `response_mime_type="application/json"`, **`response_schema=CriterionEvaluationResponse.get_genai_schema()`** — native structured output enforced at the API level |
| Claude Sonnet | `max_tokens=4096`, `temperature=0.3`, single user message, **no JSON mode, no response schema** |
| GPT-4o | `temperature=0.3`, `max_tokens=4096`, legacy `openai.ChatCompletion.create`, **no `response_format`** |

**⚠️ Fidelity-critical finding:** the actual prompt text sent (`prompts/report_evaluator/v1/pillar_eval/*`) has **no explicit JSON key-format section at all** — no "return exactly these keys" instruction anywhere in the Arabic/English main body or shared tail. For Gemini this doesn't matter (the schema is enforced natively via `response_schema`), but **Claude and GPT-4o receive the identical prompt text with zero JSON-formatting guidance**, relying entirely on the general "this is a JSON generation task" framing implicit elsewhere in the pipeline. This is a genuine latent fragility in v1's own production code for non-Gemini models — replicate it faithfully in the runner engine rather than "fixing" it, since the whole point is to compare what actually ships.

**Response** (`EvaluationReport`, wrapped in `EvaluationResponse`):
```json
{
  "success": true,
  "report": {
    "introduction": "...", "objectives": ["...", "...", "..."], "methodology_text": "...",
    "executive_summary": "...",
    "criteria_evaluations": [ { "criterion_name": "...", "weight": 0.0, "achieved_percentage": 68.5, "performance_level": "80-100%", "calculated_score": 0.0, "strengths": [...], "improvements": [...], "rationale": "..." } ],
    "total_score": 0.0
  },
  "request_id": "...",
  "timestamp": "..."
}
```

### v2 — `azure-functions-unified/report-evaluator`

**Request:** identical shape to v1, except `model` enum is `gemini_flash | gemini_pro | core42_gpt-5.1 | core42_gpt-4.1` (**Claude and GPT-4o are dropped, Core42 added**).

**Provider calls:**
| Provider | Params |
|---|---|
| Gemini Flash/Pro | Identical to v1 — `temperature=0.3`, `max_output_tokens=16384`, `response_schema` structured output |
| Core42 (gpt-5.1 / gpt-4.1 / fallback) | `temperature=0.3`, token param per deployment (same mapping as Record Evaluator v2), capped at 16384, `response_format={"type": "json_object"}` when structured. **A system message is prepended** when `use_structured=True`: *"You are a JSON response generator. You MUST respond with valid JSON only. No explanations, no markdown, no other text. Start with { and end with }."* (`prompts/report_evaluator/v2/system_message_core42.txt`) — this system message has **no v1 equivalent for this use case's pillar-eval path.** |

**Prompt text differences from v1** (already reflected in `prompts/report_evaluator/v2/`):
- Adds an explicit **"no false scope"** warning next to the percentage-determination step (a location word or identity phrase alone isn't scope evidence).
- Adds explicit **verbatim-preservation / no-embellishment** rules for names, numbers, awards, dates.
- Adds explicit **achievement-type awareness** (don't demand performance numbers from a pioneering/first-of-its-kind item).
- Adds an explicit **improvements-must-stay-individual-scope** constraint.
- Adds an explicit **JSON output-format section** with exact key names — the gap noted above for v1's Claude/GPT-4o path is closed in v2 (though v2 no longer offers Claude/GPT-4o at all, so this mainly matters for the Core42 path, which already gets `response_format=json_object` natively — the explicit key spec is now redundant-but-present belt-and-suspenders).

**Response:** identical shape to v1.

---

## Attempt Comparator

Two-stage call: a preliminary title-generation call (always Gemini Flash, single attempt, swallow-any-exception) runs before the main comparison call. See `PHASE2_PLAN.md` §1 for the full algorithm. Prompt text (main body, both terminology-rule variants, the title-generator prompt, the JSON-forcing system message) is **byte-identical between v1 and v2** — verified via direct diff after correcting the branch-provenance issue (see note above). The real v1→v2 difference is entirely in `compare()`'s fallback logic and the provider-call functions, never the prompt.

### Request/response schema (identical shape both versions)

```json
{
  "attempts_data": {
    "rubric_type": "...",
    "achievements": [ { "content": [ {"key": "value"}, ... ] }, ... ]
  },
  "rubric_data": { "rubric_type": "...", "...": "..." }
}
```
- Field name is **`attempts_data`**, not `achievements_data`. `Dict[str, Any]`, unvalidated internal shape — no `id`/`achievement_id` field is read anywhere on the request side.
- v1: no `model` field (production auto-selects via a fixed priority chain: `claude_sonnet → gemini_pro → gemini_flash`, no user choice at all).
- v2: `model` required, enum `[gemini_flash, gemini_pro, core42_gpt-5.1, core42_gpt-4.1]` (`core42_fallback` is an internal-only fallback target, not user-selectable).

**Response:**
```json
{ "arabic_analysis": { "individual_analysis": "...", "comparative_analysis": "...", "final_ranking": "...", "best_practices": "..." }, "english_analysis": { "...": "..." } }
```
plus a separately-extracted `record_id` (see below) — **not** a field on this model at all.

### The title-generation pre-call (identical both versions)

Always `gemini_flash`, hardcoded — a Core42-only request still needs `GOOGLE_API_KEY` configured to get real titles. Single attempt, no retry. `temperature=0.3`, `max_output_tokens=max(800, n_achievements*100)`, `response_mime_type: application/json`. Wrapped in one swallow-any-exception try/except — **any** failure (missing key, malformed/truncated JSON, whatever) returns `{}` and the main prompt falls back to generic "Input N" / "المُدخل N" phrasing instead of real titles, exactly matching production. This is a real, observed-in-testing failure mode, not theoretical — the 800-token floor is tight enough that a real Gemini call can truncate mid-response for as few as 2 achievements with non-trivial content.

### `record_id` extraction (identical both versions)

Popped out of the raw JSON dict **before** the rest is validated against the response shape above: `record_id = data.pop('record_id', None); if not record_id: raise`. Trusted as-is (`Any` type) — **never cross-checked against the achievement ids that were actually sent in.** If titles succeeded, the model has no explicit id to reference either (the sanitized achievements payload sent to it only contains `achievement_title` + `content`, no id/index) — so in practice `record_id` ends up being whatever the model considers an identifying reference, which may be an index, a title, or something else entirely. Prompt Lab surfaces this raw value in the UI without pretending it's guaranteed-meaningful.

### Provider call parameters — main comparison call

| Provider | v1 | v2 |
|---|---|---|
| Gemini Flash/Pro | `response_mime_type: application/json`, no explicit temperature | identical |
| Claude Sonnet | `max_tokens=4000`, `temperature=0.7`, **system message**: *"You are a JSON response generator..."* | removed in v2 |
| Core42 (gpt-5.1 / gpt-4.1 / fallback) | N/A | `temperature=0.3`, `response_format: json_object`, token cap **16384** via `max_completion_tokens` (gpt-5.1) or `max_tokens` (gpt-4.1/fallback), **same system message as v1's Claude**, explicit truncation check (`finish_reason == "length"` → error) |

### Model fallback (production; not replicated in Prompt Lab, per §0 in `PHASE2_PLAN.md`)

v1: fixed priority `claude_sonnet → gemini_pro → gemini_flash`, tries each available client, 3 retries per model with `2**attempt` backoff. v2: user-picked model only, with Core42 models additionally falling back once to `core42_fallback` on provider-level errors (rate-limit/auth/timeout), never on a JSON-parse failure. **No canned/synthetic fallback response exists anywhere in this use case's code, in either version** — exhausting every model raises straight to an HTTP 503.

---

## Pillar Summarizer

The most complex use case here: three prompt templates chained by a word-count-driven retry loop, with a branch decision at each retry stage. Verified against source on 2026-07-29. `azure-functions/pillar-summarizer/` has **zero diff** between `main` and `core42-tests` (same as Report Evaluator — no provenance ambiguity), so v1 is unambiguous; v2 is `azure-functions-unified/pillar-summarizer/`, which exists only on `core42-tests`.

### Request/response schema

```json
{
  "candidate_info": { "name": "required-if-present", "...": "all other fields optional" },
  "pillar_data": [ { "pillar_name": "...", "rows": [ {"key": "value"}, ... ] } ],
  "target_word_count": 575,
  "language": "ar | en",
  "model": "..."
}
```
- `pillar_data` required, non-empty; each pillar's `rows` must be non-empty.
- `target_word_count` defaults to 575, validated to `[100, 2000]` — then **hard-clamped to ≤600** at call time regardless. A request for 1200 silently gets 600. Prompt Lab surfaces the clamp rather than hiding it.
- `candidate_info` is accepted, echoed back in the HTTP response, and **never reaches any prompt**. It cannot affect the generated summary.
- v1 `model`: optional, defaults to `gemini_flash`, enum includes `claude_sonnet`/`gpt4o`. v2: **required**, enum swaps to `core42_gpt-5.1`/`core42_gpt-4.1`.
- Response per pillar: `{summary, word_count, num_rows, attempts, detected_language}` — identical shape both versions.

### The retry loop (identical logic both versions)

`min_acceptable = target - 100`, `max_acceptable = target` — a **target-relative** band, not a fixed 500–650 one. Up to 3 attempts:

| Attempt | Prompt |
|---|---|
| 1 | `create_overall_summary_prompt` |
| 2 | `create_retry_prompt` → **expand** if `word_count < min_words`, else **condense** |
| 3 (== max_retries) | `create_final_retry_prompt` → **reduce** if `word_count > max_words` (tiers: ≤40 / ≤80 / more), else **expand** (tiers: ≤30 / ≤70 / more) |

Attempts 2 and 3 are built from the **best attempt so far**, not the immediately previous one — these differ whenever attempt 2 lands further from target than attempt 1 did.

Early exit the moment a count lands in range. On exhaustion: prefer the closest attempt that is **not over** `max_acceptable`, even when an over-length attempt is numerically closer; fall back to closest-overall only if every attempt overshot.

**Word counting** is `text.strip().split()` — a plain whitespace split, no punctuation handling, despite the `count_arabic_words` name. Every loop decision rests on it.

**Error handling inside the loop:** rate-limit/auth/timeout re-raise immediately. Everything else (including a JSON parse failure) is swallowed **only if something has already succeeded** — `if not best_summary: raise`. So a first-attempt failure aborts the pillar outright, while a later one just burns an attempt. A JSON failure therefore *consumes* one of the three word-count attempts rather than being retried, unlike every other use case here.

**⚠️ v1 → v2 behavioral difference in `attempts`:** v1 hard-codes `attempts = max_retries` (3) whenever the loop exhausts, even if only one generation actually completed. v2 reports `len(all_attempts)`, the real count. Preserved exactly — it changes what a reviewer sees when a prompt version fails to converge.

### Provider call parameters

| Provider | v1 | v2 |
|---|---|---|
| Gemini Flash/Pro | `temperature=0.3`, `response_mime_type: application/json`, `response_schema=SummaryResponse.get_genai_schema()`. **No `max_output_tokens`** (unlike Report Evaluator's Gemini path, which caps at 16384) | identical |
| Claude Sonnet | `temperature=0.3`, **no `max_tokens` at all** — see below | removed in v2 |
| GPT-4o | `temperature=0.3`, legacy `openai.ChatCompletion.create`, no `max_tokens` (optional for OpenAI, so this one does run) | removed in v2 |
| Core42 | N/A | `temperature=0.3`, `response_format: json_object`, token cap 16384 via the usual per-deployment param, **no system message** (unlike Report Evaluator v2 and Attempt Comparator, both of which prepend one). Truncation is logged as a **warning, not raised** — unlike Attempt Comparator's Core42 path |

**⚠️ v1's Claude path is broken in production.** `_generate_with_claude` calls `messages.create(model=..., temperature=0.3, messages=[...])` with no `max_tokens`, which the Anthropic SDK requires — so it raises `TypeError` before making a request. That error isn't one of the three re-raised types, so the loop swallows it on attempts 2–3 and aborts on attempt 1: **selecting `claude_sonnet` for Pillar Summarizer always fails, today, in production.** Prompt Lab reproduces this rather than inventing a `max_tokens` value (which would produce output production cannot), raising a named `PillarSummarizerClaudeUnsupportedError` so the reason is legible instead of a bare `TypeError`.

> **Not fixed upstream.** This bug is still live in `azure-functions/pillar-summarizer/core/summarizer.py` on `main` — Prompt Lab documents it, it does not patch it. `claude_sonnet` is no longer offered in Prompt Lab's UI for **any** use case (v2 dropped Claude everywhere, and this path can never succeed), though `engine.py` still implements every Claude call path exactly, so the schemas above stay verifiable and `test_engine.py --live` still exercises them when an `ANTHROPIC_API_KEY` is present.

### Response parsing — different from every other use case

`validate_and_parse_summary_json` does **not** strip markdown fences. It:
1. Runs `clean_response_formatting`: removes `**`, `*`, `__`, `_`, and emoji — **from the report body**, not just from wrappers. An underscore inside the generated text is deleted.
2. `json.loads`; on failure, slices between the first `{` and last `}` and retries.
3. Injects `data['language'] = 'ar'` when the key is missing, **before** validating — so a model that omits it is silently treated as Arabic rather than failing.
4. **v2 only:** if `summary` is absent, renames the first match among `report` / `summary_text` / `text` / `التقرير` / `التقرير_الحكومي` / `النص`. v1 has no such rescue and fails instead. Prompt Lab reports when this fires, since a model that needed rescuing did not follow instructions.

One deliberate divergence: production has no guard for a non-object JSON payload and dies with a bare `AttributeError` from its own logging call; the port raises `JSONValidationError`. Both are caught by the same generic handler in the loop, so the attempt outcome is identical — verified across 30 differential cases, this is the only one that differs, and only in exception type.

**Observed on the first live run (2026-07-29): v1's prompt + any Core42 model fails 100% of the time.** Core42 returns `{"language": ..., "report": ...}` — the report under `report`, not `summary`. v1's prompt never names the key, Core42's `json_object` mode enforces no schema (unlike Gemini's `response_schema`, which is why Gemini is unaffected), and v1 has no alternate-key rescue. The failure lands on attempt 1, where `if not best_summary: raise` aborts the whole pillar. v2 fixes this twice over — its prompt shows the literal JSON shape and names the key, *and* it added the rename fallback — and v2 + Core42 succeeded on every model tested. This is the single clearest v1→v2 improvement across all four use cases, and it is invisible from the prompt diff alone.

Not patched, by design: this combination never shipped (v1 predates Core42 entirely), so "fixing" it would mean inventing a v1 that never existed. What *was* fixed is legibility — the failure used to surface as a raw Pydantic `Field required [type=missing]` dump. `validate_and_parse_summary_json` now raises a message naming the key the model actually used and explaining why v1 rejects it, and the Compare page flags the pairing with a warning *before* the run. Same exception type, same control flow, same production-faithful outcome.

**⚠️ Follow-on finding: v2's rename list is incomplete.** Across repeated runs Core42 does not settle on one wrong key — observed so far: `report` **and `content`**. `content` is *not* in v2's `_ALT_SUMMARY_KEYS` (`report` / `summary_text` / `text` / `التقرير` / `التقرير_الحكومي` / `النص`), so **v2's fallback does not rescue it and v2 fails too** when Core42 picks that key. v2's explicit prompt instruction makes the wrong key rarer, but the safety net behind it has a hole. Adding `content` to the list in `azure-functions-unified/pillar-summarizer/core/utils.py` is a one-line upstream change worth making; not applied here, since Prompt Lab mirrors production rather than leading it. The error message distinguishes the two cases so it never claims v2 would have succeeded when it wouldn't.

### Not replicated

Production fans all pillars out across a `ThreadPoolExecutor` (`MAX_PARALLEL_PILLARS = 6`) and, in v2, additionally walks a cross-model fallback sequence. Neither is replicated, per the §0 principle: concurrency changes nothing about the prompt or the per-pillar result, and cross-model fallback would confound "which prompt is better" with "which model answered".

---

## Summary table — what actually changed between v1 → v2

| | Record Evaluator | Report Evaluator | Attempt Comparator | Pillar Summarizer |
|---|---|---|---|---|
| Prompt wording | **Unchanged** (byte-identical) | **Changed**: adds no-false-scope, no-embellishment, achievement-type-awareness, individual-scope, explicit JSON key spec | **Unchanged** (byte-identical) | **Changed**: first-person enforced, 20→15 sentence cap, impact-ordering, number/effect pairing, verbatim-facts, explicit JSON key spec |
| Request schema | `model` field added (was auto-priority, now required enum) | `model` enum swapped (Claude/GPT-4o → Core42 gpt-5.1/gpt-4.1) | `model` field added (was fixed priority chain, now required enum) | `model` becomes required (was optional, defaulted `gemini_flash`) |
| Providers available | Gemini + Claude → Gemini + Core42 | Gemini + Claude + GPT-4o → Gemini + Core42 | Gemini + Claude → Gemini + Core42 | Gemini + Claude + GPT-4o → Gemini + Core42 |
| New system message | None | Core42 path only, no v1 equivalent | None — same system message reused verbatim from v1's Claude call | None — Core42 path gets no system message at all |
| Response schema | Unchanged | Unchanged | Unchanged | Unchanged |
| Other behavioral | — | — | — | `attempts` on give-up: v1 hard-codes 3, v2 reports the real count. v2 adds alternate-JSON-key rescue. |
