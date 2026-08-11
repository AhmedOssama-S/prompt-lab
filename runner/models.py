"""
Response-shape models, mirrored field-for-field from the real Medals-AI
core/models.py files (azure-functions/ and azure-functions-unified/).
Used only to validate that a candidate/judge call actually produced a
conforming response -- not copied from Medals-AI, just kept in lockstep
with it so a parse failure here means the same thing it would in production.
"""

from typing import Dict, List
from pydantic import BaseModel, Field


# ---- Record Evaluator (identical response shape on v1 and v2) ----

class AnalysisSection(BaseModel):
    strengths: List[str]
    areas_for_improvement: List[str]
    recommendations: List[str]


class RubricEvaluationResponse(BaseModel):
    arabic_analysis: AnalysisSection
    english_analysis: AnalysisSection


# ---- Report Evaluator: per-pillar criterion response (identical shape on v1 and v2) ----

class CriterionEvaluationResponse(BaseModel):
    achieved_percentage: float
    performance_level: str = Field(..., description='one of "80-100%", "55-75%", "30-50%", "25%-5%"')
    rationale: str
    strengths: List[str]
    improvements: List[str]
    language: str = "ar"

    @classmethod
    def get_genai_schema(cls) -> dict:
        """
        Verbatim port of the real get_genai_schema() from core/models.py
        (identical on v1 and v2) -- Gemini's structured-output config takes
        an OpenAPI-subset schema, NOT a full JSON Schema, so Pydantic's own
        model_json_schema() must NOT be substituted here: it emits $defs/
        title/additionalProperties that Gemini's schema parser doesn't
        expect, and would change what gets enforced at the API level.
        """
        return {
            "type": "object",
            "properties": {
                "achieved_percentage": {
                    "type": "number",
                    "description": "Exact percentage achieved (e.g., 68.5)"
                },
                "performance_level": {
                    "type": "string",
                    "description": "Performance level band: 80-100%, 55-75%, 30-50%, or 25%-5%",
                    "enum": ["80-100%", "55-75%", "30-50%", "25%-5%"]
                },
                "rationale": {
                    "type": "string",
                    "description": "Brief explanation (2-3 lines) for the assigned percentage"
                },
                "strengths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-5 paragraphs explaining strengths"
                },
                "improvements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1-4 paragraphs explaining improvement opportunities, or 1 positive reinforcement statement if performance is exemplary"
                },
                "language": {
                    "type": "string",
                    "description": "Language of the response content: 'ar' for Arabic or 'en' for English",
                    "enum": ["ar", "en"]
                }
            },
            "required": ["achieved_percentage", "performance_level", "rationale", "strengths", "improvements", "language"]
        }


# ---- Attempt Comparator (identical response shape on v1 and v2) ----

class ComparisonAnalysisSection(BaseModel):
    individual_analysis: str
    comparative_analysis: str
    final_ranking: str
    best_practices: str


class ComparisonResponse(BaseModel):
    """
    record_id is deliberately NOT a field here -- production pops it out of
    the raw JSON dict BEFORE validating the rest against this shape (see
    core/utils.py's validate_and_parse_json for this use case, identical on
    v1 and v2: `record_id = data.pop('record_id', None)`), and never
    validates it against the achievement ids that were actually sent in.
    See runner/json_utils.py::validate_and_parse_comparison_json.
    """
    arabic_analysis: ComparisonAnalysisSection
    english_analysis: ComparisonAnalysisSection


class AchievementTitle(BaseModel):
    ar: str
    en: str


TitlesByIndex = Dict[int, AchievementTitle]


# ---- Pillar Summarizer (identical response shape on v1 and v2) ----

class SummaryResponse(BaseModel):
    """
    The per-attempt AI response. Note `language` defaults to "ar" rather than
    being required: production's validate_and_parse_summary_json injects
    data['language'] = 'ar' when the key is missing, BEFORE validating, so a
    model that omits it is silently treated as Arabic rather than failing.
    Replicated in runner/json_utils.py::validate_and_parse_summary_json.
    """
    summary: str
    language: str = "ar"

    @classmethod
    def get_genai_schema(cls) -> dict:
        """Verbatim port of core/models.py::SummaryResponse.get_genai_schema()
        (identical on v1 and v2). See CriterionEvaluationResponse above for why
        Pydantic's model_json_schema() must not be substituted here."""
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Generated formal government report"
                },
                "language": {
                    "type": "string",
                    "description": "Language of the response: 'ar' for Arabic or 'en' for English",
                    "enum": ["ar", "en"]
                }
            },
            "required": ["summary", "language"]
        }


class SummaryAttempt(BaseModel):
    """One pass through the retry loop, captured for the trajectory view.

    Has no production counterpart as a model -- production keeps these as bare
    dicts internally and only surfaces the final result (plus, in v2 only, a
    flat list of per-attempt word counts). Prompt Lab keeps the full record
    because *how* a prompt converges is the thing being compared.
    """
    attempt: int
    prompt_stage: str = Field(..., description='"overall" | "retry" | "final_retry"')
    branch: str | None = Field(None, description='"expand"/"condense" for retry, "expand"/"reduce" for final_retry')
    tier: str | None = Field(None, description='final_retry only: which operation tier fired, e.g. "le40"')
    word_count: int
    distance: int = Field(..., description="abs(word_count - target_words)")
    in_range: bool
    language: str
    summary: str
    latency_ms: float
    prompt: str = Field(..., description="the exact rendered prompt sent for this attempt")
    raw_text: str = Field(..., description="the provider's unparsed response")
    remapped_from: str | None = Field(
        None,
        description='v2 only: the wrong summary key the model used, if code had to rename it (e.g. "report")',
    )


class PillarSummaryResult(BaseModel):
    """Final outcome of the retry loop, mirroring production's result dict
    (summary / word_count / num_rows / attempts / detected_language) plus the
    trajectory and an explicit record of how the loop terminated."""
    summary: str
    word_count: int
    num_rows: int
    attempts: int
    detected_language: str
    trajectory: List[SummaryAttempt]
    outcome: str = Field(..., description='"in_range" (early exit) | "best_effort_not_too_long" | "best_effort_closest"')
    returned_attempt: int = Field(..., description="which attempt number's text was ultimately returned")
    target_words: int
    min_acceptable: int
    max_acceptable: int
    swallowed_errors: List[str] = Field(
        default_factory=list,
        description="Errors on attempts 2+ that production silently absorbs (it only re-raises when "
                    "no attempt has succeeded yet). Surfaced here because a prompt version that "
                    "intermittently fails to parse is worse than one that never does, and production's "
                    "own logs are the only place that would otherwise show it.",
    )
