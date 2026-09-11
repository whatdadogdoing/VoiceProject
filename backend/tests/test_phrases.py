from services.phrases import matches_phrase


def test_exact_match():
    assert matches_phrase("Hôm nay trời đẹp.", "hôm nay trời đẹp")


def test_partial_match_at_threshold_passes():
    # 3 of 5 expected words present == 0.6, the ">=" threshold boundary
    assert matches_phrase("Tôi thích uống cà phê", "tôi thích uống trà nóng")


def test_below_threshold_fails():
    assert not matches_phrase("Tôi thích uống cà phê buổi sáng", "trà nóng")


def test_empty_transcript_is_rejected():
    # Regression test: transcript is meant to come from server-side STT
    # (services/stt.transcribe) run on the real audio, never from a
    # client-supplied field. An empty result (e.g. STT couldn't recognize
    # any speech) must NOT be treated as an automatic pass -- that early
    # return was exactly the bypass this replaced.
    assert not matches_phrase("Hôm nay trời đẹp.", "")


def test_punctuation_and_case_are_ignored():
    assert matches_phrase("Bạn CÓ, khỏe không?", "bạn có khỏe không")


def test_empty_expected_phrase_always_matches():
    assert matches_phrase("", "bất kỳ điều gì")
