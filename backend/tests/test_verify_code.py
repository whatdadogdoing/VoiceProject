"""services/verify_code: the switch, the generator, and splitting a transcript into phrase and code.

The transcripts are real: what PhoWhisper (spells the digits out) and Whisper small (writes numerals)
produced for a speaker reading a challenge phrase and then a 4-digit code, in the digit measurement.
"""
import pytest

from services import verify_code
from services.verify_code import CODE_LENGTH, split_code


@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("false", False), ("0", False), ("no", False),
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), (" on ", True),
])
def test_the_switch_is_off_unless_it_is_asked_for(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("VERIFY_CODE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("VERIFY_CODE_ENABLED", value)

    assert verify_code.enabled() is expected


def test_a_code_is_four_digits_including_leading_zeros(monkeypatch):
    digits = iter([0, 9, 6, 7])
    monkeypatch.setattr(verify_code.secrets, "randbelow", lambda n: next(digits))

    assert verify_code.new_code() == "0967"
    assert CODE_LENGTH == 4


def test_codes_come_from_the_secrets_module_and_vary():
    codes = {verify_code.new_code() for _ in range(50)}

    assert all(len(c) == CODE_LENGTH and c.isdigit() for c in codes)
    assert len(codes) > 40   # 50 draws from 10,000 do not collide much


@pytest.mark.parametrize("heard, said, code", [
    # PhoWhisper spells the digits out
    ("anh trai tôi đang chuẩn bị đi làm ba ba chín không.", "anh trai tôi đang chuẩn bị đi làm", "3390"),
    ("cô ấy mặc chiếc áo dài màu xanh không chín sáu bảy.", "cô ấy mặc chiếc áo dài màu xanh", "0967"),
    # Whisper small writes numerals
    ("Anh trai tôi đang chuẩn bị đi làm 3390", "anh trai tôi đang chuẩn bị đi làm", "3390"),
    ("Tôi thích đi bộ giọc theo bờ biển 4538", "tôi thích đi bộ giọc theo bờ biển", "4538"),
    # the same reading written the other ways recognizers write it
    ("hôm nay trời nắng 4 7 2 9", "hôm nay trời nắng", "4729"),
    ("hôm nay trời nắng 4-7-2-9.", "hôm nay trời nắng", "4729"),
    ("hôm nay trời nắng 47 29", "hôm nay trời nắng", "4729"),
    ("hôm nay trời nắng bốn 7 hai chín", "hôm nay trời nắng", "4729"),
    ("hôm nay trời nắng bẩy bảy hai chín", "hôm nay trời nắng", "7729"),
])
def test_the_code_is_taken_from_the_end_however_the_digits_were_written(heard, said, code):
    assert split_code(heard) == (said, code)


def test_a_phrase_that_ends_in_a_digit_word_keeps_it():
    # "không" is both the last word of many phrases and the digit zero
    said, code = split_code("bạn có thể chỉ đường giúp tôi được không bốn bảy hai chín")

    assert said == "bạn có thể chỉ đường giúp tôi được không"
    assert code == "4729"


def test_a_code_that_starts_with_zero_after_such_a_phrase_is_still_four_digits():
    said, code = split_code("bạn có thể chỉ đường giúp tôi được không không chín sáu bảy")

    assert said == "bạn có thể chỉ đường giúp tôi được không"
    assert code == "0967"


def test_too_few_digits_are_returned_as_heard_so_they_cannot_match():
    assert split_code("hôm nay trời nắng bốn bảy hai") == ("hôm nay trời nắng", "472")


def test_no_digits_at_all_gives_an_empty_code():
    assert split_code("hôm nay trời nắng đẹp và gió mát") == ("hôm nay trời nắng đẹp và gió mát", "")


def test_a_long_numeral_keeps_only_the_last_four_digits():
    assert split_code("hôm nay trời nắng 104729")[1] == "4729"


def test_tens_words_are_not_digits_so_a_number_read_as_a_number_does_not_pass():
    # "ba mươi bảy" is 37 as a number but the code is read digit by digit
    assert split_code("hôm nay trời nắng ba mươi bảy")[1] == "7"
