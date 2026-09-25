"""A random number the user reads aloud after the challenge phrase (off unless VERIFY_CODE_ENABLED).

A recording of an earlier login is genuine speech, so speaker match and anti-spoofing cannot reject
it; only something fresh can. The phrase pool is finite and a phrase eventually recurs, but a code
drawn per attempt from 10,000 never does. Measured on 20 recordings, PhoWhisper read the code back
exactly 19 times (12 of 12 read digit by digit), so it is checked in the fast recognizer.
"""
import os
import re
import secrets

CODE_LENGTH = 4

# Only the ten digits: the code is read digit by digit ("không chín sáu bảy"), and the numeric
# tens words ("mười", "mươi") are not accepted on purpose.
_DIGIT_WORDS = {
    "không": "0", "một": "1", "hai": "2", "ba": "3", "bốn": "4",
    "năm": "5", "sáu": "6", "bảy": "7", "bẩy": "7", "tám": "8", "chín": "9",
}
_PUNCTUATION = re.compile(r"[.,!?;:\"'\-]")


def enabled() -> bool:
    return os.getenv("VERIFY_CODE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def new_code() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(CODE_LENGTH))


def _digits_of(token: str) -> str | None:
    if token.isdigit():
        return token
    return _DIGIT_WORDS.get(token)


def split_code(transcript: str, length: int = CODE_LENGTH) -> tuple[str, str]:
    """(the words before the code, the last `length` digits heard at the end).

    Recognizers write the same reading as "4729", "4 7 2 9", "4-7-2-9" or "bốn bảy hai chín", and a
    phrase can itself end in a digit word ("...được không"), so the code is the last `length` digits
    only: the walk from the end stops once it has that many.
    """
    tokens = _PUNCTUATION.sub(" ", transcript.lower()).split()
    digits, start = "", len(tokens)
    while start > 0 and len(digits) < length:
        found = _digits_of(tokens[start - 1])
        if found is None:
            break
        digits = found + digits
        start -= 1
    return " ".join(tokens[:start]), digits[-length:] if digits else ""
