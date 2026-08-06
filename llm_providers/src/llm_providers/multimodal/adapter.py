from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, cast

from openai import OpenAI

from ..catalog import infer_provider_id_from_model, model_capabilities
from ..client import LLMClient
from ..errors import LLMBadRequestError, LLMProviderError

OpenAsset = Callable[[str], BinaryIO]


class MultimodalInputAdapter:
    """Render attachment-bearing domain messages into provider-native blocks."""

    def __init__(self, *, open_asset: OpenAsset, cache_dir: Path) -> None:
        self._open_asset = open_asset
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def render_message(self, message: Any, client: LLMClient) -> dict[str, Any]:
        attachments = list(getattr(message, "attachments", []) or [])
        if not attachments or getattr(message, "role", None) != "user":
            return cast(dict[str, Any], message.to_openai())

        model_id = self._canonical_model_id(client)
        capabilities = model_capabilities(model_id)
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

        provider_id = infer_provider_id_from_model(model_id)
        if provider_id != "kimi":
            raise LLMBadRequestError("attachment_model_unsupported: 当前模型路由没有附件适配器。")

        content: list[dict[str, Any]] = []
        text = str(getattr(message, "content", "") or "")
        if text:
            content.append({"type": "text", "text": text})
        for attachment in attachments:
            if attachment.media_type not in capabilities.accepted_media_types:
                raise LLMBadRequestError("attachment_type_unsupported: 当前模型不支持此附件类型。")
            prepared = self._prepare_kimi(attachment, client)
            if attachment.kind == "image":
                content.append({"type": "image_url", "image_url": {"url": prepared["url"]}})
            else:
                if int(len(prepared["content"]) * 0.75) > int(client.context_window * 0.6):
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
    def _canonical_model_id(client: LLMClient) -> str:
        model = client.spec.model
        if model.startswith("openai/kimi-"):
            return "kimi/" + model.removeprefix("openai/")
        return model

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
