"""Extend only an explicitly synthetic fixture prefix; keep its visible tail byte-identical."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prefix-turns", required=True, type=int)
    args = parser.parse_args()
    source = json.loads(args.identity.read_text(encoding="utf-8"))
    if not source.get("synthetic_only") or args.prefix_turns < 1:
        parser.error("requires synthetic-only source and positive prefix")
    args.output.mkdir(parents=True, exist_ok=False)
    destination = args.output / "data" / "sessions"
    destination.mkdir(parents=True)
    for row in source["sessions"]:
        raw = (Path(source["data_dir"]) / "sessions" / f"{row['session_id']}.jsonl").read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("synthetic source changed")
        path = destination / f"{row['session_id']}.jsonl"
        with path.open("xb") as file:
            if row["kind"] == "long":
                head, tail = raw.split(b"\n", 1)
                file.write(head + b"\n")
                for i in range(args.prefix_turns):
                    for role in ("user", "assistant"):
                        event = {
                            "type": f"{role}_message",
                            "uuid": f"growth-{role}-{i}",
                            "ts": "2026-09-30T00:00:00+00:00",
                            "payload": {"content": f"synthetic prefix {i} " + "x" * 128},
                        }
                        file.write(json.dumps(event, separators=(",", ":")).encode() + b"\n")
                file.write(tail)
                row["fixed_tail_sha256"] = hashlib.sha256(tail).hexdigest()
                row["added_prefix_turns"] = args.prefix_turns
            else:
                file.write(raw)
        row["bytes"] = path.stat().st_size
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    source["data_dir"] = str((args.output / "data").resolve())
    source["log_dir"] = str((args.output / "logs").resolve())
    source["limits"].append(
        "Growth fixture: expected.turns lists original tail only; "
        "use desktop-responsiveness, not desktop-history."
    )
    (args.output / "identity.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "prefix_turns": args.prefix_turns}))


if __name__ == "__main__":
    main()
