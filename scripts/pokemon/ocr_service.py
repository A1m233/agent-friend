"""Bounded, optional local OCR worker. No game process or binary access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--max-seconds", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 21600:
        raise SystemExit("max-seconds must be 1..21600")
    # Match the pinned package already used by ocr-check, not RapidOCR's v3 API.
    import numpy as np
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR

    root = args.instance.resolve(strict=True)
    folder = root / "ocr"
    requests = folder / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    heartbeat = folder / "heartbeat.json"
    if heartbeat.exists() and time.time() - heartbeat.stat().st_mtime < 15:
        raise SystemExit("OCR service already running")
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    token = uuid4().hex
    deadline = time.monotonic() + args.max_seconds
    (folder / "stop").unlink(missing_ok=True)
    print(json.dumps({"status": "ready", "instance": str(root), "token": token}), flush=True)
    try:
        while time.monotonic() < deadline and not (folder / "stop").exists():
            write(heartbeat, {"token": token, "time": time.time()})
            for request in requests.glob("*.json"):
                item = json.loads(request.read_text(encoding="utf-8"))
                source, digest = item.get("source", ""), item.get("sha256", "")
                if not re.fullmatch(r"[a-f0-9]{32}", source) or not re.fullmatch(
                    r"[a-f0-9]{64}", digest
                ):
                    request.unlink()
                    continue
                path = (root / "screens" / f"{source}.png").resolve(strict=True)
                if (
                    not path.is_relative_to((root / "screens").resolve())
                    or path.stat().st_size > 1048576
                ):
                    request.unlink()
                    continue
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    request.unlink()
                    continue
                output = folder / "results" / f"{digest}.json"
                if not output.exists():
                    with Image.open(path) as image:
                        if image.size != (240, 160):
                            request.unlink()
                            continue
                        pixels = np.asarray(
                            image.convert("RGB").resize((720, 480), Image.Resampling.NEAREST)
                        )[:, :, ::-1]
                    started = time.monotonic()
                    result, _ = engine(pixels, use_cls=False)
                    write(
                        output,
                        {
                            "status": "completed",
                            "source": source,
                            "sha256": digest,
                            "advisory_only": True,
                            "image_paths": [path.name],
                            "lines": [
                                {
                                    "text": row[1],
                                    "confidence": float(row[2]),
                                    "uncertain": float(row[2]) < 0.9,
                                    "box": [[float(x) / 3, float(y) / 3] for x, y in row[0]],
                                }
                                for row in result or []
                            ],
                            "seconds": time.monotonic() - started,
                            "message": "OCR 仅供辅助，数字、对象、数量须对照原图；不自动写入记录。",
                        },
                    )
                request.unlink(missing_ok=True)
            time.sleep(0.1)
    finally:
        if (
            heartbeat.exists()
            and json.loads(heartbeat.read_text(encoding="utf-8")).get("token") == token
        ):
            heartbeat.unlink()


if __name__ == "__main__":
    main()
