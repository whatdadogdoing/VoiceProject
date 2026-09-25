"""The consent version is a constant in the backend, while the text the user reads
is written in frontend/index.html. Nothing tied the two together, so the text
could change and old users would stay "consented" to words they never saw. This
pins each version to a hash of its text: editing the text without a new version
fails here."""
import hashlib
import pathlib
import re

import pytest

from services.consent import CURRENT_CONSENT_VERSION

INDEX_HTML = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "index.html"

# version -> SHA-256 of the consent text shown with it. To change the text, bump
# CURRENT_CONSENT_VERSION (which asks every user to agree again) and add the new
# hash here; keep the old lines, they record what earlier users agreed to.
CONSENT_TEXT_HASHES = {
    "1.0": "93dc7490e05b37546aedc1354215c5c4d35765fd31f511807cad384f7a458419",
}


def _consent_text() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index(">", html.index('id="step-consent"')) + 1
    end = html.index("<button", start)
    return " ".join(re.sub(r"<[^>]+>", " ", html[start:end]).split())


def test_the_consent_text_is_the_one_recorded_for_the_current_version():
    if not INDEX_HTML.exists():
        pytest.skip("frontend/ is outside backend/, which is all the container mounts")
    assert CURRENT_CONSENT_VERSION in CONSENT_TEXT_HASHES, (
        f"consent version {CURRENT_CONSENT_VERSION} has no hash in this file yet"
    )
    actual = hashlib.sha256(_consent_text().encode("utf-8")).hexdigest()
    assert actual == CONSENT_TEXT_HASHES[CURRENT_CONSENT_VERSION], (
        "the consent text in frontend/index.html changed without a new CURRENT_CONSENT_VERSION "
        f"(services/consent.py); new hash: {actual}"
    )


def test_a_hash_is_kept_for_the_current_version():
    assert CURRENT_CONSENT_VERSION in CONSENT_TEXT_HASHES
    assert all(len(h) == 64 for h in CONSENT_TEXT_HASHES.values())
