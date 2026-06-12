"""014 · dev CLI：订阅 ``/push/subscribe`` SSE，美化打印 envelope。

用法（通过 ``scripts/dev-push-subscribe/run.{sh,ps1}``）：

    ./scripts/dev-push-subscribe/run.sh [--url URL] [--kinds KINDS]

默认：``--url http://127.0.0.1:18800 --kinds agent_turn,user_turn``

每条 envelope 按一行 JSON 打印（heartbeat 静默不打，避免噪声）；
带 ``--verbose`` 时连 heartbeat 也打。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def _format_envelope(env: dict[str, Any]) -> str:
    kind = env.get("kind", "?")
    sid = env.get("session_id", "")
    seq = env.get("seq", 0)
    src = env.get("source_kind") or "-"
    events = env.get("events") or []
    parts = [f"[#{seq:>3}]", f"kind={kind}", f"sid={sid[:8] or '-'}", f"src={src}"]
    parts.append(f"events={len(events)}")
    # 简明显示前几条 event 类型
    if events:
        types = [e.get("type", "?") for e in events]
        parts.append("[" + ",".join(types) + "]")
    return " ".join(parts)


def _stream_subscribe(url: str, kinds: str, *, verbose: bool) -> int:
    """订阅 SSE，按行美化打印 envelope。Ctrl+C 退出。"""
    endpoint = url.rstrip("/") + "/push/subscribe"
    params = {"kinds": kinds}
    print(f"# 订阅: GET {endpoint}?kinds={kinds}", file=sys.stderr)
    try:
        with httpx.stream("GET", endpoint, params=params, timeout=None) as resp:
            if resp.status_code != 200:
                print(
                    f"# 订阅失败 HTTP {resp.status_code}: {resp.read().decode()}",
                    file=sys.stderr,
                )
                return 1
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line[6:])
                if not verbose and payload.get("kind") == "heartbeat":
                    continue
                print(_format_envelope(payload), flush=True)
    except KeyboardInterrupt:
        print("# 中断", file=sys.stderr)
        return 0
    except httpx.HTTPError as e:
        print(f"# 连接错误: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="订阅 bridge /push/subscribe 并美化打印 envelope（dev / 测试用）。",
    )
    p.add_argument(
        "--url",
        default="http://127.0.0.1:18800",
        help="bridge URL（默认 http://127.0.0.1:18800）",
    )
    p.add_argument(
        "--kinds",
        default="agent_turn,user_turn",
        help="逗号分隔的 envelope kind 过滤（默认 agent_turn,user_turn）",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="也打印 heartbeat（默认静默）",
    )
    args = p.parse_args(argv)
    return _stream_subscribe(args.url, args.kinds, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
