"""Inspect and symbolize native crashes using explicit local artifacts only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(SCRIPT_ROOT / "package"))
from _lib.native_symbols import Minidump, PeImage  # noqa: E402
from _lib.native_symbols.stackwalk import walk  # noqa: E402
from native_symbols import validate_archive  # noqa: E402


def symbolize(dump_path: Path, symbols: Path, incident_image: Path | None = None) -> dict:
    manifest = validate_archive(symbols)
    entry = manifest["modules"][0]
    image_path = symbols / entry["image"]["name"]
    image = PeImage(image_path)
    dump = Minidump(dump_path)
    matches = [
        m
        for m in dump.modules
        if m["codeView"] and all(m["codeView"].get(k) == image.codeview[k] for k in ("guid", "age"))
    ]
    if (
        len(matches) != 1
        or matches[0]["sizeOfImage"] != image.size_of_image
        or dump.architecture != 9
    ):
        raise RuntimeError("dump/PE/PDB identity mismatch")
    comparison = "unavailable"
    if incident_image is not None:
        incident = PeImage(incident_image)
        if incident.metadata() != image.metadata():
            raise RuntimeError("incident executable code/identity mismatch")
        comparison = "matched-incident-executable"
    result = walk(dump, image_path, symbols)
    return {
        "schemaVersion": 1,
        "buildIdentity": manifest["buildIdentity"],
        "exception": dump.exception,
        "binding": image.codeview,
        "codeSectionsVerification": comparison,
        **result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--dump", type=Path, required=True)
    sym = sub.add_parser("symbolize")
    sym.add_argument("--dump", type=Path, required=True)
    sym.add_argument(
        "--symbols",
        type=Path,
        required=True,
        help="Private archive directory containing symbols.json",
    )
    sym.add_argument("--incident-image", type=Path)
    sym.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            print(json.dumps(Minidump(args.dump).metadata(), ensure_ascii=False, indent=2))
        else:
            result = symbolize(args.dump, args.symbols, args.incident_image)
            # Evidence is immutable; never overwrite a previous analysis silently.
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "frames": len(result["frames"]),
                        "gaps": result["gaps"],
                    }
                )
            )
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(
            json.dumps({"error": type(error).__name__, "message": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
