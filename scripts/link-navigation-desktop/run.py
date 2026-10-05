"""Seed a synthetic persisted link session; never sends a model request."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent import BUILTIN_DEFAULT_PERSONA_ID, AttachmentStore, Event, JsonlSessionStore, Session
from agent.command_runtime.workspace import session_workspace_path
from agent.paths import attachments_dir, command_workspaces_dir, sessions_dir


def seed(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    target = output / "文件样本"
    target.mkdir()
    special = target / "报告 (1) %20 #name_.txt"
    special.write_text("152 synthetic local file; no model call\n", encoding="utf-8")
    (target / "独立目录").mkdir()
    png = target / "图片.png"
    png.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1cAAAAASUVORK5CYII="
    ))
    store = JsonlSessionStore(sessions_dir())
    session = Session.new(title="152 验收 · 文件链接（无模型调用）", persona="default",
                          persona_id=BUILTIN_DEFAULT_PERSONA_ID, model="deepseek/deepseek-v4-flash")
    store.create(session)
    workspace = session_workspace_path(command_workspaces_dir(), session.session_id)
    workspace.mkdir(parents=True, exist_ok=True)
    relative = workspace / "relative.txt"
    relative.write_text("152 synthetic relative file", encoding="utf-8")
    assets = AttachmentStore(attachments_dir())
    refs = []
    for path in (special, png):
        async def chunks():
            yield path.read_bytes()
        draft, ref = asyncio.run(assets.import_stream(chunks(), display_name=path.name))
        refs.append(ref)
        assets.release_draft(draft)
    ids = {}
    run = str(uuid4())

    def event(kind: str, key: str, payload: dict) -> None:
        identity = str(uuid4())
        ids[key] = identity
        store.append_event(session.session_id, Event(type=kind, uuid=identity, ts=datetime.now(UTC),
            payload=payload, meta={"run_id": run, "dev_mock": True}))

    # Cherry does not render destinations containing literal spaces; use valid
    # encoded file URIs to exercise special filenames across both renderers.
    path = special.as_uri()
    text = f"""### 文件链接验收

[内联文件](<{path}>) · [文件 URI](<{special.as_uri()}>) · [带行号](<{path}:12:3>)

- **[强调链接](<{path}>)** · [相对文件](relative.txt) · [目录](<{(target / '独立目录').as_posix()}>)

> [引用式][fixture]

| 类型 | 链接 |
| --- | --- |
| 表格 | [表格链接](<{path}>) |

[显示名不是目标.txt](<{path}>) · [不存在](<{target.as_posix()}/missing.txt>) · [不支持](vscode:unsupported)

[网页](https://example.com) · <https://example.com>

普通路径 C:/not-a-link.txt，`C:/inline-code.txt`

[fixture]: <{path}>
"""
    event("user_message", "user", {"content": f"用户正文 [用户文件](<{path}>)", "attachments": [ref.to_dict() for ref in refs]})
    started = datetime.now(UTC).isoformat()
    event("run_started", "start", {"run_id": run, "started_at": started})
    event("assistant_phase", "phase", {"run_id": run, "mode": "native", "validation": "valid"})
    event("assistant_message", "process", {"content": f"执行过程 [过程文件](<{path}>)", "phase": "commentary"})
    event("assistant_message", "answer", {"content": text, "phase": "final_answer",
        "content_parts": [{"content_index": 0, "kind": "text", "start": 0, "end": len(text),
            "annotations": [{"type": "url_citation", "url": str(special), "title": "本地来源"},
                            {"type": "file_citation", "filename": "remote-only.txt", "file_id": "synthetic-remote"}]}]})
    event("run_terminal", "terminal", {"run_id": run, "status": "completed", "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(), "recovered": False})
    fixture = {"synthetic": True, "llmCalls": 0, "session_id": session.session_id, "ids": ids,
        "file": str(special), "directory": str(target / "独立目录"), "relative": str(relative),
        "png": str(png), "assets": [ref.to_dict() for ref in refs]}
    (output / "fixture.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"fixture": str(output / "fixture.json"), "session_id": session.session_id}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New external directory for fixture and screenshots")
    seed(parser.parse_args().output.resolve())
