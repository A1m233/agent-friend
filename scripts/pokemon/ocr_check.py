"""Offline OCR experiment on an explicitly annotated, private screenshot manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import statistics
import time
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite OCR evidence")
    # Optional experiment dependencies live in uv's isolated environment only.
    import numpy as np
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR

    samples = json.loads(args.samples.read_text(encoding="utf-8"))
    started = time.perf_counter()
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    init_seconds = time.perf_counter() - started
    rows = []
    for sample in samples:
        path = Path(sample["path"])
        with Image.open(path) as source:
            im = source.convert("RGB").crop(tuple(sample["crop"]))
        for scale in (1, 3):
            # Preserve the pixel glyphs; only magnify for the OCR experiment.
            pixels = np.asarray(
                im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
            )[:, :, ::-1]
            started = time.perf_counter()
            result, _ = engine(pixels, use_cls=False)
            elapsed = time.perf_counter() - started
            lines = [{"text": row[1], "confidence": float(row[2])} for row in result or []]
            text = "".join(row["text"] for row in lines)

            def normalize(value: str) -> str:
                return "".join(c for c in value if c.isalnum()).casefold()

            expected = sample["expected"]
            rows.append(
                {
                    "name": sample["name"],
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "crop": sample["crop"],
                    "scale": scale,
                    "expected": expected,
                    "text": text,
                    "exact_normalized": normalize(text) == normalize(expected),
                    "lines": lines,
                    "seconds": elapsed,
                }
            )
    module = Path(importlib.import_module("rapidocr_onnxruntime").__file__).parent
    report: dict[str, Any] = {
        "kind": "offline_ocr_not_game_control",
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("rapidocr-onnxruntime", "onnxruntime", "numpy", "pillow")
        },
        "model_hashes": {
            str(p.relative_to(module)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in module.rglob("*.onnx")
        },
        "init_seconds": init_seconds,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(args.output),
                "init_seconds": init_seconds,
                "samples": len(samples),
                "median_seconds": statistics.median(r["seconds"] for r in rows),
                "rows": [
                    {k: r[k] for k in ("name", "scale", "text", "exact_normalized", "seconds")}
                    for r in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
