"""agent-friend · 记忆召回质量评测（PoC）。

把外部 agent-memory 基准（首个：LoCoMo）的对话历史灌进 ``memory`` 的公共接口
（``observe``），再用基准问题走 ``retrieve``，观察召回质量。

本包是 ``memory`` 的**消费方**，单向依赖 ``memory`` + ``llm_providers``，详见 README。
"""

from __future__ import annotations

__version__ = "0.1.0"
