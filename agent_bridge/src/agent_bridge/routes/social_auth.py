"""Loopback desktop control plane for pending social-platform authorization."""

from __future__ import annotations

from typing import Any, Literal

from agent.tools.builtin.social_platforms import (
    SocialAuthChallengeNotFoundError,
    SocialAuthRequest,
    SocialAuthRequestConflictError,
    SocialAuthRequestNotFoundError,
)
from agent.tools.builtin.social_platforms.bilibili_interactions.service import (
    BilibiliInteractionService,
)
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict

from ..assembly import BridgeRuntime
from ..social_auth import SocialAuthOrchestrator


class _ActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "decline", "cancel"]


def register_routes(app: Any, runtime: BridgeRuntime) -> None:
    router = APIRouter(prefix="/v1/social-auth", tags=["social-auth"])

    def bilibili() -> BilibiliInteractionService:
        service = runtime.social_platform_service
        if service is None or service.bilibili_interactions is None:
            raise HTTPException(status_code=503, detail="B 站账号管理不可用")
        return service.bilibili_interactions

    @router.get("/bilibili/account")
    def bilibili_account(verify: bool = Query(default=False)) -> dict[str, Any]:
        return bilibili().account(verify=verify)

    @router.post("/bilibili/login")
    def bilibili_login() -> dict[str, Any]:
        return bilibili().start_login()

    @router.get("/bilibili/login/{login_id}/challenge")
    def bilibili_challenge(login_id: str) -> Response:
        challenge = bilibili().challenge(login_id)
        if challenge is None:
            raise HTTPException(status_code=404, detail="二维码已过期或尚未就绪")
        return Response(
            challenge.content,
            media_type=challenge.media_type,
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/bilibili/logout")
    def bilibili_logout() -> dict[str, Any]:
        try:
            if runtime.social_platform_service is not None:
                runtime.social_platform_service.cancel_login("bilibili")
            return bilibili().logout()
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="退出未完成，请重试；不会继续使用旧内存凭据。"
            ) from exc

    def orchestrator() -> SocialAuthOrchestrator:
        value = runtime.social_auth_orchestrator
        if value is None:
            raise HTTPException(status_code=503, detail="social auth is unavailable")
        return value

    def request_payload(request: SocialAuthRequest) -> dict[str, object]:
        return _request_payload(
            request,
            challenge_kind=orchestrator().challenge_kind(request.request_id),
        )

    @router.get("/requests")
    def list_requests(
        session_id: str | None = Query(default=None),
        active_only: bool = Query(default=False),
    ) -> dict[str, object]:
        items = orchestrator().list(session_id=session_id, active_only=active_only)
        return {"requests": [request_payload(item) for item in items]}

    @router.get("/requests/{request_id}")
    def get_request(request_id: str) -> dict[str, object]:
        return request_payload(_get(orchestrator(), request_id))

    @router.get("/requests/{request_id}/challenge")
    def get_challenge(request_id: str) -> Response:
        try:
            challenge = orchestrator().challenge(request_id)
        except (SocialAuthChallengeNotFoundError, SocialAuthRequestNotFoundError) as exc:
            raise HTTPException(
                status_code=404, detail="authorization challenge not found"
            ) from exc
        return Response(
            content=challenge.content,
            media_type=challenge.media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/requests/{request_id}/approve", status_code=202)
    def approve_request(request_id: str, body: _ActionBody) -> dict[str, object]:
        _require_action(body, "approve")
        try:
            return request_payload(orchestrator().approve(request_id))
        except SocialAuthRequestNotFoundError as exc:
            raise HTTPException(status_code=404, detail="authorization request not found") from exc
        except SocialAuthRequestConflictError as exc:
            raise HTTPException(
                status_code=409, detail="authorization request is not pending"
            ) from exc

    @router.post("/requests/{request_id}/decline")
    def decline_request(request_id: str, body: _ActionBody) -> dict[str, object]:
        _require_action(body, "decline")
        try:
            return request_payload(orchestrator().decline(request_id))
        except SocialAuthRequestNotFoundError as exc:
            raise HTTPException(status_code=404, detail="authorization request not found") from exc
        except SocialAuthRequestConflictError as exc:
            raise HTTPException(
                status_code=409, detail="authorization request is not pending"
            ) from exc

    @router.post("/requests/{request_id}/cancel")
    def cancel_request(request_id: str, body: _ActionBody) -> dict[str, object]:
        _require_action(body, "cancel")
        try:
            return request_payload(orchestrator().cancel(request_id))
        except SocialAuthRequestNotFoundError as exc:
            raise HTTPException(status_code=404, detail="authorization request not found") from exc
        except SocialAuthRequestConflictError as exc:
            raise HTTPException(
                status_code=409, detail="authorization request cannot be cancelled"
            ) from exc

    app.include_router(router)


def _require_action(
    body: _ActionBody,
    expected: Literal["approve", "decline", "cancel"],
) -> None:
    if body.action != expected:
        raise HTTPException(status_code=400, detail="authorization action does not match endpoint")


def _get(
    orchestrator: SocialAuthOrchestrator,
    request_id: str,
) -> SocialAuthRequest:
    try:
        return orchestrator.get(request_id)
    except SocialAuthRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail="authorization request not found") from exc


def _request_payload(
    request: SocialAuthRequest,
    *,
    challenge_kind: str | None,
) -> dict[str, object]:
    return {
        "requestId": request.request_id,
        "sessionId": request.session_id,
        "platform": request.platform,
        "missingCapabilities": list(request.missing_capabilities),
        "reason": request.reason,
        "originSurface": request.origin_surface,
        "status": request.status,
        "createdAt": request.created_at.isoformat(),
        "expiresAt": request.expires_at.isoformat(),
        "continuationRunId": request.continuation_run_id,
        "errorKind": request.error_kind,
        "challengeKind": challenge_kind,
    }


__all__ = ["register_routes"]
