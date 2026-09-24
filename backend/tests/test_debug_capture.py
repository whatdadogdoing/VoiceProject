"""Tests for services/debug_capture.save_rejected: an opt-in way to keep the
audio of a sample the anti-spoofing gate rejected, so a false positive on a real
voice can be inspected. It must do nothing unless the directory exists, and a
capture problem must never break the request that triggered it."""
from services import debug_capture


def test_nothing_is_written_when_the_capture_directory_does_not_exist(tmp_path, monkeypatch):
    missing = tmp_path / "rejected"
    monkeypatch.setattr(debug_capture, "CAPTURE_DIR", str(missing))

    debug_capture.save_rejected(b"RIFFdata", "enroll-spoof", 0.594)

    assert not missing.exists()


def test_audio_is_saved_with_the_tag_and_score_in_its_name(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_capture, "CAPTURE_DIR", str(tmp_path))

    debug_capture.save_rejected(b"RIFFdata", "enroll-spoof", 0.594)

    (saved,) = list(tmp_path.iterdir())
    assert "enroll-spoof-0.594" in saved.name
    assert saved.read_bytes() == b"RIFFdata"


def test_a_write_failure_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_capture, "CAPTURE_DIR", str(tmp_path))
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    debug_capture.save_rejected(b"RIFFdata", "enroll-spoof", 0.594)  # must not raise
