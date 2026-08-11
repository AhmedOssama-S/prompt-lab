"""Response shape for the auto-judge verdict (no production equivalent to mirror -- this is new)."""

from typing import List

from pydantic import BaseModel


class CriterionScore(BaseModel):
    criterion: str
    score_a: int  # 1-10
    score_b: int  # 1-10
    notes: str


class JudgeVerdict(BaseModel):
    criterion_scores: List[CriterionScore]
    overall_score_a: float  # 0-100
    overall_score_b: float  # 0-100
    winner: str  # "A" | "B" | "tie"
    recommendation: str
