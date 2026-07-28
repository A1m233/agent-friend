"""Read privacy-safe Reminder routing diagnostics from a running desktop runtime."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18800")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    if args.samples < 1 or args.interval < 0:
        parser.error("--samples must be >= 1 and --interval must be >= 0")

    endpoint = f"{args.url.rstrip('/')}/v1/runtime/reminder-diagnostics"
    for index in range(args.samples):
        try:
            with urllib.request.urlopen(endpoint, timeout=5) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"todo-presence-smoke: cannot read {endpoint}; start the Tauri desktop first: {exc}"
            ) from exc

        presence = payload.get("presence", {})
        signal = presence.get("desktopSignal", {})
        decision = presence.get("previewDecision", {})
        print(
            json.dumps(
                {
                    "sample": index + 1,
                    "evaluatedAt": presence.get("evaluatedAt"),
                    "desktopSignal": signal,
                    "previewDecision": decision,
                    "imBindingCount": len(presence.get("im", [])),
                    "petAvailable": presence.get("petAvailable"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if index + 1 < args.samples:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
