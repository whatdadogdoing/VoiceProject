"""Guards on the content of the challenge-phrase pool (services/phrases.PHRASES).

The phrase is checked by speech-to-text, and English loanwords (podcast, guitar,
email, laptop...) are exactly what it mishears, so a genuine user reading one gets
rejected. These tests keep the pool clean if someone edits it later:

  - every word must have the shape of a plain Vietnamese syllable; a loanword such
    as "podcast" or "guitar" cannot (a syllable ends in a vowel or c, ch, m, n, ng,
    nh, p, t, and there are no "w", "z", "f" or "j");
  - no digits, and a sensible length;
  - it must not end in a number word (other than the very common question ending
    "không"), because a random digit code is meant to be read after the phrase and
    the two would blur together.
"""
import re
import unicodedata

import pytest

from services.phrases import PHRASES

MIN_POOL_SIZE = 200
TONE_MARKS = {"̀", "́", "̃", "̉", "̣"}   # grave, acute, tilde, hook above, dot below
SYLLABLE = re.compile(r"^(ngh|ng|nh|kh|gh|gi|ph|th|tr|ch|qu|[bcdghklmnpqrstvx])?([aeiouy]{1,3})(ch|ng|nh|c|m|n|p|t)?$")
NUMBER_WORDS = {"một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín", "mười", "tư"}


def _plain(token: str) -> str:
    token = token.lower().replace("đ", "d")
    token = "".join(c for c in unicodedata.normalize("NFD", token) if c not in TONE_MARKS)
    return "".join(c for c in unicodedata.normalize("NFD", token) if not unicodedata.combining(c))


def _words(phrase: str) -> list[str]:
    return re.sub(r"[.,!?;:\"']", "", phrase).split()


def _problems(phrase: str) -> list[str]:
    words = _words(phrase)
    found = []
    for w in words:
        if any(c.isdigit() for c in w):
            found.append(f'digit in "{w}"')
        elif not SYLLABLE.match(_plain(w)):
            found.append(f'"{w}" is not a Vietnamese syllable (loanword?)')
    if not 5 <= len(words) <= 11:
        found.append(f"{len(words)} words")
    if words and words[-1].lower() in NUMBER_WORDS:
        found.append(f'ends with the number word "{words[-1]}"')
    return found


def test_the_pool_is_as_large_as_promised_and_has_no_duplicates():
    assert len(PHRASES) >= MIN_POOL_SIZE
    assert len(set(PHRASES)) == len(PHRASES)


def test_no_phrase_has_a_loanword_a_digit_or_an_awkward_length():
    flagged = {p: _problems(p) for p in PHRASES if _problems(p)}
    assert not flagged, flagged


@pytest.mark.parametrize("word", ["podcast", "guitar", "email", "laptop", "camera", "wifi", "internet", "sofa", "micro"])
def test_the_check_catches_the_kind_of_word_it_exists_for(word):
    assert _problems(f"Tôi thường dùng {word} trong lúc đi bộ")


@pytest.mark.parametrize("phrase", [
    "Hôm nay trời nắng đẹp và gió mát.",
    "Cô giáo dặn cả lớp làm bài đầy đủ.",
    "Bạn có thể chỉ đường giúp tôi được không.",   # "không" at the end is ordinary Vietnamese
    "Chiếc thuyền nhỏ neo lại bên bến sông.",
    "Người thợ mộc đang làm một chiếc bàn.",
])
def test_the_check_accepts_ordinary_vietnamese(phrase):
    assert _problems(phrase) == []
