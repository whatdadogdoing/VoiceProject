import logging
import os
import time

# uvicorn configures this logger at INFO; a module-level logging.getLogger(__name__)
# would silently drop these lines.
logger = logging.getLogger("uvicorn.error")

# Opt-in: nothing is ever written unless this directory already exists. It sits
# under eval_data/, which is git-ignored and docker-ignored, because what lands
# here is somebody's real voice.
CAPTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval_data", "rejected")


def save_rejected(wav_bytes: bytes, tag: str, score: float) -> None:
    """Keep the audio of a sample the anti-spoofing gate rejected, so a false
    positive on a genuine voice can be examined (level, pauses, noise) instead
    of guessed at -- the server otherwise discards the audio after scoring it.
    Never lets a capture problem affect the request."""
    if not os.path.isdir(CAPTURE_DIR):
        return
    try:
        path = os.path.join(CAPTURE_DIR, f"{time.strftime('%H%M%S')}-{tag}-{score:.3f}.wav")
        with open(path, "wb") as fh:
            fh.write(wav_bytes)
        logger.info("saved rejected sample for inspection: %s", path)
    except Exception:
        logger.exception("could not save rejected sample")
