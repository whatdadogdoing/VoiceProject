from scripts.evaluate_thresholds import build_report


def _rows(group, pairs):
    return [{"group": group, "file": f"{group}_{i}.wav", "voiceprint_score": vp, "spoof_score": sp}
            for i, (vp, sp) in enumerate(pairs)]


SCORES = (
    # (speaker match, spoof score). Deployed bars: match >= 0.7, spoof < 0.5.
    _rows("genuine", [(0.90, 0.05), (0.85, 0.05), (0.80, 0.05), (0.60, 0.05)])
    + _rows("impostors", [(0.40, 0.10), (0.72, 0.10)])
    + _rows("clones", [(0.80, 0.90), (0.90, 0.30), (0.75, 0.80)])
)


def _report():
    return build_report(SCORES, ["enroll_1.wav", "enroll_2.wav", "enroll_3.wav"], 3)


def _line(report, prefix):
    return next(line for line in report.splitlines() if line.startswith(prefix))


def test_end_to_end_rates_match_hand_computation():
    report = _report()
    # genuine: 0.9, 0.85, 0.8 pass; 0.6 fails the match bar -> 3/4
    assert "75.0%" in _line(report, "| genuine (want high)")
    # impostors: only the 0.72 one clears the match bar -> 1/2
    assert "50.0%" in _line(report, "| impostors (want 0)")
    # clones: (0.8, 0.9) and (0.75, 0.8) are caught by the spoof score; (0.9, 0.3) slips through -> 1/3
    assert "33.3%" in _line(report, "| clones (want 0)")


def test_deployed_thresholds_are_marked_in_both_tables():
    report = _report()
    assert "0.70  <- deployed" in report
    assert "0.5  <- deployed" in report


def test_small_sample_warning_names_the_groups():
    report = _report()
    assert "WARNING: fewer than 20 samples" in report
    assert "genuine" in _line(report, "WARNING") and "clones" in _line(report, "WARNING")


def test_report_omits_sections_it_has_no_data_for():
    report = build_report(_rows("genuine", [(0.9, 0.05)]), ["a.wav", "b.wav", "c.wav"], 3)
    assert "## Speaker verification" not in report
    assert "## Anti-spoofing" not in report
    assert "## End to end" in report
