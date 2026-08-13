"""Response shape for the auto-judge verdict (no production equivalent to mirror -- this is new)."""

from typing import List, Optional

from pydantic import BaseModel


class CriterionScore(BaseModel):
    criterion: str
    score_a: int  # 1-10
    score_b: int  # 1-10
    notes: str
    # Verbatim excerpts from each side's own output backing up the score --
    # None when a side has nothing specific to point to (e.g. a clean 9/10).
    # judge.py checks these against the actual output text; render_helpers.py
    # shows the result, since a quote the judge merely claims exists is not
    # the same as one that verifiably does.
    quote_a: Optional[str] = None
    quote_b: Optional[str] = None


class JudgeVerdict(BaseModel):
    criterion_scores: List[CriterionScore]
    overall_score_a: float  # 0-100
    overall_score_b: float  # 0-100
    winner: str  # "A" | "B" | "tie"
    recommendation: str
