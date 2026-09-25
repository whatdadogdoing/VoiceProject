"""One-time setup: download PhoWhisper-base and convert it for faster-whisper.

PhoWhisper (VinAI Research, BSD-3-Clause) is Whisper fine-tuned on 844 hours of Vietnamese. As the
fast recognizer of the phrase check it confirmed 19 of 19 recordings made in the app where Whisper base
confirmed 14, at the same speed (see README). The published weights are a PyTorch checkpoint, so they
are converted once to a CTranslate2 int8 model, which is what faster-whisper runs.

The revision is pinned: the checkpoint is a pickle file, and a moved tag must not change what runs.

Usage (from backend/, inside the container; transformers is needed only for this conversion):
    pip install transformers
    python scripts/fetch_phowhisper.py            # writes $MODELS_DIR/phowhisper-base-ct2
    python scripts/fetch_phowhisper.py --force    # convert again

Without the converted model the backend still starts and falls back to Whisper base, with a warning.
"""
import argparse
import os
import sys

REPO = "vinai/PhoWhisper-base"
REVISION = "7ebdb9e88f5cc5271fb88f4d642c82ff9388650e"
DIR_NAME = "phowhisper-base-ct2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models-dir", default=os.environ.get("MODELS_DIR"),
                        help="where the speech models live (default: $MODELS_DIR)")
    parser.add_argument("--force", action="store_true", help="convert even if the model is already there")
    args = parser.parse_args()

    if not args.models_dir:
        sys.exit("MODELS_DIR is not set; pass --models-dir")
    out = os.path.join(args.models_dir, DIR_NAME)
    if os.path.exists(os.path.join(out, "model.bin")) and not args.force:
        print(f"already there: {out}")
        return 0

    try:
        from ctranslate2.converters import TransformersConverter
    except ImportError:
        sys.exit("ctranslate2 is missing: run this inside the backend container")

    print(f"downloading {REPO} @ {REVISION[:10]} and converting to {out} ...")
    TransformersConverter(
        REPO, revision=REVISION, copy_files=["tokenizer.json", "preprocessor_config.json"]
    ).convert(out, quantization="int8", force=args.force)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
