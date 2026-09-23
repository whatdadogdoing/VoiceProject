"""Pure metric helpers for scripts/evaluate_thresholds.py: error-rate tables,
equal error rate, and confidence intervals. No model or audio dependencies, so
they can be unit-tested on their own.

Terminology used throughout:
  genuine  -- samples that SHOULD be accepted (the real user)
  attacks  -- samples that SHOULD be rejected (impostors, voice clones)
  FRR      -- false rejection rate: fraction of genuine samples rejected
  FAR      -- false acceptance rate: fraction of attack samples accepted
  EER      -- the operating point where FAR == FRR (lower is better)

Score direction differs per model, hence `accept_if_ge`:
  speaker match (cosine similarity): higher = more likely the real speaker,
      a sample is accepted when score >= threshold      -> accept_if_ge=True
  spoof score (AASIST-L):            higher = more likely synthetic,
      a sample is accepted when score <  threshold      -> accept_if_ge=False
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n. Preferred over the
    naive normal interval because it stays sensible for the small sample sizes
    (tens of recordings) and extreme rates (0% or 100%) typical here."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _accepts(score: float, threshold: float, accept_if_ge: bool) -> bool:
    return score >= threshold if accept_if_ge else score < threshold


@dataclass(frozen=True)
class RatePoint:
    threshold: float
    genuine_rejected: int
    genuine_total: int
    attack_accepted: int
    attack_total: int

    @property
    def frr(self) -> float:
        return self.genuine_rejected / self.genuine_total if self.genuine_total else float("nan")

    @property
    def far(self) -> float:
        return self.attack_accepted / self.attack_total if self.attack_total else float("nan")


def error_rates(genuine: Sequence[float], attacks: Sequence[float], threshold: float,
                accept_if_ge: bool) -> RatePoint:
    genuine_rejected = sum(1 for s in genuine if not _accepts(s, threshold, accept_if_ge))
    attack_accepted = sum(1 for s in attacks if _accepts(s, threshold, accept_if_ge))
    return RatePoint(threshold, genuine_rejected, len(genuine), attack_accepted, len(attacks))


def threshold_table(genuine: Sequence[float], attacks: Sequence[float], thresholds: Iterable[float],
                    accept_if_ge: bool) -> list[RatePoint]:
    return [error_rates(genuine, attacks, t, accept_if_ge) for t in thresholds]


def equal_error_rate(genuine: Sequence[float], attacks: Sequence[float],
                     accept_if_ge: bool) -> tuple[float, float]:
    """Returns (eer, threshold). Sweeps every observed score (plus one value
    beyond each end, so FAR/FRR can reach 0 and 1) and picks the threshold
    where FAR and FRR are closest; the EER is their mean there."""
    if not genuine or not attacks:
        raise ValueError("need at least one genuine and one attack sample")
    observed = sorted(set(genuine) | set(attacks))
    candidates = [observed[0] - 1e-9, *observed, observed[-1] + 1e-9]

    best_key: tuple[float, float] | None = None  # (|FAR-FRR|, worse of the two)
    best_eer = 1.0
    best_threshold = candidates[0]
    for t in candidates:
        point = error_rates(genuine, attacks, t, accept_if_ge)
        key = (abs(point.far - point.frr), max(point.far, point.frr))
        if best_key is None or key < best_key:
            best_key = key
            best_eer = (point.far + point.frr) / 2
            best_threshold = t
    return best_eer, best_threshold


def pipeline_acceptance(records: Sequence[tuple[float, float]], spoof_threshold: float,
                        match_threshold: float) -> tuple[int, int]:
    """End-to-end decision as /verify makes it, ignoring context and the
    audio-quality/phrase gates: accepted when the spoof score is below its
    threshold AND the speaker-match score reaches its threshold.
    `records` are (voiceprint_score, spoof_score) pairs. Returns (accepted, total)."""
    accepted = sum(1 for vp, sp in records if sp < spoof_threshold and vp >= match_threshold)
    return accepted, len(records)


def describe(values: Sequence[float]) -> dict[str, float]:
    """n / min / median / max, for showing where each group's scores actually sit."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"n": len(ordered), "min": ordered[0], "median": median, "max": ordered[-1]}
