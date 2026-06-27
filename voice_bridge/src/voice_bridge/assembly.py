"""voice_bridge 进程级装配。

把所有依赖（settings / 火山 RTC 客户端 / call registry / agent_bridge HTTP 客户端）
在进程启动时构造一次，所有 HTTP 请求共享同一份。

详见 docs/requirements/007-voice-call/design.md §4.2.2。
"""

from __future__ import annotations

from dataclasses import dataclass

from .asr import AsrProvider
from .asr.volc import VolcAsrProvider
from .calls import CallRegistry
from .clients import AgentBridgeClient
from .rtc import VolcRtcClient
from .settings import VoiceBridgeSettings


@dataclass(frozen=True)
class VoiceBridgeRuntime:
    """voice_bridge 进程级共享运行时。"""

    settings: VoiceBridgeSettings
    rtc_client: VolcRtcClient
    call_registry: CallRegistry
    agent_bridge: AgentBridgeClient
    asr_provider: AsrProvider | None = None


def build_runtime(settings: VoiceBridgeSettings) -> VoiceBridgeRuntime:
    """进程启动期一次性装配 :class:`VoiceBridgeRuntime`。"""
    rtc_client = VolcRtcClient(
        access_key=settings.volc_access_key,
        secret_key=settings.volc_secret_key,
    )
    call_registry = CallRegistry()
    agent_bridge = AgentBridgeClient(settings.agent_bridge_url)
    asr_provider = VolcAsrProvider(settings)
    return VoiceBridgeRuntime(
        settings=settings,
        rtc_client=rtc_client,
        call_registry=call_registry,
        agent_bridge=agent_bridge,
        asr_provider=asr_provider,
    )
