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
        "api_base": None,
    },
}


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
    """

    model: str
    api_key: str
    api_base: str | None = None
    defaults: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, prefix: str) -> ProviderSpec:
        """从环境变量构造 ``ProviderSpec``。

        约定的环境变量名（以 ``prefix="DEEPSEEK"`` 为例）：

        - ``DEEPSEEK_API_KEY``（必填）
        - ``DEEPSEEK_MODEL``（可选，缺省按 :data:`PROVIDER_DEFAULTS`）
        - ``DEEPSEEK_API_BASE``（可选）

        Args:
            prefix: 环境变量前缀，按惯例使用 Provider 名称大写。

        Raises:
            LLMAuthError: ``{prefix}_API_KEY`` 未设置或为空。
        """
        api_key = os.environ.get(f"{prefix}_API_KEY", "").strip()
        if not api_key:
            raise LLMAuthError(f"环境变量 {prefix}_API_KEY 未设置。请在项目根 .env 中配置。")

        provider_defaults = PROVIDER_DEFAULTS.get(prefix, {})
        model = os.environ.get(f"{prefix}_MODEL") or provider_defaults.get("model")
        if not model:
            raise ValueError(
                f"无法确定 model：请设置环境变量 {prefix}_MODEL，"
                f"或在 PROVIDER_DEFAULTS 中为 {prefix} 添加默认值。"
            )

        api_base = os.environ.get(f"{prefix}_API_BASE") or provider_defaults.get("api_base")

        return cls(model=model, api_key=api_key, api_base=api_base)
