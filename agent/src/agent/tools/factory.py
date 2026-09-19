"""``make_default_registry``：根据当前环境配置组装默认 :class:`ToolRegistry`。

未来加新 provider / 新工具时改这里**一处**——挑选逻辑集中，``WebSearchTool`` /
:class:`Tool` Protocol / 引擎调用循环 / CLI 全部 0 改动。

详见:

- docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7.5（``web_search``）
- docs/requirements/020-engine-tool-conversation-history/design.md §4.5（``conversation_history``）
"""

from __future__ import annotations

import atexit
import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from memory.search import MemorySearchService, QueryAnalyzer

from ..memory_search import SessionHistorySource
from .builtin.calculate import CalculateTool
from .builtin.conversation_history import ConversationHistoryTool
from .builtin.datetime_calculate import DateTimeCalculateTool
from .builtin.nearby_place_search import (
    NearbyPlaceProvider,
    NearbyPlaceSearchTool,
    UnavailableNearbyPlaceProvider,
)
from .builtin.nearby_place_search.providers import AmapNearbyPlaceProvider
from .builtin.social_platforms import (
    RequestSocialAuthTool,
    SocialAuthManager,
    SocialPlatformService,
    SocialReadTool,
    SocialSearchTool,
)
from .builtin.social_platforms.connectors import (
    XIAOHONGSHU_DEFAULT_SEARCH_MCP_TIMEOUT_SECONDS,
    BilibiliAuthHandler,
    BilibiliConnector,
    BilibiliSdkApi,
    OfficialMcpTransport,
    XiaohongshuAuthHandler,
    XiaohongshuConnector,
    XiaohongshuMcpApi,
    XiaohongshuProcess,
)
from .builtin.tool_call_history import InspectToolCallsTool
from .builtin.tool_result_recovery import ToolResultRecoveryTool
from .builtin.weather_query import (
    UnavailableWeatherLocationResolver,
    UnavailableWeatherProvider,
    WeatherProvider,
    WeatherQueryTool,
)
from .builtin.weather_query.providers import QWeatherProvider
from .builtin.web_fetch import SafeHtmlReader, TavilyExtractProvider, WebFetchTool
from .builtin.web_search import WebSearchTool
from .builtin.web_search.providers.tavily import TavilyProvider
from .location import CompositeLocationResolver, LocationResolver
from .protocol import Tool
from .registry import ToolRegistry

if TYPE_CHECKING:
    from ..attachments import AttachmentStore
    from ..command_runtime import CommandRuntime
    from ..desktop_applications import NativeApplicationLauncher
    from ..execution_environment import ExecutionEnvironmentManager
    from ..filesystem import FilesystemApprovalBroker, ProtectedPathPolicy
    from ..scheduled_tasks import ScheduledTaskService
    from ..sessions.catalog import SessionCatalog
    from ..sessions.store import SessionStore
    from ..sessions.tool_execution_evidence import ToolExecutionEvidenceStore
    from ..skills import SkillCatalogService
    from ..todos import TodoService


