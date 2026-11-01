"""Explicitly fetch one user-requested GBA candidate; never part of install/package."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from urllib.parse import urlparse

from agent.paths import user_data_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--source-page", required=True)
    args = parser.parse_args()
    if urlparse(args.url).scheme != "https":
        raise SystemExit("HTTPS required")
    root = user_data_dir() / "pokemon" / "candidates"
    root.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        args.url, headers={"User-Agent": "Mozilla/5.0", "Referer": args.source_page}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    if not 192 <= len(data) <= 32 * 1024 * 1024 or data[0xB2] != 0x96:
        raise SystemExit("Response is not a bounded GBA cartridge candidate")
    digest = hashlib.sha256(data).hexdigest()
    path = root / f"{digest}.gba"
    if path.exists() and path.read_bytes() != data:
        raise SystemExit("Existing content mismatch")
    path.write_bytes(data)
    record = {
        "sha256": digest,
        "bytes": len(data),
        "source_page": args.source_page,
        "download_url": args.url,
        "header_title": data[0xA0:0xAC].decode("ascii", errors="replace"),
        "header_code": data[0xAC:0xB0].decode("ascii", errors="replace"),
        "header_checksum_valid": data[0xBD] == ((-sum(data[0xA0:0xBD]) - 0x19) & 255),
        "path": str(path),
        "status": "candidate_not_verified",
        "license": "not_verified_do_not_redistribute",
        "language_verified": False,
    }
    path.with_suffix(".json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
