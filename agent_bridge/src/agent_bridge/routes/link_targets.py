"""Read-only UI link resolution; never invokes a model or opens an application."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from agent.command_runtime.workspace import session_workspace_path
from agent.paths import command_workspaces_dir
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent import AttachmentError, SessionNotFoundError

from ..assembly import BridgeRuntime


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target: str | None = Field(default=None, max_length=16_384)
    asset_id: str | None = Field(default=None, alias="assetId", pattern=r"^[0-9a-f]{64}$")
    draft_id: str | None = Field(default=None, alias="draftId", max_length=256)
    context: Literal["session", "persona"]
    session_id: str | None = Field(default=None, alias="sessionId", max_length=256)

    @model_validator(mode="after")
    def one_target(self) -> LinkRequest:
        if sum(value is not None for value in (self.target, self.asset_id, self.draft_id)) != 1:
            raise ValueError("exactly one link target is required")
        return self


def unavailable(value: str, reason: str) -> dict[str, str]:
    return {"kind": "unavailable", "value": value, "reason": reason}


def local_path(value: str, base: Path | None) -> Path:
    """Decode URI syntax once; literal filesystem paths keep percent/hash characters."""
    raw = value.strip()
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("文件路径为空或包含无效字符")
    if raw.lower().startswith("file:"):
        url = urlsplit(raw)
        if any(char in url.netloc for char in "@:\\"):
            raise ValueError("文件链接主机格式无效")
        if url.query:
            raise ValueError("文件链接不能带查询参数")
        if re.search(r"%(?![0-9a-fA-F]{2})", url.path):
            raise ValueError("文件链接编码无效")
        raw = unquote(url.path, errors="strict")
        if url.netloc and url.netloc.lower() != "localhost":
            if os.name != "nt":
                raise ValueError("当前系统无法定位此共享路径")
            raw = "\\\\" + url.netloc + raw.replace("/", "\\")
        elif os.name == "nt" and re.match(r"^/[a-zA-Z]:/", raw):
            raw = raw[1:]
        if url.fragment and not re.fullmatch(r"L[1-9]\d*(?:C[1-9]\d*)?", url.fragment):
            raise ValueError("文件链接锚点无效；文件名中的井号请使用 %23")
        if not raw or "\x00" in raw:
            raise ValueError("文件链接路径无效")
    elif raw.startswith("//"):
        raise ValueError("无法确定这是网址还是共享路径")
    elif re.match(r"^[a-zA-Z][a-zA-Z\d+.-]*:", raw) and not re.match(r"^[a-zA-Z]:[\\/]", raw):
        raise ValueError("当前不支持此文件链接类型")
    if raw.startswith("~"):
        raise ValueError("请使用完整文件路径")
    windows_path = bool(re.match(r"^[a-zA-Z]:[\\/]", raw) or raw.startswith("\\\\"))
    if os.name != "nt" and windows_path:
        raise ValueError("此 Windows 路径无法在当前系统定位")
    if os.name == "nt" and raw.startswith("/"):
        raise ValueError("此路径缺少 Windows 盘符或属于其它系统")
    if os.name == "nt" and raw.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("不支持设备路径，请使用普通文件路径")
    if os.name == "nt" and raw.startswith("\\") and not raw.startswith("\\\\"):
        raise ValueError("文件路径缺少盘符")
    path = Path(raw)
    if not path.is_absolute():
        if base is None:
            raise ValueError("缺少此链接的目录信息，请使用完整文件路径")
        path = base / path
    return path


def inspect_path(path: Path, *, allow_url_encoding: bool = False) -> dict[str, str]:
    # Exact names win over suffix interpretation (e.g. POSIX filename 'notes:12').
    candidates = [path]
    stripped = re.sub(r"(?::[1-9]\d*(?::[1-9]\d*)?|#L[1-9]\d*(?:C[1-9]\d*)?)$", "", str(path))
    if stripped != str(path):
        candidates.append(Path(stripped))
    if allow_url_encoding:
        # Explicit Markdown destinations commonly encode spaces. Preserve exact literal
        # filenames first; never decode file URI paths a second time.
        for candidate in tuple(candidates):
            try:
                decoded = Path(unquote(str(candidate), errors="strict"))
            except UnicodeError:
                continue
            if decoded not in candidates:
                candidates.append(decoded)
    for candidate in candidates:
        try:
            candidate.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            # Windows rejects ':line:column' before existence lookup; only a recognized
            # reference suffix may recover from invalid-name, never permission/I/O errors.
            if (
                getattr(exc, "winerror", None) == 123
                and re.search(r":[1-9]\d*(?::[1-9]\d*)?$", str(candidate))
                and len(candidates) > 1
            ):
                continue
            raise
        if candidate.is_dir():
            return {"kind": "directory", "value": str(candidate.absolute())}
        if candidate.is_file():
            return {"kind": "file", "value": str(candidate.absolute())}
        raise ValueError("此目标不是普通文件或文件夹")
    return unavailable(str(path), "文件或文件夹不存在，可能已被移动或删除")


def resolve_target(runtime: BridgeRuntime, request: LinkRequest) -> dict[str, str]:
    value = request.target or ""
    try:
        base: Path | None = None
        session = None
        if request.context == "session" and request.session_id:
            session = runtime.persistent_store.load(request.session_id)
            base = session_workspace_path(command_workspaces_dir(), request.session_id)
        elif request.context == "persona" and runtime.persona_store is not None:
            base = runtime.persona_store.user_file.parent
        if request.asset_id or request.draft_id:
            store = runtime.attachment_store
            if store is None:
                return unavailable("", "附件服务暂不可用")
            if request.draft_id:
                ref = store.draft(request.draft_id)
                path = store.verify(ref.asset_id)
            else:
                if session is None or not any(
                    isinstance(item, dict) and item.get("asset_id") == request.asset_id
                    for event in session.active_events
                    if event.type == "user_message"
                    or (event.type == "assistant_message" and "generated_image" in event.payload)
                    for item in event.payload.get("attachments", []) or []
                ):
                    return unavailable("", "此会话没有该附件的有效引用")
                assert request.asset_id is not None
                path = store.verify(request.asset_id)
            return inspect_path(path)
        assert request.target is not None
        return inspect_path(
            local_path(request.target, base),
            allow_url_encoding=not request.target.lower().startswith("file:"),
        )
    except SessionNotFoundError:
        return unavailable(value, "链接所属会话已不存在")
    except AttachmentError:
        return unavailable("", "附件副本已失效或不可用")
    except PermissionError:
        return unavailable(value, "没有访问此文件或文件夹的权限")
    except ValueError as exc:
        return unavailable(value, str(exc))
    except OSError:
        return unavailable(value, "无法访问文件路径，请检查文件位置或共享连接")


def register_routes(app: FastAPI, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/link-targets")

    @router.post("/resolve")
    def resolve_link(request: LinkRequest) -> dict[str, str]:
        return resolve_target(runtime, request)

    app.include_router(router)
