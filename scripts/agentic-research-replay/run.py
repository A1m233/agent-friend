"""Replay the bounded creator-research path without exposing page bodies or credentials."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

from agent.context.naive import NaiveContextManager
from agent.conversation import Conversation
from agent.conversation_events import TextDelta, ToolCallRequest, TurnDone
from agent.paths import sessions_dir
from agent.personas import BUILTIN_DEFAULT_PERSONA_ID, PersonaCatalog
from agent.prompts import MarkdownPromptBuilder, PromptBuilder
from agent.runtime import AgentRuntime, RunOutcome, UserEvent
from agent.sessions.session import Session
from agent.sessions.store import JsonlSessionStore
from agent.system_prompt import DESKTOP_CHAT_SURFACE
from agent.tools.builtin.social_platforms import (
    SocialContentType,
    SocialCreatorWork,
    SocialCreatorWorksResponse,
    SocialEvidence,
    SocialPlatform,
    SocialPlatformService,
    SocialReadResponse,
    SocialReadTool,
    SocialResolvedUrl,
    SocialResourceLocator,
    SocialResourceStore,
    SocialSearchCandidate,
    SocialSearchRequest,
    SocialSearchResponse,
    SocialSearchTool,
)
from agent.tools.factory import make_default_registry, make_default_social_platform_service
from agent.tools.registry import ToolRegistry
from agent_bridge.runtime_config import resolve_bridge_runtime_config
from llm_providers.client import LLMClient
from llm_providers.stream_events import (
    LLMStreamEvent,
    LLMTextDelta,
    LLMToolCallDelta,
    LLMTurnDone,
)
from runtime_config.policy import credential_source_for_mode

QUERY = "看看b站up开战斗机的虾米，他有什么代表作"
CREATOR_UID = 8741868
CREATOR_URL = f"https://space.bilibili.com/{CREATOR_UID}"


@dataclass(frozen=True)
class _CreatorLocator:
    uid: int = CREATOR_UID
    platform: SocialPlatform = field(init=False, default="bilibili")
    content_type: SocialContentType = field(init=False, default="creator")


@dataclass(frozen=True)
class _VideoLocator:
    bvid: str
    platform: SocialPlatform = field(init=False, default="bilibili")
    content_type: SocialContentType = field(init=False, default="video")


class _FixtureConnector:
    platform: ClassVar[SocialPlatform] = "bilibili"
    name: ClassVar[str] = "fixture-bilibili"
    version: ClassVar[str] = "1"

    def search(self, request: SocialSearchRequest) -> SocialSearchResponse:
        if request.content_types != ("creator",):
            raise AssertionError("fixture expected native creator search")
        return SocialSearchResponse(
            candidates=(
                SocialSearchCandidate(
                    locator=_CreatorLocator(),
                    title="开战斗机的虾米",
                    author="开战斗机的虾米",
                    canonical_url=CREATOR_URL,
                    summary="fixture creator",
                    identity={"uid": CREATOR_UID},
                ),
            )
        )

    def resolve_url(self, url: str) -> SocialResolvedUrl | None:
        if url.split("?", 1)[0].rstrip("/") != CREATOR_URL:
            return None
        return SocialResolvedUrl(
            platform="bilibili",
            content_type="creator",
            canonical_url=CREATOR_URL,
            locator=_CreatorLocator(),
        )

    def read(self, locator: SocialResourceLocator) -> SocialReadResponse:
        if locator != _CreatorLocator():
            raise AssertionError("fixture creator identity changed")
        return SocialReadResponse(
            title="开战斗机的虾米",
            author="开战斗机的虾米",
            canonical_url=CREATOR_URL,
            evidence=(SocialEvidence("creator_profile", f'{{"uid": {CREATOR_UID}}}'),),
        )

    def creator_works(
        self,
        locator: SocialResourceLocator,
        *,
        sort: Any,
        page: int,
        page_size: int,
    ) -> SocialCreatorWorksResponse:
        if locator != _CreatorLocator() or sort != "popular" or page != 1 or page_size != 20:
            raise AssertionError("fixture works request changed")
        return SocialCreatorWorksResponse(
            creator_name="开战斗机的虾米",
            creator_url=CREATOR_URL,
            works=tuple(
                SocialCreatorWork(
                    locator=_VideoLocator(bvid),
                    title=title,
                    canonical_url=f"https://www.bilibili.com/video/{bvid}",
                    metrics={"play": play},
                )
                for bvid, title, play in (
                    ("BVfixture01", "代表作一", 2000),
                    ("BVfixture02", "代表作二", 1000),
                )
            ),
            sort="popular",
            page=1,
            has_more=False,
        )

    def close(self) -> None:
        return None


class _FixtureLlm:
    context_window = 128_000

    def __init__(self) -> None:
        self.turn = 0

    def complete(self, messages: list[dict[str, Any]], **overrides: Any) -> str:
        del messages, overrides
        return ""

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **overrides: Any,
    ) -> Iterator[LLMStreamEvent]:
        del messages, tools, overrides
        self.turn += 1
        if self.turn == 1:
            yield LLMToolCallDelta(
                0,
                "creator-search",
                "social_search",
                json.dumps(
                    {
                        "platform": "bilibili",
                        "query": "开战斗机的虾米",
                        "content_types": ["creator"],
                    }
                ),
            )
            yield LLMTurnDone(stop_reason="tool_use")
            return
        if self.turn == 2:
            yield LLMToolCallDelta(
                0,
                "creator-works",
                "social_read",
                json.dumps(
                    {
                        "resource_ref": "social_ref_00000000000000000000000000000001",
                        "view": "works",
                        "sort": "popular",
                    }
                ),
            )
            yield LLMTurnDone(stop_reason="tool_use")
            return
        yield LLMTextDelta("已根据该 UID 的公开投稿列表确认两项代表作。")
        yield LLMTurnDone(stop_reason="end_turn")


def _conversation(
    root: Path,
    *,
    client: LLMClient,
    registry: ToolRegistry,
    model: str,
    session_id: str,
) -> Conversation:
    catalog = PersonaCatalog(external_dir=root / "personas")
    store = JsonlSessionStore(root / "sessions")
    session = Session.new(
        title="073 agentic research replay",
        persona="default",
        persona_id=BUILTIN_DEFAULT_PERSONA_ID,
        model=model,
        session_id=session_id,
    )
    store.create(session)

    def prompt_factory(persona_id: str) -> PromptBuilder:
        return MarkdownPromptBuilder(persona_id=persona_id, catalog=catalog)

    return Conversation(
        session=session,
        store=store,
        llm_client=client,
        context_manager=NaiveContextManager(),
        prompt_builder=prompt_factory(BUILTIN_DEFAULT_PERSONA_ID),
        prompt_builder_factory=prompt_factory,
        catalog=catalog,
        tool_registry=registry,
        post_turn_external=True,
    )


def _audit_events(events: list[Any]) -> dict[str, object]:
    tool_sequence = [
        str(event.payload.get("tool_name")) for event in events if event.type == "tool_call_request"
    ]
    creator_uids: set[int] = set()
    work_urls: set[str] = set()
    tool_call_shapes: list[dict[str, object]] = []
    result_summaries: list[dict[str, object]] = []
    for event in events:
        if event.type == "tool_call_request":
            args = event.payload.get("args")
            safe_args = args if isinstance(args, dict) else {}
            tool_call_shapes.append(
                {
                    "tool": str(event.payload.get("tool_name")),
                    "argument_keys": sorted(
                        key for key in safe_args if not str(key).startswith("__agent_friend_")
                    ),
                    "content_types": safe_args.get("content_types"),
                    "view": safe_args.get("view"),
                    "sort": safe_args.get("sort"),
                }
            )
            continue
        if event.type != "tool_call_result":
            continue
        try:
            payload = json.loads(str(event.payload.get("content", "")))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        result_summaries.append(
            {
                "tool": str(event.payload.get("tool_name")),
                "is_error": bool(event.payload.get("is_error")),
                "status": payload.get("status"),
                "content_type": payload.get("content_type"),
                "view": payload.get("view"),
                "result_count": len(payload.get("results", ())),
                "works_count": len(payload.get("works", ())),
            }
        )
        if event.payload.get("is_error"):
            continue
        for item in payload.get("results", ()):
            if not isinstance(item, dict) or item.get("content_type") != "creator":
                continue
            identity = item.get("identity")
            uid = identity.get("uid") if isinstance(identity, dict) else None
            if isinstance(uid, int):
                creator_uids.add(uid)
        for item in payload.get("works", ()):
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                work_urls.add(item["url"])
        creator = payload.get("creator")
        creator_url = creator.get("url") if isinstance(creator, dict) else None
        if isinstance(creator_url, str):
            uid_text = creator_url.rstrip("/").rsplit("/", 1)[-1]
            if uid_text.isdigit():
                creator_uids.add(int(uid_text))
    final = next(
        (
            event
            for event in reversed(events)
            if event.type == "assistant_message" and str(event.payload.get("content", "")).strip()
        ),
        None,
    )
    return {
        "user_message_count": sum(event.type == "user_message" for event in events),
        "tool_sequence": tool_sequence,
        "tool_call_shapes": tool_call_shapes,
        "result_summaries": result_summaries,
        "creator_uids": sorted(creator_uids),
        "work_urls": sorted(work_urls),
        "final_answer_chars": len(str(final.payload.get("content", ""))) if final else 0,
        "stop_reason": final.meta.get("tool_run", {}).get("stop_reason") if final else None,
    }


def fixture() -> dict[str, object]:
    connector = _FixtureConnector()
    service = SocialPlatformService(
        (connector,),
        resources=SocialResourceStore(
            make_ref=iter(
                [
                    "social_ref_00000000000000000000000000000001",
                    "social_ref_00000000000000000000000000000002",
                    "social_ref_00000000000000000000000000000003",
                ]
            ).__next__
        ),
    )
    registry = ToolRegistry([SocialSearchTool(service), SocialReadTool(service)])
    try:
        with tempfile.TemporaryDirectory(prefix="agent-friend-073-fixture-") as temp:
            conversation = _conversation(
                Path(temp),
                client=cast(LLMClient, _FixtureLlm()),
                registry=registry,
                model="fixture/073",
                session_id="agentic-research-fixture",
            )
            emitted = list(conversation.stream(QUERY))
            audit = _audit_events(conversation.session.events)
            done = next(event for event in emitted if isinstance(event, TurnDone))
            expected_tools = ["social_search", "social_read"]
            if (
                audit["user_message_count"] != 1
                or audit["tool_sequence"] != expected_tools
                or audit["creator_uids"] != [CREATOR_UID]
                or len(cast(list[str], audit["work_urls"])) < 2
                or done.stop_reason != "end_turn"
            ):
                raise AssertionError(f"fixture replay failed: {audit}")
            return {"status": "passed", "mode": "fixture", **audit}
    finally:
        service.close()


def real_agent(*, model: str) -> dict[str, object]:
    runtime_config = resolve_bridge_runtime_config(credential_source_for_mode("desktop"))
    client = runtime_config.llm_client_factory(model)
    service = make_default_social_platform_service()
    credentials = runtime_config.tool_credentials
    registry = make_default_registry(
        tavily_api_key=credentials.tavily_api_key,
        amap_webservice_key=credentials.amap_webservice_key,
        qweather_api_key=credentials.qweather_api_key,
        qweather_api_host=credentials.qweather_api_host,
        social_platform_service=service,
        stderr_warn=False,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="agent-friend-073-real-") as temp:
            conversation = _conversation(
                Path(temp),
                client=client,
                registry=registry,
                model=model,
                session_id="agentic-research-real",
            )
            runtime = AgentRuntime(
                conversation_factory=lambda _session_id: conversation, memory=None
            )
            runtime.start()
            try:
                handle = runtime.submit(
                    UserEvent(session_id=conversation.session.session_id, user_input=QUERY),
                    channel="text",
                    surface=DESKTOP_CHAT_SURFACE,
                    metadata={"protocol": "ag_ui"},
                )
                emitted = list(handle.events())
                if handle.terminal is None or handle.terminal.outcome is not RunOutcome.COMPLETED:
                    raise AssertionError("real AgentRuntime did not complete")
                audit = _audit_events(conversation.session.events)
                if (
                    audit["user_message_count"] != 1
                    or CREATOR_UID not in cast(list[int], audit["creator_uids"])
                    or len(cast(list[str], audit["work_urls"])) < 2
                    or audit["final_answer_chars"] == 0
                ):
                    raise AssertionError(f"real-agent acceptance failed: {audit}")
                return {
                    "status": "passed",
                    "mode": "real-agent",
                    "model": model,
                    "stream_tool_sequence": [
                        event.tool_name for event in emitted if isinstance(event, ToolCallRequest)
                    ],
                    "stream_text_chars": sum(
                        len(event.text) for event in emitted if isinstance(event, TextDelta)
                    ),
                    **audit,
                }
            finally:
                runtime.stop()
    finally:
        service.close()


def audit_session(session_id: str, *, base_dir: Path | None = None) -> dict[str, object]:
    store = JsonlSessionStore(base_dir or sessions_dir())
    session = store.load(session_id)
    return {
        "status": "ok",
        "mode": "audit-session",
        "session_id": session_id,
        **_audit_events(session.active_events),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fixture")
    real = subparsers.add_parser("real-agent")
    real.add_argument("--model", required=True)
    real.add_argument("--allow-llm", action="store_true")
    real.add_argument("--allow-platform-network", action="store_true")
    audit = subparsers.add_parser("audit-session")
    audit.add_argument("--session-id", required=True)
    audit.add_argument("--sessions-dir", type=Path)
    args = parser.parse_args()

    if args.command == "fixture":
        result = fixture()
    elif args.command == "real-agent":
        if not args.allow_llm or not args.allow_platform_network:
            parser.error("real-agent requires --allow-llm and --allow-platform-network")
        result = real_agent(model=args.model)
    else:
        result = audit_session(args.session_id, base_dir=args.sessions_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
