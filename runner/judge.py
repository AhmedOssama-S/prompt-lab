"""
Auto-judge: automatically scores Side A and Side B outputs against the
use case's own criteria (extracted from the prompts' own rule checklists --
see prompts/*/judge_criteria.yaml) and produces a winner + recommendation.

Runs inline as part of Compare, not a separate page. No production
equivalent to mirror here -- this is a new capability -- so, unlike
runner/engine.py's candidate-call path, there's no fidelity constraint
requiring an exact parameter match; reasonable judge-specific defaults
(lower temperature for consistency) are used instead.

Design choice: single-pass judging, not a double-order-swap. Swapping
A/B order and re-running would catch position bias but doubles latency
and cost on every single "Run comparison" click -- since this needs to
feel instant/automatic, that tradeoff isn't worth it here. Worth revisiting
if judge verdicts start looking position-biased in practice.
"""

import json
from pathlib import Path
from typing import Any, Dict

import yaml

from .json_utils import validate_and_parse_json
from .judge_models import JudgeVerdict

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Preference order for the judge model: Core42 GPT-5.1 first (per
# PROMPT_LAB_PLAN.md's original decision), falling back only if that's not
# configured in this environment.
JUDGE_MODEL_PRIORITY = ["core42_gpt-5.1", "core42_fallback", "core42_gpt-4.1", "gemini_pro", "gemini_flash"]


class NoJudgeModelAvailable(Exception):
    pass


def load_judge_criteria(use_case: str) -> Dict[str, str]:
    path = PROMPTS_DIR / use_case / "judge_criteria.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: entry["description"].strip() for name, entry in data.items()}


def _pick_judge_model(clients: Dict[str, Any]) -> str:
    for key in JUDGE_MODEL_PRIORITY:
        if key in clients:
            return key
    raise NoJudgeModelAvailable(
        "No judge model available -- configure at least one Core42 or Gemini API key"
    )


def build_judge_prompt(context: str, output_a: dict, output_b: dict, criteria: Dict[str, str]) -> str:
    criteria_text = "\n".join(f"- {name}: {desc}" for name, desc in criteria.items())
    return f"""You are an impartial expert judge evaluating two AI-generated outputs (A and B) produced for the same input, for a government excellence-award evaluation system. Score each output against the criteria below, then give an overall recommendation.

ORIGINAL INPUT CONTEXT:
{context}

CRITERIA (score each output 1-10 on each; higher = better adherence to the criterion):
{criteria_text}

OUTPUT A:
{json.dumps(output_a, ensure_ascii=False, indent=2)}

OUTPUT B:
{json.dumps(output_b, ensure_ascii=False, indent=2)}

Return ONLY a valid JSON object with this exact structure:
{{
  "criterion_scores": [
    {{"criterion": "<criterion name, exactly as given above>", "score_a": <integer 1-10>, "score_b": <integer 1-10>, "notes": "<1-2 sentence comparison for this specific criterion>"}}
  ],
  "overall_score_a": <number 0-100>,
  "overall_score_b": <number 0-100>,
  "winner": "<exactly one of: A, B, tie>",
  "recommendation": "<2-4 sentence recommendation explaining which output is better and why, referencing specific criteria and specific content from the outputs>"
}}

Score strictly and specifically -- ground every score in the criterion's actual description and in concrete details from each output, not a generic impression. If both outputs are equally good on a criterion, give them equal scores. Do not let output length alone influence scores. Include one criterion_scores entry per criterion listed above, in the same order.
"""


def run_judge(use_case: str, context: str, output_a: dict, output_b: dict, clients: Dict[str, Any]) -> tuple[JudgeVerdict, str]:
    """Returns (verdict, model_key_used)."""
    model_key = _pick_judge_model(clients)
    criteria = load_judge_criteria(use_case)
    prompt = build_judge_prompt(context, output_a, output_b, criteria)
    client_info = clients[model_key]

    if model_key.startswith("core42_"):
        from .engine import CORE42_TOKEN_PARAM

        token_param = CORE42_TOKEN_PARAM.get(model_key, "max_tokens")
        kwargs = {
            "model": client_info["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        kwargs[token_param] = 4096
        response = client_info["client"].chat.completions.create(**kwargs)
        raw = response.choices[0].message.content or ""
    else:  # gemini
        response = client_info["client"].models.generate_content(
            model=client_info["model"],
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0.2},
        )
        raw = response.text

    verdict = validate_and_parse_json(raw, JudgeVerdict)
    return verdict, model_key
