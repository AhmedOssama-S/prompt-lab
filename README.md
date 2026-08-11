# Prompt Lab

Standalone tool for comparing Medals-AI prompt versions (manual side-by-side + automated LLM-judge), independent of the Medals-AI repo/deployment. See [`../PROMPT_LAB_PLAN.md`](../PROMPT_LAB_PLAN.md) for the full design and [`../PHASE2_PLAN.md`](../PHASE2_PLAN.md) for the two later use cases.

**Status: all four Azure Function use cases are implemented and verified live.**

| Use case | v1 | v2 | Auto-judge | Notes |
|---|---|---|---|---|
| Record Evaluator | ✅ | ✅ | ✅ | Prompt text byte-identical between versions |
| Report Evaluator | ✅ | ✅ | ✅ (pillar eval only) | Pillar evaluation + executive summary |
| Attempt Comparator | ✅ | ✅ | ✅ | Two-stage: Gemini-Flash title pre-call, then comparison |
| Pillar Summarizer | ✅ | ✅ | ✅ | Three prompt templates chained by a word-count retry loop |

Still open: double-order-swap judging (A/B positions swapped to cancel position bias — a known limitation, deliberately deferred), and a leaderboard / aggregate win-rate view over `results/runs.jsonl`.

## The three pages

| Page | For | Does |
|---|---|---|
| **Compare** | Anyone | Run one real input through two (version, model) combinations side by side, with an inline auto-judge |
| **Prompts** | Business / content team | Write a new version of a prompt, validate it, propose it, approve or reject it |
| **Handoff** | Developers | See approved proposals with the exact file to change, a diff, and the evidence behind the approval |

### How a prompt change travels

```
Prompts page          Compare page              Prompts page         Handoff page        Developer
write a version  ──▶  test it vs current  ──▶   approve it     ──▶   pick it up    ──▶   edits the code
                      (real engine + judge)                          (file + diff)
```

**Prompt Lab never writes to `prompts/` and never touches the Medals-AI repo.** Approving a proposal raises a flag; a developer applies it by hand. That keeps the people who judge wording separate from the people who change a live deployment.

Drafts live in `drafts/<id>/` as `meta.json` + `draft.txt` — two files rather than one JSON blob so a developer can diff `draft.txt` directly against the real prompt instead of un-escaping Arabic out of a JSON string. They're committed to git on purpose: that's the handoff channel.

**A draft replaces exactly one prompt file** and inherits everything else from the version it was written against, so testing it runs the identical pipeline — same fragments, same retry loop, same per-provider call parameters. Implemented as a contextvar consulted by `prompt_loader._read()` (`runner/prompt_overrides.py`), scoped to a single side's run so it can't leak between sides or across Streamlit reruns.

Two guardrails worth knowing about:
- **Saving validates the template.** A stray unescaped `{` is blocked at save time with a plain-English message, as is dropping or inventing a `{placeholder}`. Without this the failure surfaces mid-run, after the model call on some paths, far from its cause.
- **Approval requires a recorded test run**, and editing an approved draft resets it to draft — otherwise approval could be granted on one text and applied to another.

## Layout

```
prompt-lab/
├── README.md                 (this file)
├── requirements.txt
├── .env.example                # copy to .env — key names match production exactly
├── .claude/launch.json          # preview config: streamlit run streamlit_app.py
├── streamlit_app.py              # entry point, st.navigation router
├── app_pages/
│   └── compare.py                 # side-by-side comparison + inline auto-judge
├── render_helpers.py               # result cards per use case, incl. the attempt-trajectory panel
├── sample_payloads.py                # one standard sample per use case, auto-filled on use-case switch
├── test_engine.py               # headless smoke test: `python test_engine.py` (dry run, no keys needed)
│                                 or `python test_engine.py --live` (fires real calls with .env configured)
├── results/
│   ├── logger.py                 # append_run() -> runs.jsonl
│   └── runs.jsonl                 # created on first logged comparison (gitignored)
├── runner/
│   ├── prompt_loader.py         # loads/assembles prompt fragments; render_*() do the .format() substitution correctly
│   ├── input_adapters.py         # request payload -> the exact variables each template needs
│   ├── engine.py                  # candidate-call path: exact per-provider params, same-model retry, no cross-provider fallback
│   ├── json_utils.py              # response parsing, one variant per use case (they genuinely differ — see below)
│   ├── models.py                   # response-shape models, including Gemini's exact get_genai_schema()
│   ├── judge.py / judge_models.py  # the auto-judge call (the one place LangChain is used)
├── prompts/
│   ├── record_evaluator/{v1,v2}.txt + judge_criteria.yaml
│   ├── report_evaluator/{v1,v2}/{pillar_eval,executive_summary}/... + judge_criteria.yaml
│   ├── attempt_comparator/      # no per-version split — text is byte-identical between v1 and v2
│   │   ├── title_generator.txt, main_prompt.txt, system_message.txt
│   │   ├── terminology_rule_{with,without}_titles.txt
│   │   └── judge_criteria.yaml
│   └── pillar_summarizer/
│       ├── {v1,v2}/overall_{ar,en}.txt
│       ├── {v1,v2}/retry_{expand,condense}_{ar,en}.txt
│       ├── {v1,v2}/final_retry_{expand,reduce}_{ar,en}.txt
│       ├── {v1,v2}/final_retry_operations.json   # the 6 tiered edit instructions per language
│       └── judge_criteria.yaml
└── schemas/
    └── SCHEMAS.md             # THE fidelity reference: exact request/response schemas and per-provider
                                call params for every use case and version, verified against source
```

