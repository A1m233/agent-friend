"""把 AG-UI ``RunAgentInput`` 解码为 bridge 内部输入。

AG-UI 的 :class:`ag_ui.core.RunAgentInput` 已经是 pydantic 模型——FastAPI 直接
作为请求体类型解析。这里只承担**最后一步**：从已解析的 ``messages`` 数组里取
出本轮 user 新输入。

设计取舍（详见 design §4.4.2）：

- AG-UI 假设服务端已通过 ``thread_id`` 落盘了之前所有历史，``messages`` 数组
  里的 ``user`` / ``assistant`` 历史**与服务端 jsonl 是同一份事件流的不同视图**
- 本期不去对照"客户端送的 history 和 jsonl 落盘 history 是否一致"——后者是
  source of truth；客户端 history 仅用来识别最后一条 user 的 ``content``
- 不允许 ``messages`` 为空或末位不是 user / 不允许 ``thread_id`` / ``run_id`` 缺失
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from ag_ui.core import (
    DocumentInputContent,
    ImageInputContent,
    RunAgentInput,
    TextInputContent,
    UserMessage,
)
from agent.approvals import SessionApprovalMode
from agent.skills import SkillSelection, decode_skill_selections

from llm_providers import (
    ModelRuntimeConfig,
    all_model_ids,
    default_model_runtime_config,
    model_belongs_to_provider,
    normalize_provider_id,
    validate_model_runtime_config,
)


class DecodeError(ValueError):
    """AG-UI ``RunAgentInput`` 不符合 bridge 接受的最小约束。"""


@dataclass(frozen=True)
class DecodedRequest:
    """AG-UI request 解码结果。

    Attributes:
        thread_id: AG-UI 协议 ``thread_id``；bridge 直接当 ``session_id``。
        run_id: AG-UI 协议 ``run_id``；本轮 ``RUN_STARTED`` / ``RUN_FINISHED``
            事件回填。
        new_user_input: 最后一条 ``UserMessage.content`` 的文本。
        requested_model: 桌面设置中心传入的新会话默认模型。已有 session 不会被该值覆盖。
    """

    thread_id: str
    run_id: str
    new_user_input: str
    user_message_id: str
    draft_attachments: tuple[DecodedDraftAttachment, ...] = ()
    requested_model_config: ModelRuntimeConfig | None = None
    initial_approval_mode: SessionApprovalMode | None = None
    skills: tuple[SkillSelection, ...] = ()

    @property
    def requested_model(self) -> str | None:
        config = self.requested_model_config
        return config.model_id if config is not None else None


@dataclass(frozen=True)
class DecodedDraftAttachment:
    draft_id: str
    kind: Literal["image", "document"]


_DRAFT_CONTENT_PATH = re.compile(r"^/v1/attachments/drafts/([A-Za-z0-9_-]{16,80})/content$")


def _draft_id_from_source(part: ImageInputContent | DocumentInputContent) -> str:
    source = part.source
    if getattr(source, "type", None) != "url":
        raise DecodeError("附件只接受本机托管 URL，不接受内联 data")
    raw = str(getattr(source, "value", ""))
    parsed = urlparse(raw)
    if (parsed.scheme or parsed.netloc) and (
        parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
    ):
        raise DecodeError("附件 URL 必须由本机 agent-friend Bridge 签发")
    match = _DRAFT_CONTENT_PATH.fullmatch(parsed.path)
    if match is None:
        raise DecodeError("附件 URL 不是有效的本机草稿地址")
    return match.group(1)


def resolve_requested_model_config(payload: RunAgentInput) -> ModelRuntimeConfig | None:
    """Resolve optional desktop-requested model from ``forwarded_props``.

    The canonical wire value is a model id string such as
    ``deepseek/deepseek-v4-flash`` or ``kimi/kimi-k2.6``. A provider-aware object
    shape is also accepted so newer clients do not need to rely on prefixes.
    """

    forwarded_props: Any = getattr(payload, "forwarded_props", None) or {}
    if not isinstance(forwarded_props, dict):
        return None

    raw_config = forwarded_props.get("modelConfig")
    if raw_config not in (None, ""):
        if not isinstance(raw_config, dict):
            raise DecodeError("forwarded_props.modelConfig 必须是对象")
        model = raw_config.get("model")
        preset = raw_config.get("reasoningPreset")
        if not isinstance(model, str) or not isinstance(preset, str):
            raise DecodeError("forwarded_props.modelConfig model/reasoningPreset 必须是字符串")
        try:
            return validate_model_runtime_config(ModelRuntimeConfig(model.strip(), preset.strip()))
        except ValueError as exc:
            raise DecodeError(str(exc)) from exc

    raw = forwarded_props.get("model")
    if raw in (None, ""):
        return None

    if isinstance(raw, str):
        model = raw.strip()
        if model not in all_model_ids():
            raise DecodeError(f"不支持的桌面模型：{model}")
        return default_model_runtime_config(model)

    if isinstance(raw, dict):
        provider_raw = raw.get("provider")
        model_raw = raw.get("model")
        if not isinstance(provider_raw, str) or not isinstance(model_raw, str):
            raise DecodeError("forwarded_props.model provider/model 必须是字符串")
        provider_id = normalize_provider_id(provider_raw)
        model = model_raw.strip()
        if not model_belongs_to_provider(provider_id, model):
            raise DecodeError(f"不支持的桌面模型：{model}")
        return default_model_runtime_config(model)

    raise DecodeError("forwarded_props.model 必须是字符串或 provider/model 对象")


def resolve_requested_model(payload: RunAgentInput) -> str | None:
    """Compatibility projection for older decoder callers."""

    config = resolve_requested_model_config(payload)
    return config.model_id if config is not None else None


def resolve_initial_approval_mode(payload: RunAgentInput) -> SessionApprovalMode | None:
    """Decode the risk-acknowledged approval mode for a not-yet-created session."""

    forwarded_props: Any = getattr(payload, "forwarded_props", None) or {}
    if not isinstance(forwarded_props, dict):
        return None
    raw = forwarded_props.get("initialApprovalMode")
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise DecodeError("forwarded_props.initialApprovalMode 必须是对象")
    if raw.get("mode") != "session_auto":
        raise DecodeError("forwarded_props.initialApprovalMode.mode 只接受 session_auto")
    if raw.get("riskAcknowledged") is not True:
        raise DecodeError("进入本会话无需授权前必须重新确认风险")
    return "session_auto"


def decode_run_agent_input(payload: RunAgentInput) -> DecodedRequest:
    """从 :class:`RunAgentInput` 抽出 bridge 真正需要的三个字段。

    Raises:
        DecodeError: ``thread_id`` / ``run_id`` 缺失或为空；``messages``
            为空或末位不是 :class:`UserMessage`。
    """
    if not payload.thread_id:
        raise DecodeError("thread_id 不能为空")
    if not payload.run_id:
        raise DecodeError("run_id 不能为空")
    if not payload.messages:
        raise DecodeError("messages 不能为空")

    last = payload.messages[-1]
    if not isinstance(last, UserMessage):
        raise DecodeError(f"messages 最后一条必须是 role=user，收到 {type(last).__name__}")

    content = last.content if isinstance(last.content, str) else ""
    drafts: list[DecodedDraftAttachment] = []
    if isinstance(last.content, list):
        text_parts: list[str] = []
        for part in last.content:
            if isinstance(part, TextInputContent):
                text_parts.append(part.text)
            elif isinstance(part, ImageInputContent):
                drafts.append(DecodedDraftAttachment(_draft_id_from_source(part), "image"))
            elif isinstance(part, DocumentInputContent):
                drafts.append(DecodedDraftAttachment(_draft_id_from_source(part), "document"))
            else:
                raise DecodeError(f"暂不支持 AG-UI 内容类型：{getattr(part, 'type', 'unknown')}")
        content = "\n".join(item for item in text_parts if item)
    if not content.strip() and not drafts:
        raise DecodeError("messages 最后一条 user content 不能为空")
    if len({item.draft_id for item in drafts}) != len(drafts):
        raise DecodeError("同一个附件草稿不能重复发送")
    forwarded_props: Any = getattr(payload, "forwarded_props", None) or {}
    try:
        skills = decode_skill_selections(
            forwarded_props.get("skills") if isinstance(forwarded_props, dict) else None
        )
    except ValueError as exc:
        raise DecodeError(f"forwarded_props.{exc}") from exc
    return DecodedRequest(
        thread_id=payload.thread_id,
        run_id=payload.run_id,
        new_user_input=content,
        user_message_id=last.id,
        draft_attachments=tuple(drafts),
        requested_model_config=resolve_requested_model_config(payload),
        initial_approval_mode=resolve_initial_approval_mode(payload),
        skills=skills,
    )
