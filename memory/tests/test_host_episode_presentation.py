"""Only confirmed notification receipt prefixes become local display times."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from memory.host_episodes import presentation

OCCURRED_AT = datetime(2026, 9, 6, 20, 9, 27, 18153, tzinfo=UTC)
EPISODE_ID = "notification-presented:n1"
SOURCE_REF = "notification:n1"


def receipt(timestamp: str, *, text: str = "回来看结果吧。", truncated: bool = False) -> str:
    return (
        f"{timestamp}，我通过桌宠气泡就「通知验收」发出提醒，实际显示："
        + json.dumps(text, ensure_ascii=False)
        + "。"
        + ("内容在显示时被截断。" if truncated else "")
    )


def project(summary: str, **changes: object) -> str:
    values = {"episode_id": EPISODE_ID, "source_ref": SOURCE_REF, "occurred_at": OCCURRED_AT}
    values.update(changes)
    return presentation.project_notification_summary(summary, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("offset_minutes", "expected"),
    [
        (480, "2026-09-07 04:09:27 +08:00"),
        (345, "2026-09-07 01:54:27 +05:45"),
        (-210, "2026-09-06 16:39:27 -03:30"),
        (0, "2026-09-06 20:09:27 +00:00"),
    ],
)
def test_format_uses_local_offset_and_omits_microseconds(
    monkeypatch: pytest.MonkeyPatch, offset_minutes: int, expected: str
) -> None:
    local_zone = timezone(timedelta(minutes=offset_minutes))
    monkeypatch.setattr(presentation, "_local_time", lambda value: value.astimezone(local_zone))
    assert presentation.format_notification_time(OCCURRED_AT) == expected
    assert (
        presentation.format_notification_time(OCCURRED_AT.astimezone(timezone(timedelta(hours=-7))))
        == expected
    )


def test_format_rejects_ambiguous_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        presentation.format_notification_time(OCCURRED_AT.replace(tzinfo=None))


@pytest.mark.parametrize("old_time", ["2026-09-06T20:09:27.018153+00:00", "2026-09-06T20:09:27Z"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_old_receipt_localizes_only_prefix_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, old_time: str, wrapped: bool
) -> None:
    monkeypatch.setattr(
        presentation, "_local_time", lambda value: value.astimezone(timezone(timedelta(hours=8)))
    )
    text = '原文时间 2026-09-06T09:09:27.018153+00:00，保留 "引号"、换行\n和反斜线\\。'
    original = receipt(old_time, text=text, truncated=True)
    episode_id = f"episodic:{EPISODE_ID}" if wrapped else EPISODE_ID
    projected = project(original, episode_id=episode_id)
    assert projected == receipt("2026-09-07 04:09:27 +08:00", text=text, truncated=True)
    assert projected.split("，", 1)[1] == original.split("，", 1)[1]
    assert project(projected, episode_id=episode_id) == projected


def test_new_receipt_is_unchanged_in_its_local_zone_and_reprojects_in_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = receipt("2026-09-07 04:09:27 +08:00")
    monkeypatch.setattr(
        presentation, "_local_time", lambda value: value.astimezone(timezone(timedelta(hours=8)))
    )
    assert project(original) == original
    monkeypatch.setattr(
        presentation, "_local_time", lambda value: value.astimezone(timezone(timedelta(hours=-4)))
    )
    changed = project(original)
    assert changed == receipt("2026-09-06 16:09:27 -04:00")
    assert project(changed) == changed


@pytest.mark.parametrize(
    ("episode_id", "source_ref"),
    [
        ("ordinary-episode", "session#event"),
        (EPISODE_ID, "notification:other"),
        (EPISODE_ID, "session#event"),
        ("ordinary-episode", SOURCE_REF),
        ("episodic:episodic:" + EPISODE_ID, SOURCE_REF),
        ("notification-presented:", "notification:"),
    ],
)
def test_non_host_or_mismatched_identity_never_rewrites_text(
    monkeypatch: pytest.MonkeyPatch, episode_id: str, source_ref: str
) -> None:
    def unexpected(_: datetime) -> str:
        pytest.fail("non-host evidence must not reach time conversion")

    monkeypatch.setattr(presentation, "format_notification_time", unexpected)
    original = receipt("2026-09-06T20:09:27.018153+00:00")
    assert project(original, episode_id=episode_id, source_ref=source_ref) == original


@pytest.mark.parametrize(
    "summary",
    [
        "2026-09-06T20:09:27+00:00，用户创建了喝水提醒。",
        "用户引用：" + receipt("2026-09-06T20:09:27+00:00"),
        receipt("2026-09-06 20:09:27"),
        receipt("2026-09-06T20:09:27"),
        receipt("2026-13-06T20:09:27+00:00"),
        receipt("2026-09-06T25:09:27+00:00"),
        receipt("2026-09-06T20:09:27+00:00").replace("我通过桌宠气泡", "用户通过邮件"),
        receipt("2026-09-06T20:09:27+00:00").replace('"回来看结果吧。"', "未引用的文字"),
        receipt("2026-09-06T20:09:27+00:00").replace('"回来看结果吧。"', '"非法\\q转义"'),
        receipt("2026-09-06T20:09:27+00:00") + "这证明任务完成。",
        receipt("2026-09-06T20:09:27+00:00") + "\n",
    ],
)
def test_unknown_templates_or_invalid_timestamps_remain_verbatim(
    monkeypatch: pytest.MonkeyPatch, summary: str
) -> None:
    def unexpected(_: datetime) -> str:
        pytest.fail("unknown templates must not reach time conversion")

    monkeypatch.setattr(presentation, "format_notification_time", unexpected)
    assert project(summary) == summary
