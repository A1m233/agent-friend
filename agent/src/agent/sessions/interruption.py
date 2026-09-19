"""Place interruption facts beside their surviving source turn, never at the tail."""

from collections.abc import Sequence
from uuid import uuid4

from ..messages import Message
from .events import Event


def scope_interruption(
    items: Sequence[Message],
    requests: Sequence[Event],
    results: Sequence[Event],
    *,
    run_id: str,
    reason_code: str,
    result_origin: str,
) -> list[Message]:
    previous = next(
        (
            m
            for m in items
            if m.meta.get("synthetic") == "turn_aborted" and m.meta.get("run_id") == run_id
        ),
        None,
    )
    retained = [m for m in items if m is not previous]
    event_ids = {e.uuid for e in (*requests, *results)}
    positions = [
        i
        for i, m in enumerate(retained)
        if m.uuid in event_ids
        or run_id
        in (
            m.meta.get("provider_run_id"),
            m.meta.get("provider_reasoning_run_id"),
            m.meta.get("run_id"),
        )
    ]
    # A compacted-away turn must not be resurrected as a new interruption.
    if not positions or not requests:
        return retained
    unknown = any(
        e.payload.get("executed") == "unknown" or e.payload.get("execution_state") == "unknown"
        for e in results
    )
    not_started = bool(results) and all(e.payload.get("executed") is False for e in results)
    execution = (
        "部分工具的执行结果未知；仅在重试该任务前核实实际状态。"
        if unknown
        else "该轮工具均未执行。"
        if not_started
        else "已落盘的工具结果见该轮记录；中断不表示这些工具需要重跑。"
    )
    content = (
        "<turn_aborted>\n"
        f"历史运行 {run_id}（开始于 {requests[0].ts.isoformat()}）已中断。"
        f"{execution}此事实仅属于上述历史运行，不改变之后用户请求的目标。\n"
        "</turn_aborted>"
    )
    meta = {
        "synthetic": "turn_aborted",
        "run_id": run_id,
        "reason_code": reason_code,
        "result_origin": result_origin,
        "trusted_host_context": True,
        "interruption_scope_version": 1,
    }
    marker = Message(
        role="system",
        content=content,
        timestamp=requests[0].ts,
        meta=meta,
        uuid=previous.uuid if previous is not None else str(uuid4()),
    )
    retained.insert(max(positions) + 1, marker)
    return retained
