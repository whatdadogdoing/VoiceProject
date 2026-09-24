import logging

from services.phrases import matches_phrase
from services.stt import transcribe_accurate, transcribe_fast

# uvicorn configures this logger at INFO; a module-level logging.getLogger(__name__)
# would silently drop these lines.
logger = logging.getLogger("uvicorn.error")


def check_phrase(wav_bytes: bytes, expected_phrase: str) -> bool:
    """Did the speaker say `expected_phrase`, judged on the audio the server got?

    The cheap recognizer (Whisper base, ~3s) goes first. When it agrees, that is
    the answer. When it doesn't, the slower one (Whisper small, ~10s regardless
    of clip length) gets a second listen: on recordings from the app's own
    browser recorder, base passed about 4 in 5 attempts and small nearly all,
    while the Vosk recognizer this replaced passed almost none. Both are held
    to the same word-match threshold, so the second opinion only rescues
    misheard genuine readings; a wrong phrase still fails both.

    What each recognizer heard is logged whenever the first one rejects, because
    without that a wrong-phrase rejection can't be diagnosed after the fact.
    """
    fast = transcribe_fast(wav_bytes)
    if matches_phrase(expected_phrase, fast):
        return True

    try:
        accurate = transcribe_accurate(wav_bytes)
    except Exception:
        # A missing or broken second recognizer must degrade to "Vosk said no",
        # not turn into a 500 on every wrong-phrase attempt.
        logger.exception("second-opinion STT failed; keeping the fast recognizer's rejection")
        return False

    accepted = matches_phrase(expected_phrase, accurate)
    logger.info(
        "phrase check: fast STT rejected; expected=%r fast=%r accurate=%r accepted=%s",
        expected_phrase, fast, accurate, accepted,
    )
    return accepted
