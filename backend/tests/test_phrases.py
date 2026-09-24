from services import phrases
from services.phrases import PHRASE_HISTORY_SIZE, PHRASES, matches_phrase, next_verify_phrase


def test_exact_match():
    assert matches_phrase("Hôm nay trời đẹp.", "hôm nay trời đẹp")


def test_partial_match_at_threshold_passes():
    # 3 of 5 expected words present == 0.6, the ">=" threshold boundary
    assert matches_phrase("Tôi thích uống cà phê", "tôi thích uống trà nóng")


def test_below_threshold_fails():
    assert not matches_phrase("Tôi thích uống cà phê buổi sáng", "trà nóng")


def test_empty_transcript_is_rejected():
    # Regression test: transcript is meant to come from server-side STT
    # (services/stt.py) run on the real audio, never from a
    # client-supplied field. An empty result (e.g. STT couldn't recognize
    # any speech) must NOT be treated as an automatic pass -- that early
    # return was exactly the bypass this replaced.
    assert not matches_phrase("Hôm nay trời đẹp.", "")


def test_punctuation_and_case_are_ignored():
    assert matches_phrase("Bạn CÓ, khỏe không?", "bạn có khỏe không")


def test_empty_expected_phrase_always_matches():
    assert matches_phrase("", "bất kỳ điều gì")


def _force_earliest_candidate(monkeypatch):
    # next_verify_phrase() filters PHRASES down to candidates and calls
    # random.choice() on them; forcing it to always take the first candidate
    # (preserving PHRASES' original order) makes phrase selection deterministic,
    # so the points below are exact call counts instead of something that only
    # shows up with the right random luck.
    monkeypatch.setattr(phrases.random, "choice", lambda seq: seq[0])


async def test_no_phrase_is_issued_twice_until_the_whole_pool_has_been_used(monkeypatch, fake_redis):
    """Regression test for the replay window. A recording of an earlier challenge
    is genuine speech, so speaker match and AASIST cannot reject it; only a
    different challenge sentence stops it. The first version excluded just the
    last PHRASE_HISTORY_SIZE phrases, and the very first phrase came back on the
    12th call. Now nothing repeats until every phrase has been issued once."""
    monkeypatch.setattr(phrases, "get_redis", lambda: fake_redis)
    _force_earliest_candidate(monkeypatch)

    issued = [await next_verify_phrase("user-1") for _ in range(len(PHRASES))]

    assert len(set(issued)) == len(PHRASES)   # every phrase exactly once


async def test_a_new_cycle_does_not_bring_back_the_most_recent_phrases(monkeypatch, fake_redis):
    monkeypatch.setattr(phrases, "get_redis", lambda: fake_redis)
    _force_earliest_candidate(monkeypatch)
    issued = [await next_verify_phrase("user-1") for _ in range(len(PHRASES))]

    after_reset = await next_verify_phrase("user-1")   # the pool is exhausted: a new cycle starts

    assert after_reset not in issued[-PHRASE_HISTORY_SIZE:]


async def test_the_used_record_is_kept_per_user(monkeypatch, fake_redis):
    monkeypatch.setattr(phrases, "get_redis", lambda: fake_redis)
    _force_earliest_candidate(monkeypatch)

    first_for_alice = await next_verify_phrase("alice")
    await next_verify_phrase("alice")
    first_for_bob = await next_verify_phrase("bob")

    assert first_for_bob == first_for_alice   # alice's history does not shrink bob's pool


def test_history_window_is_smaller_than_the_phrase_pool():
    # If this ever stopped holding (pool shrunk to <= the history window),
    # another_phrase()'s fallback (random.choice(PHRASES) when candidates is
    # empty) would kick in and could reissue a phrase still inside the
    # exclusion window on the very next call, silently defeating the point
    # of tracking history at all.
    assert len(PHRASES) > PHRASE_HISTORY_SIZE
