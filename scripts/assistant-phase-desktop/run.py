"""115 desktop fixture/probe. Synthetic data only; never invokes a model."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent import BUILTIN_DEFAULT_PERSONA_ID, Event, JsonlSessionStore, Session, sessions_dir

ROOT = Path(__file__).resolve().parents[2]


def seed(output: Path) -> None:
    # Refuse overwrite before creating a session in the ordinary desktop profile.
    with output.open("x", encoding="utf-8") as target:
        store = JsonlSessionStore(sessions_dir())
        session = Session.new(
            title="115 验收 · 合成夹具（无模型调用）",
            persona="default",
            persona_id=BUILTIN_DEFAULT_PERSONA_ID,
            model="deepseek/deepseek-v4-flash",
        )
        store.create(session)
        base = datetime.now(UTC) - timedelta(minutes=5)
        ids: dict[str, str] = {"session": session.session_id}

        def event(kind: str, key: str, payload: dict, run: str, seconds: int) -> None:
            identity = str(uuid4())
            ids[key] = identity
            store.append_event(
                session.session_id,
                Event(
                    type=kind,
                    uuid=identity,
                    ts=base + timedelta(seconds=seconds),
                    payload=payload,
                    meta={"run_id": run, "dev_mock": True},
                ),
            )

        for index, (mode, name) in enumerate(
            (("native", "native"), ("legacy", "legacy"), ("native", "interrupted"))
        ):
            run = f"{session.session_id}:{name}"
            start = index * 120
            event("user_message", f"{name}_user", {"content": f"合成验收：{name}"}, run, start)
            event(
                "run_started",
                f"{name}_start",
                {"run_id": run, "started_at": (base + timedelta(seconds=start)).isoformat()},
                run,
                start,
            )
            event(
                "assistant_phase",
                f"{name}_phase",
                {
                    "run_id": run,
                    "mode": mode,
                    "validation": "valid" if name == "native" else "pending",
                },
                run,
                start,
            )
            texts = (
                [
                    ("a", "合成过程A：检查现有业务。", "commentary"),
                    ("unknown", "未知阶段：这段文字始终可见。", "unknown"),
                    ("b", "合成过程B：检查已完成。", "commentary"),
                    ("final", "合成最终回答：本轮检查完成。", "final_answer"),
                ]
                if name == "native"
                else [("text", "Legacy 合成正文，文案和用时照常。", "unknown")]
                if name == "legacy"
                else [("text", "合成中断：不应继续计时或自动收起。", "commentary")]
            )
            for offset, (key, text, phase) in enumerate(texts):
                event(
                    "assistant_message",
                    f"{name}_{key}",
                    {"content": text, "phase": phase, "partial": name == "interrupted"},
                    run,
                    start + offset + 1,
                )
            duration = 112 if name == "native" else 8
            event(
                "run_terminal",
                f"{name}_terminal",
                {
                    "run_id": run,
                    "status": "interrupted" if name == "interrupted" else "completed",
                    "started_at": (base + timedelta(seconds=start)).isoformat(),
                    "finished_at": None
                    if name == "interrupted"
                    else (base + timedelta(seconds=start + duration)).isoformat(),
                    "recovered": name == "interrupted",
                },
                run,
                start + duration,
            )
        event("model_change", "current_model", {"to": "kimi/kimi-k3"}, "", 260)
        json.dump(
            {"synthetic": True, "ids": ids, "session_id": session.session_id},
            target,
            ensure_ascii=False,
            indent=2,
        )
    print(json.dumps({"fixture": str(output.resolve()), "session_id": session.session_id}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "probe"))
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.command == "seed":
        seed(args.fixture)
        return 0
    if args.output_dir is None:
        parser.error("probe requires --output-dir (new directory)")
    command = [
        shutil.which("node") or "node",
        str(Path(__file__).with_name("probe.mjs")),
        str(args.fixture.resolve()),
        str(args.output_dir.resolve()),
    ]
    return subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
