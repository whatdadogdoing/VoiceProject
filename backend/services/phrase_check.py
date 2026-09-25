import logging

from services.phrases import matches_phrase
from services.stt import transcribe_accurate, transcribe_fast
from services.verify_code import split_code

# uvicorn configures this logger at INFO; a module-level logging.getLogger(__name__)
# would silently drop these lines.
logger = logging.getLogger("uvicorn.error")


def _accepts(expected_phrase: str, expected_code: str | None, heard: str) -> bool:
    if expected_code is None:
        return matches_phrase(expected_phrase, heard)
    # The code is read after the phrase, so both must be in the same transcript: the phrase
    # in the words before the code (the length rule applies to those alone), the code exactly.
    said, code = split_code(heard, len(expected_code))
    return code == expected_code and matches_phrase(expected_phrase, said)


def check_phrase(wav_bytes: bytes, expected_phrase: str, expected_code: str | None = None) -> bool:
    """Did the speaker say `expected_phrase` (and then `expected_code`, when there is one),
    judged on the audio the server got?

    The cheap recognizer (PhoWhisper-base, ~3s) goes first. When it agrees, that is the answer.
    When it doesn't, the slower one (Whisper small, ~10s regardless of clip length) gets a second
    listen. On recordings from the app's own browser recorder PhoWhisper confirmed 19 of 19 where
    stock Whisper base, which it replaced, confirmed 14 (and only 2 of 7 real submissions), so the
    slow path (about 14 s in all) is now the exception. The Vosk recognizer before that passed
    almost none. Both are held to the same word-match threshold, so the second opinion only
    rescues misheard genuine readings; a wrong phrase or a wrong code still fails both.

    What each recognizer heard is logged whenever the first one rejects, because
    without that a rejection can't be diagnosed after the fact.
    """
    fast = transcribe_fast(wav_bytes)
    if _accepts(expected_phrase, expected_code, fast):
        return True

    try:
        accurate = transcribe_accurate(wav_bytes)
    except Exception:
        # A missing or broken second recognizer must degrade to "the fast one said no",
        # not turn into a 500 on every wrong-phrase attempt.
        logger.exception("second-opinion STT failed; keeping the fast recognizer's rejection")
        return False

    accepted = _accepts(expected_phrase, expected_code, accurate)
    logger.info(
        "phrase check: fast STT rejected; expected=%r fast=%r accurate=%r accepted=%s",
        expected_phrase, fast, accurate, accepted,
    )
    return accepted
