"""Which model each speech-to-text tier loads, and what happens when it is missing.

No model is loaded: only the choice of source is tested. The fast tier is PhoWhisper (converted by
scripts/fetch_phowhisper.py); a fresh clone that has not run the script must still start, on stock
Whisper base, and say so.
"""
import importlib
import logging

import pytest


@pytest.fixture
def stt(monkeypatch, tmp_path):
    module = importlib.import_module("services.stt")
    monkeypatch.setattr(module, "_MODELS_DIR", str(tmp_path))
    return module


def test_each_tier_loads_from_its_own_directory_when_present(stt, tmp_path):
    (tmp_path / "phowhisper-base-ct2").mkdir()
    (tmp_path / "faster-whisper-small").mkdir()

    assert stt._model_source("fast") == str(tmp_path / "phowhisper-base-ct2")
    assert stt._model_source("accurate") == str(tmp_path / "faster-whisper-small")


def test_a_missing_fast_model_falls_back_to_stock_whisper_base_and_says_so(stt, caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert stt._model_source("fast") == "base"

    assert "fetch_phowhisper.py" in caplog.text


def test_a_missing_second_opinion_falls_back_to_stock_whisper_small_quietly(stt, caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        assert stt._model_source("accurate") == "small"

    assert caplog.text == ""


def test_the_fast_tier_is_the_one_that_uses_the_wider_search(stt):
    # beam 5 was measured for the cheap recognizer, beam 1 for the slow one
    assert (stt._TIERS["fast"]["beam"], stt._TIERS["accurate"]["beam"]) == (5, 1)


def test_startup_prepares_both_tiers(stt, monkeypatch):
    loaded = []
    monkeypatch.setattr(stt, "_load", lambda tier: loaded.append(tier))

    stt.load_models()

    assert loaded == ["fast", "accurate"]
