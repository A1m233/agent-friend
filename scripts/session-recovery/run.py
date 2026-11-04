"""Offline, explicitly selected repair of historical cold-delivery journal forks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agent.sessions.delivery_recovery import recover_delivery_history
from agent.sessions.store import JsonlSessionStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Apply after validation; keep original backup"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            recover_delivery_history(
                JsonlSessionStore(args.directory), args.session_id, apply=args.apply
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
