"""``ProviderSpec``：把"切换 Provider 时需要变的东西"显式收纳。

切换 Provider = 改一个 spec 实例的字段，不需要动 ``LLMClient`` 内部代码。

详见 docs/requirements/001-foundation-chat-and-memory/design.md §4.1.1。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .errors import LLMAuthError

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "DEEPSEEK": {
        # 注意：旧模型 ``deepseek-chat`` / ``deepseek-reasoner`` 将于
        # 2026/07/24 弃用，已改为指向 V4 系列的高性价比款 ``v4-flash``。
        # 切换到旗舰款用 ``deepseek/deepseek-v4-pro``，详见 .env.example。
        "model": "deepseek/deepseek-v4-flash",
        # 记忆抽取是旁路质量任务，默认用更稳的旗舰款；主对话仍保持 v4-flash。
        "memory_model": "deepseek/deepseek-v4-pro",
        "api_base": None,
    },
}


@dataclass(frozen=True)
class ProviderConfig:
    """Provider credentials and optional non-secret overrides from runtime config."""

    api_key: str
    model: str | None = None
    api_base: str | None = None
    context_window: int | None = None


@dataclass(frozen=True)
class ProviderSpec:
    """单个 LLM Provider 的完整调用配置。

    Attributes:
        model: LiteLLM 风格的模型名，必须带 provider 前缀，例如
            ``"deepseek/deepseek-chat"``。
        api_key: 该 Provider 的 API key。
        api_base: 自定义 API 端点；用于代理或自建场景，``None`` 表示用
            Provider 默认地址。
        defaults: 默认调用参数（``temperature`` / ``max_tokens`` 等），
            可被单次调用的 ``overrides`` 覆盖。
        context_window: 可选的 context window（最大输入 token）覆盖值（009 起新增）。
            ``None``（默认）时由 :attr:`LLMClient.context_window` 走 litellm 元数据
            查询 + 保守兜底。仅当 litellm 不认识该 model（私有 ``api_base`` / 自建
            网关）时才需显式设置。
    """

    model: str
    api_key: str
    api_base: str | None = None
    defaults: dict[str, Any] = field(default_factory=dict)
    context_window: int | None = None

    @classmethod
    def from_config(
        cls,
        prefix: str,
        config: ProviderConfig,
        *,
        default_model_key: str = "model",
    ) -> ProviderSpec:
        """从显式 runtime config 构造 ``ProviderSpec``。

        ``prefix`` 仅用于读取 :data:`PROVIDER_DEFAULTS` 和错误文案；配置来源
        （env / keyring / legacy dotenv / future managed source）由调用方负责。
        """
        api_key = config.api_key.strip()
        if not api_key:
            raise LLMAuthError(f"{prefix}_API_KEY 未配置。请先配置 provider credential。")

        provider_defaults = PROVIDER_DEFAULTS.get(prefix, {})
        model = config.model or provider_defaults.get(default_model_key)
        if not model:
            raise ValueError(
                f"无法确定 model：请在 runtime config 中设置 {prefix}_MODEL，"
                f"或在 PROVIDER_DEFAULTS 中为 {prefix} 添加 {default_model_key} 默认值。"
            )

        api_base = config.api_base or provider_defaults.get("api_base")

        return cls(
            model=model,
            api_key=api_key,
            api_base=api_base,
            context_window=config.context_window,
        )

    @classmethod
    def from_env(
        cls,
        prefix: str,
        *,
        model_env_var: str | None = None,
        default_model_key: str = "model",
    ) -> ProviderSpec:
        """从环境变量构造 ``ProviderSpec``。

        约定的环境变量名（以 ``prefix="DEEPSEEK"`` 为例）：

        - ``DEEPSEEK_API_KEY``（必填）
        - ``DEEPSEEK_MODEL``（可选，缺省按 :data:`PROVIDER_DEFAULTS`）
        - ``DEEPSEEK_API_BASE``（可选）
        - ``DEEPSEEK_CONTEXT_WINDOW``（可选，009 起；litellm 不认识私有 model 时显式指定）

        Args:
            prefix: 环境变量前缀，按惯例使用 Provider 名称大写。
            model_env_var: 可选的 model 环境变量名。默认读 ``{prefix}_MODEL``；
                记忆抽取等旁路可传 ``DEEPSEEK_MEMORY_MODEL`` 做独立选择。
            default_model_key: 环境变量未设置时读取 :data:`PROVIDER_DEFAULTS`
                里的哪个默认模型键。默认 ``"model"``。

        Raises:
            LLMAuthError: ``{prefix}_API_KEY`` 未设置或为空。
        """
        provider_defaults = PROVIDER_DEFAULTS.get(prefix, {})
        model_env_name = model_env_var or f"{prefix}_MODEL"

        context_window: int | None = None
        raw_window = os.environ.get(f"{prefix}_CONTEXT_WINDOW", "").strip()
        if raw_window:
            try:
                parsed = int(raw_window)
                if parsed > 0:
                    context_window = parsed
            except ValueError:
                pass  # 非法值忽略，回落到 litellm 查询

        return cls.from_config(
            prefix,
            ProviderConfig(
                api_key=os.environ.get(f"{prefix}_API_KEY", ""),
                model=os.environ.get(model_env_name) or provider_defaults.get(default_model_key),
                api_base=os.environ.get(f"{prefix}_API_BASE") or provider_defaults.get("api_base"),
                context_window=context_window,
            ),
            default_model_key=default_model_key,
        )
