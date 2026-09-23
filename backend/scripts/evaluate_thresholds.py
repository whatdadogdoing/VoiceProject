"""Measures how well the deployed thresholds actually work, instead of taking
0.5 / 0.7 / 0.85 on faith: how many voice clones get through, and how many
genuine recordings get rejected, at each threshold.

It scores audio with the same models /verify uses (Resemblyzer for speaker
match, AASIST-L on ONNX Runtime for the spoof score) and reports FRR / FAR /
EER with 95% confidence intervals.

Inputs (each a directory of audio files, or a single file; anything ffmpeg can
decode):
  --enroll     the enrollment recordings of the target speaker (the first
               --enroll-count files, sorted by name, build the voiceprint)
  --genuine    HELD-OUT recordings of the same speaker (must not be the
               enrollment files) -- these should be accepted
  --impostors  recordings of OTHER real people -- these should be rejected
  --clones     synthetic clones of the target's voice, e.g. the output of
               fake-voice-lab -- these should be rejected

What it does NOT include: the speech-to-text phrase check, the loudness gate,
the context rules (new device / unusual hour) and the OTP step. Those are
separate layers; this measures the two biometric scores and their thresholds.
Numbers are only as good as the data: with few recordings the confidence
intervals are wide, and that is stated in the report rather than hidden.

Run inside the backend container (needs ffmpeg + the ML stack), from backend/:
  python scripts/evaluate_thresholds.py --enroll data/enroll --genuine data/genuine \\
      --impostors data/impostors --clones data/clones --out-dir eval_results
"""
import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.eval_metrics import (  # noqa: E402
    describe, equal_error_rate, pipeline_acceptance, threshold_table, wilson_interval,
)
from services.risk_engine import MATCH_THRESHOLD, SPOOF_THRESHOLD, STRICT_MATCH_THRESHOLD  # noqa: E402

AUDIO_EXTS = {".wav", ".webm", ".mp3", ".m4a", ".mp4", ".ogg", ".flac"}
MATCH_GRID = [round(0.50 + 0.05 * i, 2) for i in range(10)]   # 0.50 .. 0.95
SPOOF_GRID = [round(0.1 * i, 1) for i in range(1, 10)]        # 0.1 .. 0.9
SMALL_N = 20


def list_audio(path):
    if not path:
        return []
    p = Path(path)
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXTS)


def pct(k, n):
    """'12.5% (4-31%)' -- rate with its 95% Wilson interval."""
    if n == 0:
        return "n/a"
    lo, hi = wilson_interval(k, n)
    return f"{100 * k / n:.1f}% ({100 * lo:.0f}-{100 * hi:.0f}%)"


def score_files(files, group, voiceprint, models):
    embed, cosine_similarity, analyze, to_wav_pcm16 = models
    rows = []
    for f in files:
        try:
            wav = to_wav_pcm16(f.read_bytes())
            rows.append({
                "group": group,
                "file": str(f),
                "voiceprint_score": cosine_similarity(voiceprint, embed(wav)),
                "spoof_score": analyze(wav),
            })
        except Exception as exc:  # one undecodable file shouldn't sink the whole run
            print(f"skipping {f}: {exc}", file=sys.stderr)
    return rows


