import math

import pytest

from scripts.eval_metrics import (
    describe, equal_error_rate, error_rates, pipeline_acceptance, threshold_table, wilson_interval,
)


def test_wilson_interval_with_no_samples_is_uninformative():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_matches_known_value():
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)


def test_wilson_interval_stays_in_bounds_at_the_extremes():
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.2 < high < 0.35
    low, high = wilson_interval(10, 10)
    assert high == 1.0 and 0.65 < low < 0.8


def test_error_rates_for_a_speaker_match_score():
    genuine = [0.9, 0.8, 0.6]
    attacks = [0.5, 0.72]
    point = error_rates(genuine, attacks, 0.7, accept_if_ge=True)
    assert (point.genuine_rejected, point.genuine_total) == (1, 3)   # 0.6 is below 0.7
    assert (point.attack_accepted, point.attack_total) == (1, 2)     # 0.72 slips through
    assert point.frr == pytest.approx(1 / 3)
    assert point.far == pytest.approx(1 / 2)


def test_error_rates_for_a_spoof_score_are_direction_reversed():
    # spoof score: LOW is good. A sample is accepted only when score < threshold.
    bona_fide = [0.05, 0.1, 0.6]
    clones = [0.9, 0.3]
    point = error_rates(bona_fide, clones, 0.5, accept_if_ge=False)
    assert point.genuine_rejected == 1   # the 0.6 bona fide sample is flagged as fake
    assert point.attack_accepted == 1    # the 0.3 clone is not caught


def test_threshold_table_has_one_row_per_threshold():
    rows = threshold_table([0.9], [0.1], [0.5, 0.7, 0.95], accept_if_ge=True)
    assert [r.threshold for r in rows] == [0.5, 0.7, 0.95]
    assert [r.genuine_rejected for r in rows] == [0, 0, 1]


def test_eer_is_zero_when_scores_separate_perfectly():
    eer, _ = equal_error_rate([0.8, 0.9], [0.1, 0.2], accept_if_ge=True)
    assert eer == 0.0


def test_eer_for_overlapping_speaker_scores():
    genuine = [0.6, 0.7, 0.8, 0.9]
    attacks = [0.1, 0.2, 0.3, 0.65]
    eer, threshold = equal_error_rate(genuine, attacks, accept_if_ge=True)
    assert eer == pytest.approx(0.25)
    assert threshold == pytest.approx(0.65)


def test_eer_for_spoof_scores_uses_the_reversed_direction():
    bona_fide = [0.05, 0.1, 0.15, 0.55]
    clones = [0.45, 0.8, 0.9, 0.95]
    eer, threshold = equal_error_rate(bona_fide, clones, accept_if_ge=False)
    assert eer == pytest.approx(0.25)
    assert 0.45 < threshold <= 0.55


def test_eer_needs_both_groups():
    with pytest.raises(ValueError):
        equal_error_rate([0.9], [], accept_if_ge=True)


def test_pipeline_acceptance_combines_both_gates():
    records = [
        (0.90, 0.10),  # accepted
        (0.90, 0.60),  # rejected: looks synthetic
        (0.60, 0.10),  # rejected: doesn't sound like the enrolled speaker
        (0.70, 0.49),  # accepted: exactly on the match bar, just under the spoof bar
    ]
    assert pipeline_acceptance(records, spoof_threshold=0.5, match_threshold=0.7) == (2, 4)


def test_describe_reports_median_for_odd_and_even_counts():
    assert describe([0.3, 0.1, 0.2])["median"] == pytest.approx(0.2)
    assert describe([0.1, 0.2, 0.3, 0.4])["median"] == pytest.approx(0.25)
    assert describe([]) == {"n": 0}
    assert not math.isnan(describe([0.5])["min"])
