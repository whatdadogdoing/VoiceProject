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
    # another_phrase() filters PHRASES down to candidates and calls
    # random.choice() on them; forcing it to always take the first candidate
    # (preserving PHRASES' original order) makes phrase selection
    # deterministic, so the reuse point demonstrated below is an exact call
    # count instead of something that only shows up with the right random luck.
    monkeypatch.setattr(phrases.random, "choice", lambda seq: seq[0])


async def test_phrase_history_prevents_repeats_within_the_window(monkeypatch, fake_redis):
    monkeypatch.setattr(phrases, "get_redis", lambda: fake_redis)
    _force_earliest_candidate(monkeypatch)

    issued = [await next_verify_phrase("user-1") for _ in range(PHRASE_HISTORY_SIZE + 1)]

    assert len(set(issued)) == len(issued)


async def test_phrase_reappears_once_it_ages_out_of_the_history_window(monkeypatch, fake_redis):
    """Demonstrates the actual limit of replay protection: next_verify_phrase
    only excludes the last PHRASE_HISTORY_SIZE (10) challenges out of a fixed
    pool of PHRASES (102). A phrase is not retired for good -- it becomes a
    valid challenge again as soon as it ages out of that window. This matters
    for a stored-audio replay attack: if an attacker already has a genuine
    recording of the victim saying a phrase that resurfaces, that recording
    passes speaker match, AASIST (it's real, unmodified speech, not synthetic)
    and the phrase check together -- freshness is the only layer that was
    ever defending against it, and this is its actual limit, not the 102-size
    pool alone."""
    monkeypatch.setattr(phrases, "get_redis", lambda: fake_redis)
    _force_earliest_candidate(monkeypatch)

    first_phrase = await next_verify_phrase("user-1")
    for _ in range(PHRASE_HISTORY_SIZE):
        await next_verify_phrase("user-1")  # ages first_phrase out of the window

    assert await next_verify_phrase("user-1") == first_phrase


def test_history_window_is_smaller_than_the_phrase_pool():
    # If this ever stopped holding (pool shrunk to <= the history window),
    # another_phrase()'s fallback (random.choice(PHRASES) when candidates is
    # empty) would kick in and could reissue a phrase still inside the
    # exclusion window on the very next call, silently defeating the point
    # of tracking history at all.
    assert len(PHRASES) > PHRASE_HISTORY_SIZE
