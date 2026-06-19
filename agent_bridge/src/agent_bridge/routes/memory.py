"""026 · 记忆面板专用 REST。

| Endpoint | 用途 |
|---|---|
| GET  /v1/memory/semantic       | list semantic |
| GET  /v1/memory/episodic       | list episodic（可按 persona 过滤） |
| GET  /v1/memory/search         | FTS 关键字搜（layer = semantic / episodic / both） |
| GET  /v1/memory/recalls        | ring buffer snapshot |
| POST /v1/memory/recall-probe   | 复用 Memory.retrieve(..., source="probe") |

runtime.memory is None 时所有路由 503（memory disabled）。
owner_user_id v1 锁死 DEFAULT_OWNER_USER_ID="local",不走 query 参数。
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from memory.contracts import RecallTrace, RecallTraceItem
from memory.store import SqliteMemoryStore
from memory.store.schema import serialize_ts
from pydantic import BaseModel, Field

from memory import Memory

from ..assembly import BridgeRuntime
from ..dev.recall_buffer import RecallBuffer

logger = logging.getLogger(__name__)

OWNER = "local"


class RecallProbeBody(BaseModel):
    """``POST /v1/memory/recall-probe`` 请求体。"""

    query: str = Field(..., description="试探召回 query")
    persona_id: str = Field(..., description="目标 persona id")
    top_k: int | None = Field(8, ge=1, le=50, description="返回条目上限，默认 8")


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    """把 memory inspector 路由挂到 :class:`FastAPI` 实例。"""
    router = APIRouter(prefix="/v1/memory", tags=["memory"])

    def _require_memory() -> tuple[Memory, RecallBuffer]:
        if runtime.memory is None or runtime.recall_buffer is None:
            raise HTTPException(status_code=503, detail="memory disabled")
        return runtime.memory, runtime.recall_buffer

    def _store() -> SqliteMemoryStore:
        return _require_memory()[0].store

    @router.get("/semantic")
    def list_semantic(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """按 created_at 倒序列出活跃语义记忆。"""
        rows = _store().list_semantic(owner_user_id=OWNER, limit=limit, offset=offset)
        return [_dataclass_to_dict(r) for r in rows]

    @router.get("/episodic")
    def list_episodic(
        persona_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """按 occurred_at 倒序列出活跃情节记忆；persona_id 为 None 时不过滤 persona。"""
        rows = _store().list_episodic(
            owner_user_id=OWNER, persona_id=persona_id, limit=limit, offset=offset
        )
        return [_dataclass_to_dict(r) for r in rows]

    @router.get("/search")
    def search(
        q: str, layer: str = "both", persona_id: str | None = None, limit: int = 50
    ) -> dict[str, list[dict[str, Any]]]:
        """FTS 关键字搜索。layer 可选 semantic / episodic / both。"""
        store = _store()
        out: dict[str, list[dict[str, Any]]] = {"semantic": [], "episodic": []}
        if layer in ("semantic", "both"):
            sem = store.search_semantic(
                q, owner_user_id=OWNER, persona_id=persona_id or "", limit=limit
            )
            out["semantic"] = [{"row": _dataclass_to_dict(r), "bm25": b} for r, b in sem]
        if layer in ("episodic", "both"):
            epi = store.search_episodic(q, owner_user_id=OWNER, persona_id=persona_id, limit=limit)
            out["episodic"] = [{"row": _dataclass_to_dict(r), "bm25": b} for r, b in epi]
        return out

    @router.get("/recalls")
    def list_recalls(limit: int = 100) -> list[dict[str, Any]]:
        """返回进程内 recall trace ring buffer 的快照（最新在前）。"""
        _, buffer = _require_memory()
        traces = buffer.snapshot(limit=limit)
        return [_trace_to_dict(t) for t in traces]

    @router.post("/recall-probe")
    def recall_probe(body: RecallProbeBody) -> dict[str, Any]:
        """手动触发一次只读召回试探，并把产生的 trace 一并返回。"""
        memory, buffer = _require_memory()
        ctx = memory.retrieve(
            body.query,
            persona_id=body.persona_id,
            owner_user_id=OWNER,
            top_k=body.top_k,
            source="probe",
        )
        latest = buffer.snapshot(limit=1)
        return {
            "rendered": ctx.rendered,
            "items": [_dataclass_to_dict(i) for i in ctx.items],
            "trace": _trace_to_dict(latest[0]) if latest else None,
        }

    app.include_router(router)


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """把 frozen dataclass 转成 JSON 友好的 dict，datetime 转 ISO8601 字符串。"""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        raw = dataclasses.asdict(obj)
        return {k: _serialize_value(v) for k, v in raw.items()}
    if isinstance(obj, dict):
        return {k: _serialize_value(v) for k, v in obj.items()}
    raise TypeError(f"expected dataclass or dict, got {type(obj).__name__}")


def _serialize_value(value: Any) -> Any:
    """递归处理 datetime / list / dict，使其可被 JSON 序列化。"""
    if isinstance(value, datetime):
        return serialize_ts(value)
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    return value


def _trace_to_dict(trace: RecallTrace) -> dict[str, Any]:
    """RecallTrace 转 dict，保持与 RecallTrace dataclass 同 shape。"""
    return {
        "timestamp": serialize_ts(trace.timestamp),
        "query": trace.query,
        "owner_user_id": trace.owner_user_id,
        "persona_id": trace.persona_id,
        "top_k": trace.top_k,
        "source": trace.source,
        "pinned_pre_gate": trace.pinned_pre_gate,
        "pinned_post_gate": trace.pinned_post_gate,
        "gate_enabled": trace.gate_enabled,
        "gate_mode": trace.gate_mode,
        "gate_decision": trace.gate_decision,
        "candidates_count": trace.candidates_count,
        "ranked_count": trace.ranked_count,
        "items": [_trace_item_to_dict(i) for i in trace.items],
    }


def _trace_item_to_dict(item: RecallTraceItem) -> dict[str, Any]:
    return {
        "text": item.text,
        "layer": item.layer,
        "source_ref": item.source_ref,
        "score": item.score,
    }
