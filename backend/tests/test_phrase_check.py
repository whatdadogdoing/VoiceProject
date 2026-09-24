"""Tests for services/phrase_check.check_phrase: the cheap recognizer first, the
slower one only as a second opinion when the first rejects.

The transcripts below are real: they are what two recognizers produced for a
genuine user reading "Bạn nên đội mũ bảo hiểm khi đi xe máy." through the
browser recorder. The first (a Vosk model, since replaced) got the first half
wrong ("bạn nên đội mũ bảo hiểm" -> "và rồi bỏ diễn", 4 of 9 words) and failed the
0.6 bar; Whisper small heard 7 of 9 and passed. The fixtures keep those strings
as the "cheap recognizer misheard, careful recognizer got it" case.

services.stt is stubbed so no speech model is loaded; the real check_phrase and
the real matches_phrase run unmodified.
"""
import importlib
import logging
import sys
import types

import pytest

PHRASE = "Bạn nên đội mũ bảo hiểm khi đi xe máy."
FAST_MISHEARD = "và rồi bỏ diễn khi đi xe máy"
ACCURATE_HEARD = "Bạn nên đổi mú bảo hiểm khi đi xe máy"
OTHER_PHRASE_HEARD = "Hôm nay trời nắng đẹp và gió mát"


@pytest.fixture
def check(monkeypatch):
    stt = types.SimpleNamespace(transcribe_fast=lambda wav: "", transcribe_accurate=lambda wav: "")
    monkeypatch.setitem(sys.modules, "services.stt", stt)
    monkeypatch.delitem(sys.modules, "services.phrase_check", raising=False)
    module = importlib.import_module("services.phrase_check")

    calls = types.SimpleNamespace(fast=0, accurate=0)

    def use(fast, accurate):
        def fast_stt(wav):
            calls.fast += 1
            return fast

        def accurate_stt(wav):
            calls.accurate += 1
            if isinstance(accurate, Exception):
                raise accurate
            return accurate

        monkeypatch.setattr(module, "transcribe_fast", fast_stt)
        monkeypatch.setattr(module, "transcribe_accurate", accurate_stt)

    yield module, use, calls

    sys.modules.pop("services.phrase_check", None)


def test_fast_recognizer_agreeing_skips_the_slow_one(check):
    module, use, calls = check
    use(fast=PHRASE, accurate=OTHER_PHRASE_HEARD)

    assert module.check_phrase(b"wav", PHRASE) is True
    assert calls.fast == 1
    assert calls.accurate == 0


def test_the_careful_recognizer_rescues_a_genuine_reading_the_fast_one_misheard(check):
    module, use, calls = check
    use(fast=FAST_MISHEARD, accurate=ACCURATE_HEARD)

    assert module.check_phrase(b"wav", PHRASE) is True
    assert calls.accurate == 1


def test_a_different_phrase_is_rejected_by_both_recognizers(check):
    module, use, calls = check
    use(fast=OTHER_PHRASE_HEARD, accurate=OTHER_PHRASE_HEARD)

    assert module.check_phrase(b"wav", PHRASE) is False


def test_threshold_is_not_loosened_for_the_second_recognizer(check):
    # The second recognizer is held to the same 0.6 word-match bar: 4 of 9 words
    # (0.44) fails no matter which recognizer produced it.
    module, use, calls = check
    use(fast=FAST_MISHEARD, accurate=FAST_MISHEARD)

    assert module.check_phrase(b"wav", PHRASE) is False


def test_a_broken_second_recognizer_degrades_to_a_rejection_not_a_crash(check):
    module, use, calls = check
    use(fast=FAST_MISHEARD, accurate=FileNotFoundError("model.bin missing"))

    assert module.check_phrase(b"wav", PHRASE) is False


def test_what_each_recognizer_heard_is_logged_when_the_first_rejects(check, caplog):
    module, use, calls = check
    use(fast=FAST_MISHEARD, accurate=ACCURATE_HEARD)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        module.check_phrase(b"wav", PHRASE)

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert FAST_MISHEARD in logged and ACCURATE_HEARD in logged and "accepted=True" in logged
