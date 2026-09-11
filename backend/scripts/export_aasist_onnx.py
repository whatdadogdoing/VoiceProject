"""One-off dev tool: exports the vendored AASIST-L PyTorch checkpoint
(services/weights/aasist_l.pth) to ONNX (services/weights/aasist_l.onnx),
then verifies the ONNX Runtime output matches PyTorch's within a tight
tolerance on random inputs before printing a latency comparison.

Not part of the running app -- services/anti_spoofing.py only loads the
.onnx file at runtime. Re-run this only if aasist_l.pth is ever retrained or
replaced. Needs torch, onnx and onnxruntime installed (only onnxruntime is a
runtime dependency of the app itself; torch/onnx are dev-only extras for
this script).

Usage: python scripts/export_aasist_onnx.py   (run from backend/)
"""
import os
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.aasist_model import Model as AASISTModel  # noqa: E402

_MODEL_CONFIG = {
    "first_conv": 128,
    "filts": [70, [1, 32], [32, 32], [32, 24], [24, 24]],
    "gat_dims": [24, 32],
    "pool_ratios": [0.4, 0.5, 0.7, 0.5],
    "temperatures": [2.0, 2.0, 100.0, 100.0],
}
NB_SAMP = 64600  # fixed input length the pretrained weights were trained on (~4.04s @ 16kHz)

_WEIGHTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "weights")
WEIGHTS_PATH = os.path.join(_WEIGHTS_DIR, "aasist_l.pth")
ONNX_PATH = os.path.join(_WEIGHTS_DIR, "aasist_l.onnx")


def export():
    model = AASISTModel(_MODEL_CONFIG)
    model.load_state_dict(torch.load(WEIGHTS_PATH, map_location="cpu"))
    model.eval()

    dummy = torch.randn(1, NB_SAMP, dtype=torch.float32)
    torch.onnx.export(
        model,
        dummy,
        ONNX_PATH,
        input_names=["waveform"],
        output_names=["last_hidden", "logits"],
        opset_version=17,
        dynamo=False,
    )
    print(f"Exported {ONNX_PATH} ({os.path.getsize(ONNX_PATH)} bytes)")
    return model


def verify_and_benchmark(model):
    import onnxruntime as ort

    sess = ort.InferenceSession(ONNX_PATH, providers=["CPUExecutionProvider"])

    rng = np.random.default_rng(42)
    max_abs_diff = 0.0
    with torch.no_grad():
        for _ in range(5):
            x_np = (rng.standard_normal((1, NB_SAMP)) * 0.05).astype(np.float32)
            _, torch_logits = model(torch.from_numpy(x_np))
            torch_probs = torch.softmax(torch_logits, dim=1).numpy()

            ort_logits = sess.run(["logits"], {"waveform": x_np})[0]
            ort_probs = torch.softmax(torch.from_numpy(ort_logits), dim=1).numpy()

            max_abs_diff = max(max_abs_diff, float(np.max(np.abs(torch_probs - ort_probs))))

    assert max_abs_diff < 1e-4, f"ONNX output diverges from PyTorch beyond tolerance: {max_abs_diff}"
    print(f"Numeric parity OK, max abs diff across 5 random inputs: {max_abs_diff:.2e}")

    x_np = (rng.standard_normal((1, NB_SAMP)) * 0.05).astype(np.float32)
    x_t = torch.from_numpy(x_np)
    n = 15
    with torch.no_grad():
        for _ in range(2):
            model(x_t)
        t0 = time.perf_counter()
        for _ in range(n):
            model(x_t)
        torch_time = (time.perf_counter() - t0) / n

    for _ in range(2):
        sess.run(["logits"], {"waveform": x_np})
    t0 = time.perf_counter()
    for _ in range(n):
        sess.run(["logits"], {"waveform": x_np})
    onnx_time = (time.perf_counter() - t0) / n

    print(f"torch:  {torch_time * 1000:.1f} ms/call")
    print(f"onnx:   {onnx_time * 1000:.1f} ms/call")
    print(f"speedup: {torch_time / onnx_time:.2f}x")


if __name__ == "__main__":
    m = export()
    verify_and_benchmark(m)
