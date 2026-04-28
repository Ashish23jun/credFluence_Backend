"""
CIBIL-like trust score engine (30–90 scale).

Score components (when all present):
  Payment behaviour  40 %  — penalty for late/missing payments
  Behaviour ratings  25 %  — avg of communication, professionalism, reliability
  Quality ratings    15 %  — avg of quality, brief_adherence, timeline_adherence
  Evidence boost     10 %  — verified evidence files raise score
  Flags penalty     -10 %  — ghosting / missed deadlines / contract violations

Growth is intentionally slow (+3 max per review).
Penalties are fast (flags hit immediately, weighted heavily).
New score is blended: old * 0.85 + new * 0.15.
Age factor scales from 0 → 1 over first 20 reviews.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

_MIN = 30
_MAX = 90
_DEFAULT = 65
_BLEND_OLD = 0.85
_BLEND_NEW = 0.15
_AGE_FULL = 20          # reviews needed to reach full weight
_MAX_DELTA = 3          # max score change per review (growth cap)

# Penalty points per severity for flags
_FLAG_PENALTY: dict[str, float] = {"low": 2, "medium": 5, "high": 10}

# Weights within the payment component
_PAYMENT_STATUS_SCORE = {"paid": 1.0, "late": 0.4, "pending": 0.6}


@dataclass
class ReviewSignals:
    """All signals from a single review, extracted before calling score_engine."""

    # Ratings: {category: score (1–5)}
    ratings: dict[str, int] = field(default_factory=dict)

    # Payments: list of {"status": "paid"|"late"|"pending"}
    payments: list[dict] = field(default_factory=list)

    # Flags: list of {"type": ..., "severity": "low"|"medium"|"high"}
    flags: list[dict] = field(default_factory=list)

    # Evidence: list of {"verified": bool}
    evidence: list[dict] = field(default_factory=list)


def _payment_component(signals: ReviewSignals) -> float | None:
    """Returns 0–1 representing payment reliability. None if no payments."""
    if not signals.payments:
        return None
    scores = [_PAYMENT_STATUS_SCORE.get(p.get("status", "pending"), 0.6) for p in signals.payments]
    return sum(scores) / len(scores)


def _behaviour_component(signals: ReviewSignals) -> float | None:
    """Avg of communication, professionalism, reliability — normalised 0–1."""
    cats = ("communication", "professionalism", "reliability")
    vals = [signals.ratings[c] for c in cats if c in signals.ratings]
    if not vals:
        return None
    return (sum(vals) / len(vals) - 1) / 4  # 1–5 → 0–1


def _quality_component(signals: ReviewSignals) -> float | None:
    """Avg of quality, brief_adherence, timeline_adherence — normalised 0–1."""
    cats = ("quality", "brief_adherence", "timeline_adherence")
    vals = [signals.ratings[c] for c in cats if c in signals.ratings]
    if not vals:
        return None
    return (sum(vals) / len(vals) - 1) / 4


def _evidence_component(signals: ReviewSignals) -> float | None:
    """Returns 0–1 based on share of verified evidence. None if no evidence."""
    if not signals.evidence:
        return None
    verified = sum(1 for e in signals.evidence if e.get("verified"))
    return verified / len(signals.evidence)


def _flag_penalty(signals: ReviewSignals) -> float:
    """Total penalty points from flags (subtracted from final before clamping)."""
    return sum(_FLAG_PENALTY.get(f.get("severity", "low"), 2) for f in signals.flags)


def _raw_score(signals: ReviewSignals) -> float:
    """Compute a 30–90 score purely from this review's signals."""
    weights = {
        "payment": (0.40, _payment_component(signals)),
        "behaviour": (0.25, _behaviour_component(signals)),
        "quality": (0.15, _quality_component(signals)),
        "evidence": (0.10, _evidence_component(signals)),
    }

    total_weight = 0.0
    weighted_sum = 0.0
    for _name, (w, val) in weights.items():
        if val is not None:
            weighted_sum += w * val
            total_weight += w

    if total_weight == 0:
        return _DEFAULT

    # Normalise to the weight actually present, then map 0–1 → 30–90
    normalised = weighted_sum / total_weight
    score = _MIN + normalised * (_MAX - _MIN)

    # Apply flag penalties (fast, direct deduction)
    score -= _flag_penalty(signals)

    return score


def compute_new_trust_score(
    current_score: int,
    total_reviews: int,
    signals: ReviewSignals,
) -> int:
    """
    Returns the updated trust score (int, clamped 30–90).

    Args:
        current_score:  Existing profile trust_score (30–90).
        total_reviews:  Number of reviews the profile had BEFORE this one.
        signals:        Signals extracted from the new review.
    """
    raw = _raw_score(signals)

    # Age factor: scores from early reviews carry less weight
    age_factor = min(1.0, (total_reviews + 1) / _AGE_FULL)

    # Blend: mostly stick to existing score, nudge toward new signal
    blended = current_score * _BLEND_OLD + raw * _BLEND_NEW * age_factor

    # Growth cap: positive movement is limited to +3 per review
    delta = blended - current_score
    if delta > _MAX_DELTA:
        blended = current_score + _MAX_DELTA

    # Clamp
    result = max(_MIN, min(_MAX, blended))
    return round(result)


def default_trust_score() -> int:
    return _DEFAULT