def make_default_registry(
    session_store: SessionStore | None = None,
    *,
    memory_search: MemorySearchService | None = None,
    todo_service: TodoService | None = None,
    scheduled_task_service: ScheduledTaskService | None = None,
    scheduled_task_persona_id: str | None = None,
    scheduled_task_persona_name: str = "default",
    scheduled_task_model_id: str | None = None,
    session_catalog: SessionCatalog | None = None,
    tavily_api_key: str | None = None,
    amap_webservice_key: str | None = None,
    qweather_api_key: str | None = None,
    qweather_api_host: str | None = None,
    social_platform_service: SocialPlatformService | None = None,
    skill_service: SkillCatalogService | None = None,
    approval_broker: FilesystemApprovalBroker | None = None,
    filesystem_policy: ProtectedPathPolicy | None = None,
    command_runtime: CommandRuntime | None = None,
    execution_environment_manager: ExecutionEnvironmentManager | None = None,
    tool_execution_evidence_store: ToolExecutionEvidenceStore | None = None,
    desktop_application_launcher: NativeApplicationLauncher | None = None,
    attachment_store: AttachmentStore | None = None,
    stderr_warn: bool = True,
) -> ToolRegistry:
    """构造默认 registry，确定性本地工具始终注册，网络/记忆工具按资源注册。

    Args:
        session_store: 020 起新增。非 ``None`` 时注册 :class:`ConversationHistoryTool`
            让 LLM 能主动回忆过往对话；``None``（默认）时不注册——保留 005 ~ 019
            的 web-search-only 行为完全字节兼容。``NullSessionStore`` 等空 store
            可注入：本工具调用时 ``list()`` 返回空，自然走拟人化"翻不到"兜底
            （详见 020 design §5.2 N-4）。
        tavily_api_key: Tavily API key。新桌面主路径由 runtime config 显式注入；
            ``None`` 时保留旧的 process env fallback。
        amap_webservice_key: 高德 Web 服务 Key。新桌面主路径由 runtime config
            显式注入；``None`` 时保留 process env fallback。无 Key 时仍注册地点
            工具，并在调用时返回可恢复的配置提示。
        qweather_api_key: 和风天气 API Key。与 ``qweather_api_host`` 同时可用时
            启用天气查询；缺少任一项时保留工具表面并返回配置提示。
        qweather_api_host: 和风天气账户专属 API Host。由 provider 严格校验，
            不接受 scheme、path、端口或非 ``qweatherapi.com`` 子域。
        stderr_warn: 未配置 ``TAVILY_API_KEY`` 时是否往 stderr 打一行提示。
            默认开启；测试场景可传 ``False`` 关闭以避免日志噪声。

    Returns:
        :class:`ToolRegistry`：

        - 始终包含 ``calculate`` 与 ``datetime_calculate``
        - 始终包含本地静态页 ``web_fetch``；Tavily 已配置时附加远端正文回退
        - ``TAVILY_API_KEY`` 已配置 → 额外包含 ``web_search``
        - 始终包含 ``nearby_place_search``；无高德 Key 时返回配置提示
        - 始终包含 ``weather_query``；天气 Key 或 Host 不完整时返回配置提示
        - ``session_store`` 非 ``None`` → 含 :class:`ConversationHistoryTool`
        - 始终包含受治理的公共文件工具；授权能力由入口和当前用户轮决定
        - 网络与记忆条件都不满足 → 仍保留确定性工具、公共文件工具及地点、天气工具表面
    """
    from ..filesystem.tools import make_filesystem_tools

    tools: list[Tool] = [CalculateTool(), DateTimeCalculateTool()]
    tools.extend(
        make_filesystem_tools(
            broker=approval_broker,
            policy=filesystem_policy,
        )
    )
    if attachment_store is not None:
        from ..filesystem.service import FilesystemService
        from ..image_reading import ImageReadingService
        from .builtin.view_image import ViewImageTool

        tools.append(
            ViewImageTool(
                FilesystemService(filesystem_policy), ImageReadingService(attachment_store)
            )
        )

    if command_runtime is not None:
        from ..command_runtime import make_command_tools

        tools.extend(
            make_command_tools(
                command_runtime,
                approval_broker,
                execution_environment_manager,
                tool_execution_evidence_store,
            )
        )

    if tool_execution_evidence_store is not None:
        from ..desktop_applications import LaunchDesktopApplicationTool, NativeApplicationLauncher

        tools.append(
            LaunchDesktopApplicationTool(
                desktop_application_launcher or NativeApplicationLauncher(None),
                approval_broker,
                tool_execution_evidence_store,
            )
        )

    if skill_service is not None:
        from .builtin.skills import SkillReadTool

        tools.append(SkillReadTool(skill_service))

    social_service = social_platform_service or make_default_social_platform_service()
    if social_platform_service is None:
        atexit.register(social_service.close)
    tools.extend(
        (
            SocialSearchTool(social_service),
            SocialReadTool(social_service),
            RequestSocialAuthTool(social_service),
        )
    )

    if todo_service is not None:
        from .builtin.todo_manage import TodoManageTool

        tools.append(TodoManageTool(todo_service))

    if scheduled_task_service is not None and scheduled_task_model_id is not None:
        from .builtin.scheduled_task_manage import ScheduledTaskManageTool

        tools.append(
            ScheduledTaskManageTool(
                scheduled_task_service,
                default_persona_id=scheduled_task_persona_id,
                default_persona_name=scheduled_task_persona_name,
                default_model_id=scheduled_task_model_id,
                skill_service=skill_service,
                approval_pending=_scheduled_task_approval_pending(approval_broker),
            )
        )

    api_key = tavily_api_key or os.environ.get("TAVILY_API_KEY")
    if api_key:
        provider = TavilyProvider(api_key=api_key)
        tools.append(WebSearchTool(provider=provider))
        tools.append(
            WebFetchTool(
                provider=TavilyExtractProvider(api_key=api_key),
                html_reader=SafeHtmlReader(),
            )
        )
    elif stderr_warn:
        print(
            "[tool] 未配置 TAVILY_API_KEY，网页搜索和远端正文回退已关闭；"
            "用户提供或已有证据中的静态页面仍可本地读取。"
            "如需启用，请运行 scripts/credentials/run.* set-bundle tavily；"
            "存量脚本可显式使用 legacy-dotenv。",
            file=sys.stderr,
        )
    if not api_key:
        tools.append(WebFetchTool(html_reader=SafeHtmlReader()))

    amap_key = amap_webservice_key or os.environ.get("AMAP_WEBSERVICE_KEY")
    nearby_provider: NearbyPlaceProvider
    amap_provider: AmapNearbyPlaceProvider | None = None
    if amap_key:
        amap_provider = AmapNearbyPlaceProvider(api_key=amap_key)
        nearby_provider = amap_provider
    else:
        nearby_provider = UnavailableNearbyPlaceProvider()
        if stderr_warn:
            print(
                "[tool] 未配置 AMAP_WEBSERVICE_KEY，附近地点查询需要先在设置页配置。",
                file=sys.stderr,
            )
    tools.append(NearbyPlaceSearchTool(provider=nearby_provider))

    weather_key = qweather_api_key or os.environ.get("QWEATHER_API_KEY")
    weather_host = qweather_api_host or os.environ.get("QWEATHER_API_HOST")
    weather_provider: WeatherProvider
    weather_resolver: LocationResolver
    if weather_key and weather_host:
        weather_provider = QWeatherProvider(api_key=weather_key, api_host=weather_host)
        weather_resolver = CompositeLocationResolver(weather_provider, amap_provider)
    else:
        weather_provider = UnavailableWeatherProvider()
        weather_resolver = UnavailableWeatherLocationResolver()
        if stderr_warn:
            print(
                "[tool] 未完整配置 QWEATHER_API_KEY / QWEATHER_API_HOST，天气查询需要先在设置页配置。",
                file=sys.stderr,
            )
    tools.append(
        WeatherQueryTool(
            resolver=weather_resolver,
            provider=weather_provider,
        )
    )

    if memory_search is None and session_store is not None:
        memory_search = MemorySearchService(
            [SessionHistorySource(session_store)],
            analyzer=QueryAnalyzer(),
        )
    if memory_search is not None:
        tools.append(
            ConversationHistoryTool(
                search_service=memory_search,
                session_catalog=session_catalog,
            )
        )

    if session_store is not None:
        tools.append(ToolResultRecoveryTool(session_store, tool_execution_evidence_store))
        tools.append(InspectToolCallsTool(session_store))

    return ToolRegistry(tools)