## Try it

```bash
pip install -r requirements.txt
python test_engine.py            # dry run: validates prompt assembly + input adaptation, no API keys needed
cp .env.example .env              # then fill in real keys
python test_engine.py --live      # fires real calls per configured provider, all four use cases
streamlit run streamlit_app.py    # the Compare UI
```

Without any `.env` keys configured, the Compare page still works for exploring the form, loading samples, and picking (version, model) combinations — "Run comparison" shows a clear per-side "not configured" error instead of crashing.

## Design notes

### Prompt text is never hand-transcribed

Pillar Summarizer's 20 templates were extracted by calling production's own `create_*_prompt()` functions with sentinel values and reverse-substituting them back into `{placeholder}` form, then asserting every stored template re-renders byte-for-byte identical to production's output across both languages, both retry branches, and all six tiers.

That extractor — plus differential tests for the response parser and the retry loop — lives in [`tools/pillar_summarizer_fidelity/`](tools/pillar_summarizer_fidelity/). It needs the Medals-AI git repo but no API keys:

```bash
cd tools/pillar_summarizer_fidelity && python extract.py && python verify_parser.py && python verify_loop.py
```

Re-run after any upstream prompt change; a clean `diff -r out ../../prompts/pillar_summarizer` means the stored templates are still exact. The same principle applies elsewhere: where a prompt *is* hand-transcribed, `schemas/SCHEMAS.md` records the provenance.

### Response parsing differs per use case, on purpose

There are three separate parsers in `runner/json_utils.py`, because production has three:
- **Record / Report Evaluator** — strip a markdown fence, `json.loads`, validate.
- **Attempt Comparator** — same, but `record_id` is popped out *before* validation and trusted as-is.
- **Pillar Summarizer** — no fence handling at all. Strips `*`/`_`/emoji from the report body, falls back to slicing between the first `{` and last `}`, injects `language: "ar"` when missing, and (v2 only) renames an alternate summary key.

Sharing one parser would quietly change which malformed responses survive — which is exactly the signal being measured.

### Why Report Evaluator's prompts are split into fragments

The real code assembles the pillar-evaluation prompt from three pieces at runtime — `main` body + a conditional Learning & Development block (only appended when `pillar_name` matches "تعلم و تطور") + a shared tail — plus a separate executive-summary prompt. Storing them the same way preserves that conditional-assembly behavior faithfully, instead of risking a flattened, always-includes-everything version that doesn't match what production sends for a non-L&D pillar.

### Why Pillar Summarizer has an attempt-trajectory view

It is the only use case whose retry loop is driven by the *content* of the previous response rather than by whether it parsed. Each of up to three attempts sends a different prompt, selected by how far the best-so-far word count missed. A prompt version that lands in range on attempt 1 is meaningfully better than one that only gets there after the mechanical final-retry edit, even when both end up acceptable — and that difference is invisible in the output text alone.

### Claude is implemented but not offered

`engine.py` implements every Claude call path exactly as production does, and `schemas/SCHEMAS.md` documents them — but `claude_sonnet` is not in any use case's model dropdown. v2 dropped Claude from all four use cases, and v1's Pillar Summarizer Claude path is broken upstream (it omits the SDK-required `max_tokens`, so it raises before making a request). `test_engine.py --live` still exercises the working Claude paths if an `ANTHROPIC_API_KEY` is present.

### Known-failing combinations are flagged, not hidden

Prompt Lab deliberately allows (version, model) pairings production never shipped. A few of those reliably fail — most notably **v1 + any Core42 model on Pillar Summarizer**, where Core42 returns the report under `report` instead of `summary` and v1 has no rename fallback. These get a warning before the run and an explanatory error after, rather than being removed: the failure is the finding. See `_KNOWN_FAILING_COMBOS` in `app_pages/compare.py`.

### What is deliberately NOT replicated

Production's cross-provider fallback and its `ThreadPoolExecutor` pillar concurrency. Silently substituting a different model on failure would confound "which prompt is better" with "which model happened to answer"; concurrency changes nothing about the prompt or the per-pillar result. Same-model retry *is* replicated, since that's a real per-prompt reliability signal. See `PROMPT_LAB_PLAN.md` §9 and `PHASE2_PLAN.md` §0.

## Read `schemas/SCHEMAS.md` before changing the engine

It documents the exact per-provider call parameters (temperature, JSON-mode mechanism, token limits, truncation handling, retry sequence, system messages) verified directly against the real `core/*.py` files — not the READMEs, several of which are stale. It also records the known production bugs this tool reproduces rather than fixes, and the one deliberate divergence.
