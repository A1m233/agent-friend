"""Safely add a second IM binding through a running agent-bridge."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"bridge returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach agent-bridge at {base_url}: {exc.reason}") from exc


def print_qr(url: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(out=sys.stdout, tty=sys.stdout.isatty(), invert=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a QQ Bot without replacing or stopping an existing binding."
    )
    parser.add_argument("--url", default="http://127.0.0.1:18800")
    parser.add_argument("--session-id", help="Bind the new Bot to this session after onboarding")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    providers = request_json(args.url, "/v1/im/providers")
    print(f"Existing IM bindings: {len(providers)}")
    for provider in providers:
        print(f"- {provider['im_type']} {provider['bind_id_masked']} ({provider['status']})")

    started = request_json(
        args.url,
        "/v1/im/onboard/start",
        method="POST",
        body={"im_type": "qq", "conflict_policy": "reject"},
    )
    task_id = started["task_id"]
    print("Safe add-only onboarding started. Waiting for QR code...")

    deadline = time.monotonic() + args.timeout
    printed_qr_url: str | None = None
    while time.monotonic() < deadline:
        state = request_json(args.url, f"/v1/im/onboard/{task_id}")
        qr_url = state.get("qr_url")
        if qr_url and qr_url != printed_qr_url:
            print_qr(qr_url)
            print("Scan the QR code above with the QQ account that will use the new Bot.")
            printed_qr_url = qr_url

        if state["status"] == "success":
            bind_id = state["bind_id"]
            if args.session_id:
                encoded_bind_id = urllib.parse.quote(bind_id, safe="")
                request_json(
                    args.url,
                    f"/v1/im/providers/qq/{encoded_bind_id}/session-binding",
                    method="PUT",
                    body={"session_id": args.session_id},
                )
            print(
                f"Added QQ binding {state['bind_id_masked']} without replacing existing bindings."
            )
            if args.session_id:
                print(f"Bound to session {args.session_id}.")
            return 0
        if state["status"] == "failed":
            raise RuntimeError(
                "onboarding failed; existing bindings were left untouched: "
                f"{state.get('error') or 'unknown error'}"
            )
        time.sleep(1.0)

    raise RuntimeError(
        f"onboarding timed out after {args.timeout:g}s; existing bindings were left untouched"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