def build_report(scores, enroll_files, enroll_count):
    by = {g: [r for r in scores if r["group"] == g] for g in ("genuine", "impostors", "clones")}
    vp = {g: [r["voiceprint_score"] for r in rs] for g, rs in by.items()}
    sp = {g: [r["spoof_score"] for r in rs] for g, rs in by.items()}
    L = []

    L.append("# Threshold evaluation")
    L.append("")
    L.append("Scores only: excludes the phrase check, loudness gate, context rules and OTP.")
    L.append("")
    L.append(f"Deployed thresholds: spoof score >= {SPOOF_THRESHOLD} rejected; "
             f"speaker match < {MATCH_THRESHOLD} rejected "
             f"(< {STRICT_MATCH_THRESHOLD} in an unusual context).")
    L.append(f"Voiceprint built from {enroll_count} enrollment recording(s): "
             + ", ".join(Path(f).name for f in enroll_files[:enroll_count]))
    L.append("")
    L.append("## Data")
    L.append("")
    L.append("| group | n | speaker match min / median / max | spoof score min / median / max |")
    L.append("|---|---|---|---|")
    for g in ("genuine", "impostors", "clones"):
        d_vp, d_sp = describe(vp[g]), describe(sp[g])
        if not d_vp["n"]:
            L.append(f"| {g} | 0 | - | - |")
        else:
            L.append(f"| {g} | {d_vp['n']} | {d_vp['min']:.3f} / {d_vp['median']:.3f} / {d_vp['max']:.3f} "
                     f"| {d_sp['min']:.3f} / {d_sp['median']:.3f} / {d_sp['max']:.3f} |")
    small = [g for g in by if 0 < len(by[g]) < SMALL_N]
    if small:
        L.append("")
        L.append(f"WARNING: fewer than {SMALL_N} samples in: {', '.join(small)}. "
                 "The confidence intervals below are wide; treat point estimates as indicative only.")

    # -- speaker verification --------------------------------------------------
    if vp["genuine"] and (vp["impostors"] or vp["clones"]):
        L.append("")
        L.append("## Speaker verification (does it sound like the enrolled person?)")
        L.append("")
        L.append("FRR = genuine recordings rejected. FAR = attack recordings accepted. "
                 "Higher threshold = stricter.")
        L.append("")
        L.append("| threshold | FRR (genuine rejected) | FAR impostors | FAR clones |")
        L.append("|---|---|---|---|")
        imp = threshold_table(vp["genuine"], vp["impostors"], MATCH_GRID, accept_if_ge=True)
        clo = threshold_table(vp["genuine"], vp["clones"], MATCH_GRID, accept_if_ge=True)
        for a, b in zip(imp, clo):
            mark = "  <- deployed" if a.threshold == MATCH_THRESHOLD else ""
            L.append(f"| {a.threshold:.2f}{mark} | {pct(a.genuine_rejected, a.genuine_total)} "
                     f"| {pct(a.attack_accepted, a.attack_total)} | {pct(b.attack_accepted, b.attack_total)} |")
        L.append("")
        for name, group in (("impostors", "impostors"), ("clones", "clones")):
            if vp[group]:
                eer, t = equal_error_rate(vp["genuine"], vp[group], accept_if_ge=True)
                L.append(f"- EER genuine vs {name}: {100 * eer:.1f}% at threshold {t:.3f}")

    # -- anti-spoofing ---------------------------------------------------------
    real_sp = sp["genuine"] + sp["impostors"]   # any real human speech counts as bona fide
    if real_sp and sp["clones"]:
        L.append("")
        L.append("## Anti-spoofing (is it a real person, or synthetic?)")
        L.append("")
        L.append("Bona fide = genuine + impostor recordings (all real human speech). "
                 "FRR = real speech wrongly flagged as fake. FAR = clones that were NOT caught.")
        L.append("")
        L.append("| threshold | FRR (real flagged as fake) | FAR (clones not caught) |")
        L.append("|---|---|---|")
        for p in threshold_table(real_sp, sp["clones"], SPOOF_GRID, accept_if_ge=False):
            mark = "  <- deployed" if p.threshold == SPOOF_THRESHOLD else ""
            L.append(f"| {p.threshold:.1f}{mark} | {pct(p.genuine_rejected, p.genuine_total)} "
                     f"| {pct(p.attack_accepted, p.attack_total)} |")
        eer, t = equal_error_rate(real_sp, sp["clones"], accept_if_ge=False)
        L.append("")
        L.append(f"- EER real vs clones: {100 * eer:.1f}% at threshold {t:.3f}")

    # -- end to end ------------------------------------------------------------
    L.append("")
    L.append("## End to end at the deployed thresholds")
    L.append("")
    L.append("Accepted = spoof score below the spoof threshold AND speaker match at or above the match threshold.")
    L.append("")
    L.append(f"| group | accepted at match >= {MATCH_THRESHOLD} | accepted at match >= {STRICT_MATCH_THRESHOLD} (unusual context) |")
    L.append("|---|---|---|")
    for g, should in (("genuine", "want high"), ("impostors", "want 0"), ("clones", "want 0")):
        recs = list(zip(vp[g], sp[g]))
        if not recs:
            continue
        a, n = pipeline_acceptance(recs, SPOOF_THRESHOLD, MATCH_THRESHOLD)
        b, _ = pipeline_acceptance(recs, SPOOF_THRESHOLD, STRICT_MATCH_THRESHOLD)
        L.append(f"| {g} ({should}) | {pct(a, n)} | {pct(b, n)} |")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--enroll", required=True)
    ap.add_argument("--genuine", required=True)
    ap.add_argument("--impostors")
    ap.add_argument("--clones")
    ap.add_argument("--enroll-count", type=int, default=3)
    ap.add_argument("--out-dir", default="eval_results")
    args = ap.parse_args()

    enroll_files = list_audio(args.enroll)
    if len(enroll_files) < args.enroll_count:
        sys.exit(f"need at least {args.enroll_count} enrollment recordings, found {len(enroll_files)}")
    genuine_files = list_audio(args.genuine)
    overlap = {f.resolve() for f in enroll_files[:args.enroll_count]} & {f.resolve() for f in genuine_files}
    if overlap:
        sys.exit(f"genuine test files must not include enrollment files: {sorted(str(p) for p in overlap)}")

    # heavy imports last so --help and argument errors stay instant
    from services.anti_spoofing import analyze
    from services.audio import to_wav_pcm16
    from services.speaker_verification import average_embedding, cosine_similarity, embed

    print("building voiceprint ...", file=sys.stderr)
    voiceprint = average_embedding([embed(to_wav_pcm16(f.read_bytes())) for f in enroll_files[:args.enroll_count]])
    models = (embed, cosine_similarity, analyze, to_wav_pcm16)

    scores = []
    for group, path in (("genuine", args.genuine), ("impostors", args.impostors), ("clones", args.clones)):
        files = list_audio(path)
        print(f"scoring {group}: {len(files)} file(s)", file=sys.stderr)
        scores += score_files(files, group, voiceprint, models)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "scores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["group", "file", "voiceprint_score", "spoof_score"])
        w.writeheader()
        w.writerows(scores)
    report = build_report(scores, enroll_files, args.enroll_count)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {out / 'scores.csv'} and {out / 'report.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
