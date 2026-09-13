from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, cast

from openai import OpenAI

from ..catalog import (
    DEEPSEEK_VISION_LITELLM_MODEL,
    model_capabilities,
    model_processing_profile,
)
from ..client import LLMClient
from ..errors import LLMBadRequestError, LLMProviderError
from ..protocol import LanguageModel
from .tool_images import supports_tool_images

OpenAsset = Callable[[str], BinaryIO]

DEEPSEEK_FILE_TTL_SECONDS = 30 * 24 * 60 * 60
DEEPSEEK_FILE_EXPIRY_MARGIN_SECONDS = 5 * 60


class MultimodalInputAdapter:
    """Render attachment-bearing domain messages into provider-native blocks."""

    def __init__(self, *, open_asset: OpenAsset, cache_dir: Path) -> None:
        self._open_asset = open_asset
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def render_message(self, message: Any, client: LanguageModel) -> dict[str, Any]:
        if getattr(message, "images", ()):
            return self._render_tool_images(message, client)
        attachments = list(getattr(message, "attachments", []) or [])
        if not attachments or getattr(message, "role", None) != "user":
            return cast(dict[str, Any], message.to_openai())

        concrete_client = self._concrete_client(client)
        model_id = self._canonical_model_id(concrete_client)
        capabilities = concrete_client.spec.attachment_capabilities or model_capabilities(model_id)
        if not capabilities.attachments:
            if getattr(message, "meta", {}).get("current_input"):
                raise LLMBadRequestError("attachment_model_unsupported: 当前模型不支持附件。")
            markers = "\n".join(
                f"[此前用户附带{item.kind} {item.display_name}；当前模型无法直接读取]"
                for item in attachments
            )
            text = str(getattr(message, "content", "") or "")
            return {"role": "user", "content": f"{text}\n{markers}".strip()}
        if len(attachments) > capabilities.max_attachments_per_turn:
            raise LLMBadRequestError("attachment_turn_limit_exceeded: 单轮附件数量超过限制。")
        sizes = [int(getattr(item, "size_bytes", 0)) for item in attachments]
        if any(size > capabilities.max_attachment_bytes for size in sizes):
            raise LLMBadRequestError("attachment_size_limit_exceeded: 单个附件大小超过限制。")
        if sum(sizes) > capabilities.max_total_attachment_bytes:
            raise LLMBadRequestError("attachment_total_limit_exceeded: 本轮附件总大小超过限制。")

        processing_profile = concrete_client.spec.attachment_profile or model_processing_profile(
            model_id
        )
        if processing_profile not in (
            "kimi-multimodal-v1",
            "deepseek-vision-files-v1",
            "openai-attachments-v1",
        ):
            raise LLMBadRequestError("attachment_model_unsupported: 当前模型路由没有附件适配器。")

        content: list[dict[str, Any]] = []
        text = str(getattr(message, "content", "") or "")
        if text:
            content.append({"type": "text", "text": text})
        for attachment in attachments:
            if attachment.media_type not in capabilities.accepted_media_types:
                raise LLMBadRequestError("attachment_type_unsupported: 当前模型不支持此附件类型。")
            if processing_profile == "openai-attachments-v1":
                with self._open_asset(attachment.asset_id) as source:
                    data = source.read(capabilities.max_attachment_bytes + 1)
                if len(data) > capabilities.max_attachment_bytes:
                    raise LLMBadRequestError("attachment_size_limit_exceeded")
                if attachment.media_type == "text/plain":
                    try:
                        document = data.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        raise LLMBadRequestError(
                            "attachment_text_encoding: TXT 需要 UTF-8 编码。"
                        ) from None
                    content.append(
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"attachment": attachment.display_name, "content": document},
                                ensure_ascii=False,
                            ),
                        }
                    )
                else:
                    encoded = self._data_url(attachment.media_type, data)
                    if attachment.kind == "image":
                        content.append({"type": "image_url", "image_url": {"url": encoded}})
                    else:
                        content.append(
                            {
                                "type": "file",
                                "file": {"filename": attachment.display_name, "file_data": encoded},
                            }
                        )
                continue
            if processing_profile == "deepseek-vision-files-v1":
                if attachment.kind != "image":
                    raise LLMBadRequestError(
                        "attachment_type_unsupported: DeepSeek 视觉模型仅支持图片附件。"
                    )
                prepared = self._prepare_deepseek(attachment, concrete_client)
                content.append({"type": "file", "file": {"file_id": prepared["_file_id"]}})
                continue

            prepared = self._prepare_kimi(attachment, concrete_client)
            if attachment.kind == "image":
                content.append({"type": "image_url", "image_url": {"url": prepared["url"]}})
            else:
                if int(len(prepared["content"]) * 0.75) > int(concrete_client.context_window * 0.6):
                    raise LLMBadRequestError(
                        "attachment_context_too_large: 附件抽取内容超过当前模型的上下文容量。"
                    )
                content.append(
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "source_type": "attachment",
                                "name": attachment.display_name,
                                "media_type": attachment.media_type,
                                "content": prepared["content"],
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
        return {"role": "user", "content": content}

    @staticmethod
    def _data_url(media_type: str, data: bytes) -> str:
        return f"data:{media_type};base64," + base64.b64encode(data).decode("ascii")

    def _render_tool_images(self, message: Any, client: LanguageModel) -> dict[str, Any]:
        if message.role != "tool":
            raise LLMBadRequestError("image_source_invalid: images require tool origin")
        result = {"role": "tool", "tool_call_id": message.meta.get("tool_call_id", "")}
        text = str(message.content or "")
        if not supports_tool_images(client):
            return {
                **result,
                "content": text
                + "\n[工具图像当前未提供：当前模型路由不支持图像工具结果，不能推断原图内容]",
            }
        concrete = self._concrete_client(client)
        caps = concrete.spec.attachment_capabilities or model_capabilities(
            self._canonical_model_id(concrete)
        )
        images = message.images
        if len(images) > min(4, caps.max_attachments_per_turn):
            raise LLMBadRequestError("image_count_exceeded: too many tool images")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        limit = min(8 * 1024 * 1024, caps.max_attachment_bytes)
        if sum(item.size_bytes for item in images) > caps.max_total_attachment_bytes:
            raise LLMBadRequestError("image_total_size_exceeded")
        for ref in images:
            if ref.kind != "image" or ref.media_type not in caps.accepted_media_types:
                content.append({"type": "text", "text": "[工具图像未提供：当前模型不支持此格式]"})
                continue
            if not 0 < ref.size_bytes <= limit:
                content.append({"type": "text", "text": "[工具图像未提供：大小超过当前模型限制]"})
                continue
            try:
                with self._open_asset(ref.asset_id) as source:
                    data = source.read(limit + 1)
                if len(data) != ref.size_bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
                    raise ValueError("image_integrity_failed")
            except (OSError, ValueError):
                content.append(
                    {
                        "type": "text",
                        "text": "[工具图像未提供：原资产缺失或损坏，请重新读取有效图片]",
                    }
                )
                continue
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._data_url(ref.media_type, data),
                        "detail": "auto",
                    },
                }
            )
        return {**result, "content": content}

    @staticmethod
    def _canonical_model_id(client: LLMClient) -> str:
        model = client.spec.model
        if model == DEEPSEEK_VISION_LITELLM_MODEL:
            return "deepseek/deepseek-flash"
        if model.startswith("openai/kimi-"):
            return "kimi/" + model.removeprefix("openai/")
        return model

    @staticmethod
    def _concrete_client(client: LanguageModel) -> LLMClient:
        if not isinstance(client, LLMClient):
            raise LLMBadRequestError("attachment_model_unsupported: 当前模型路由没有附件适配器。")
        return client

    def _prepare_kimi(self, attachment: Any, client: LLMClient) -> dict[str, str]:
        account_scope = hashlib.sha256(client.spec.api_key.encode("utf-8")).hexdigest()[:16]
        purpose = "image" if attachment.kind == "image" else "file-extract"
        key = hashlib.sha256(
            f"{attachment.asset_id}|kimi|{account_scope}|openai-compatible|kimi-multimodal-v1|{purpose}".encode()
        ).hexdigest()
        path = self._cache_dir / f"{key}.json"
        lock = self._lock_for(key)
        with lock:
            cached = self._read_cache(path)
            if cached is not None:
                if attachment.kind != "image":
                    return cached
                remote_file_id = cached.get("_file_id")
                if remote_file_id:
                    try:
                        sdk = OpenAI(api_key=client.spec.api_key, base_url=client.spec.api_base)
                        cached_files_api: Any = sdk.files
                        cached_files_api.retrieve(remote_file_id)
                        return cached
                    except Exception:
                        path.unlink(missing_ok=True)
                else:
                    path.unlink(missing_ok=True)
            try:
                sdk = OpenAI(api_key=client.spec.api_key, base_url=client.spec.api_base)
                with self._open_asset(attachment.asset_id) as source:
                    files_api: Any = sdk.files
                    uploaded = files_api.create(
                        file=(attachment.display_name, source, attachment.media_type),
                        purpose=purpose,
                    )
                if attachment.kind == "image":
                    result = {"url": f"ms://{uploaded.id}", "_file_id": str(uploaded.id)}
                else:
                    response = files_api.content(uploaded.id)
                    extracted = getattr(response, "text", None)
                    if not isinstance(extracted, str):
                        raw = getattr(response, "content", b"")
                        if isinstance(raw, bytes):
                            extracted = raw.decode("utf-8", errors="replace")
                        else:
                            extracted = str(raw)
                    result = {"content": extracted}
            except Exception as exc:
                raise LLMProviderError(
                    "attachment_provider_upload_failed: 附件交给模型处理失败。"
                ) from exc
            self._write_cache(path, {**result, "_asset_id": attachment.asset_id})
            return result

    def _prepare_deepseek(self, attachment: Any, client: LLMClient) -> dict[str, str]:
        account_scope = hashlib.sha256(client.spec.api_key.encode("utf-8")).hexdigest()[:16]
        key = hashlib.sha256(
            (
                f"{attachment.asset_id}|deepseek|{account_scope}|openai-compatible|"
                "deepseek-vision-files-v1|user_data"
            ).encode()
        ).hexdigest()
        path = self._cache_dir / f"{key}.json"
        lock = self._lock_for(key)
        with lock:
            cached = self._read_cache(path)
            if cached is not None:
                file_id = cached.get("_file_id")
                try:
                    expires_at = float(cached.get("_expires_at", ""))
                except (TypeError, ValueError):
                    expires_at = 0
                if file_id and expires_at - time.time() > DEEPSEEK_FILE_EXPIRY_MARGIN_SECONDS:
                    return cached
                path.unlink(missing_ok=True)

            try:
                sdk = OpenAI(api_key=client.spec.api_key, base_url=client.spec.api_base)
                with self._open_asset(attachment.asset_id) as source:
                    files_api: Any = sdk.files
                    uploaded = files_api.create(
                        file=(attachment.display_name, source, attachment.media_type),
                        purpose="user_data",
                        expires_after={
                            "anchor": "created_at",
                            "seconds": DEEPSEEK_FILE_TTL_SECONDS,
                        },
                    )
                uploaded_expires_at = getattr(uploaded, "expires_at", None)
                try:
                    if not isinstance(uploaded_expires_at, (int, float, str)):
                        raise TypeError
                    expires_at = float(uploaded_expires_at)
                except (TypeError, ValueError):
                    expires_at = time.time() + DEEPSEEK_FILE_TTL_SECONDS
                if expires_at <= time.time():
                    expires_at = time.time() + DEEPSEEK_FILE_TTL_SECONDS
                result = {
                    "_file_id": str(uploaded.id),
                    "_expires_at": str(int(expires_at)),
                }
            except Exception as exc:
                raise LLMProviderError(
                    "attachment_provider_upload_failed: 附件交给模型处理失败。"
                ) from exc
            self._write_cache(path, {**result, "_asset_id": attachment.asset_id})
            return result

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    @staticmethod
    def _read_cache(path: Path) -> dict[str, str] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            return value
        return None

    @staticmethod
    def _write_cache(path: Path, value: dict[str, str]) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