def _scheduled_task_approval_pending(
    broker: FilesystemApprovalBroker | None,
) -> Callable[[str, str], bool] | None:
    if broker is None:
        return None

    def pending(session_id: str, run_id: str) -> bool:
        return bool(broker.pending(session_id=session_id, run_id=run_id))

    return pending


def make_default_social_platform_service(
    *, xiaohongshu_search_timeout_seconds: float | None = None
) -> SocialPlatformService:
    """Build local-only platform connectors without starting processes or network calls."""
    xiaohongshu_process = XiaohongshuProcess()
    xiaohongshu_api = XiaohongshuMcpApi(
        process=xiaohongshu_process,
        transport=OfficialMcpTransport(
            search_timeout_seconds=(
                xiaohongshu_search_timeout_seconds
                if xiaohongshu_search_timeout_seconds is not None
                else XIAOHONGSHU_DEFAULT_SEARCH_MCP_TIMEOUT_SECONDS
            ),
        ),
    )
    bilibili_api = BilibiliSdkApi()
    auth = SocialAuthManager(
        (
            XiaohongshuAuthHandler(
                xiaohongshu_process,
                verify_login=xiaohongshu_api.check_login,
            ),
            BilibiliAuthHandler(bilibili_api),
        )
    )
    return SocialPlatformService(
        (
            XiaohongshuConnector(xiaohongshu_api),
            BilibiliConnector(bilibili_api),
        ),
        auth=auth,
    )
