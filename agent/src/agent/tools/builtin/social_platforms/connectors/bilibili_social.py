"""Bounded video-comment APIs. All returned author text remains external data."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from bilibili_api import Credential, comment, session, user, video

from ..models import SocialProviderError
from .bilibili import BilibiliSdkApi, _run_async


class BilibiliInteractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def positive_id(value: Any) -> str:
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,18}", text):
        raise BilibiliInteractionError(
            "invalid_identifier", "需要准确的正整数 ID，不能使用昵称或猜测值。"
        )
    return text


def video_identifier(value: str) -> dict[str, Any]:
    match = re.fullmatch(
        r"(?:https://www\.bilibili\.com/video/)?(BV[A-Za-z0-9]{10}|av[1-9][0-9]{0,18})/?", value
    )
    if not match:
        raise BilibiliInteractionError(
            "invalid_video", "仅支持准确 BV/av 号或规范视频主页，不支持动态或任意 URL。"
        )
    identifier = match.group(1)
    return {"bvid": identifier} if identifier.startswith("BV") else {"aid": int(identifier[2:])}


def comment_record(raw: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    member = raw.get("member") or {}
    mentions = (raw.get("content") or {}).get("at_name_to_mid_str") or {}
    return {
        "comment_id": str(raw.get("rpid_str") or raw.get("rpid", "")),
        "root_id": str(raw.get("root_str") or raw.get("root", "0")),
        "parent_id": str(raw.get("parent_str") or raw.get("parent", "0")),
        "author_uid": str(member.get("mid", "")),
        "author_name": str(member.get("uname", ""))[:200],
        "text": str((raw.get("content") or {}).get("message", ""))[:6000],
        "created_at": raw.get("ctime"),
        "platform_state": raw.get("state"),
        "mentioned_uids": [
            str(uid)
            for uid in list(mentions.values())[:30]
            if re.fullmatch(r"[1-9][0-9]{0,18}", str(uid))
        ]
        if isinstance(mentions, dict)
        else [],
        "source_type": "external_comment",
        "replies": [comment_record(r, depth=depth + 1) for r in (raw.get("replies") or [])[:20]]
        if depth < 2
        else [],
    }


def notification_video(uri: str) -> str | None:
    """Resolve only a canonical platform video location, never prose or redirects."""
    try:
        url = urlsplit(uri)
        if url.scheme != "https" or url.netloc != "www.bilibili.com":
            return None
        parsed = video_identifier(f"https://www.bilibili.com{url.path}")
    except ValueError:
        return None
    return str(parsed["bvid"]) if "bvid" in parsed else f"av{parsed['aid']}"


class BilibiliSocialApi:
    def __init__(self, credentials: BilibiliSdkApi) -> None:
        self.credentials = credentials

    def _call(self, awaitable: Any) -> Any:
        try:
            return _run_async(asyncio.wait_for(awaitable, timeout=20))
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in {-101, -111} or "CredentialNo" in type(exc).__name__:
                raise BilibiliInteractionError(
                    "login_required", "B 站登录已失效或缺少有效凭据，请在自主陪伴设置中登录。"
                ) from exc
            raise BilibiliInteractionError(
                "platform_unavailable", "B 站请求未完成，不能据此判定没有互动。"
            ) from exc

    def identity(self) -> dict[str, Any]:
        credential, generation = self.credentials.credential_snapshot()
        if not credential.has_sessdata():
            return {"state": "anonymous", "generation": generation}
        raw = self._call(user.get_self_info(credential))
        if generation != self.credentials.credential_generation:
            raise BilibiliInteractionError("account_changed", "账号已变化，请重新查询。")
        return {
            "state": "authenticated",
            "uid": positive_id(raw.get("mid")),
            "name": str(raw.get("name", ""))[:200],
            "avatar": str(raw.get("face", ""))[:1000],
            "generation": generation,
        }

    def video_info(self, identifier: str) -> dict[str, Any]:
        credential, _ = self.credentials.credential_snapshot()
        raw = self._call(
            video.Video(**video_identifier(identifier), credential=credential).get_info()
        )
        return {
            "aid": positive_id(raw.get("aid")),
            "bvid": str(raw.get("bvid", "")),
            "title": str(raw.get("title", ""))[:300],
        }

    def comments(
        self, identifier: str, *, root_id: str | None = None, page: int = 1, cursor: str = ""
    ) -> dict[str, Any]:
        info = self.video_info(identifier)
        credential, _ = self.credentials.credential_snapshot()
        oid = int(info["aid"])
        if root_id:
            raw = self._call(
                comment.Comment(
                    oid, comment.CommentResourceType.VIDEO, int(positive_id(root_id)), credential
                ).get_sub_comments(page_index=page, page_size=20)
            )
            root = raw.get("root")
            visibility = self.public_visibility(info["aid"], root_id)
            return {
                "video": info,
                "root": comment_record(root) if isinstance(root, dict) else None,
                "comments": [comment_record(r) for r in (raw.get("replies") or [])[:20]],
                "next_page": page + 1 if len(raw.get("replies") or []) >= 20 else None,
                "readback_scope": "current_account",
                "public_visibility": visibility,
                "mention_delivery": "unknown",
            }
        raw = self._call(
            comment.get_comments_lazy(
                oid, comment.CommentResourceType.VIDEO, offset=cursor, credential=credential
            )
        )
        pagination = raw.get("cursor") or {}
        return {
            "video": info,
            "comments": [comment_record(r) for r in (raw.get("replies") or [])[:30]],
            "next_cursor": None
            if pagination.get("is_end")
            else (pagination.get("pagination_reply") or {}).get("next_offset"),
            "truncated": len(raw.get("replies") or []) > 30,
            "readback_scope": "current_account",
        }

    def public_visibility(self, aid: str, root_id: str) -> dict[str, Any]:
        """One independent anonymous read, without cookies or background polling."""
        try:
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
                response = client.get(
                    "https://api.bilibili.com/x/v2/reply/reply",
                    params={
                        "type": "1",
                        "oid": positive_id(aid),
                        "root": positive_id(root_id),
                        "pn": "1",
                        "ps": "20",
                    },
                    headers={"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            return {"status": "unknown", "viewer": "anonymous", "reason": "lookup_failed"}
        if not isinstance(data, dict):
            return {"status": "unknown", "viewer": "anonymous", "reason": "invalid_response"}
        if data.get("code") == 12006:
            return {
                "status": "not_visible",
                "viewer": "anonymous",
                "reason": "comment_not_found",
                "message": "平台接收或作者能读回，不等于其他人可见。当前匿名查询没有该评论；不要重复发布。",
            }
        payload = data.get("data")
        root = payload.get("root") if isinstance(payload, dict) else None
        if (
            data.get("code") != 0
            or not isinstance(payload, dict)
            or not isinstance(root, dict)
            or str(root.get("rpid_str") or root.get("rpid")) != root_id
        ):
            return {"status": "unknown", "viewer": "anonymous", "reason": "unconfirmed_response"}
        replies = payload.get("replies")
        return {
            "status": "visible",
            "viewer": "anonymous",
            "root_id": root_id,
            "visible_reply_ids": [
                str(r.get("rpid_str") or r.get("rpid"))
                for r in (replies if isinstance(replies, list) else [])[:20]
                if isinstance(r, dict)
            ],
        }

    def interactions(self, kind: str, cursor: str | None = None) -> dict[str, Any]:
        account = self.identity()
        if account["state"] != "authenticated":
            raise BilibiliInteractionError(
                "login_required", "请先在自主陪伴设置中登录 AF 的 B 站账号。"
            )
        data = json.loads(cursor) if cursor else {}
        if not isinstance(data, dict) or set(data) - {"id", "time"}:
            raise BilibiliInteractionError("invalid_cursor", "请原样使用上次返回的 next_cursor。")
        identifier = int(positive_id(data["id"])) if data.get("id") else None
        timestamp = int(positive_id(data["time"])) if data.get("time") else None
        credential, _ = self.credentials.credential_snapshot()
        raw = self._call(
            session.get_replies(credential, last_reply_id=identifier, reply_time=timestamp)
            if kind == "replies"
            else session.get_at(
                credential,
                **(
                    {"last_id": identifier, "at_time": timestamp}
                    if identifier is not None and timestamp is not None
                    else {}
                ),
            )
        )
        items = []
        for row in (raw.get("items") or [])[:30]:
            item = row.get("item") or {}
            author = row.get("user") or {}
            uri = str(item.get("uri", ""))[:1000]
            video_id = notification_video(uri)
            items.append(
                {
                    "notification_id": str(row.get("id", "")),
                    "author_uid": str(author.get("mid", "")),
                    "author_name": str(author.get("nickname", ""))[:200],
                    "text": str(item.get("source_content") or item.get("title") or "")[:6000],
                    "subject_type": str(item.get("type", "")),
                    "subject_id": str(item.get("subject_id", "")),
                    "comment_id": str(item.get("source_id", "")),
                    "uri": uri,
                    "video_id": video_id,
                    "supported": video_id is not None,
                    "unsupported_reason": None
                    if video_id
                    else "video_comment_location_unavailable",
                    "created_at": row.get("reply_time" if kind == "replies" else "at_time"),
                    "source_type": "external_comment",
                }
            )
        paging = raw.get("cursor") or {}
        next_id = paging.get("id")
        next_time = paging.get("reply_time" if kind == "replies" else "at_time")
        continuation = (
            json.dumps({"id": str(next_id), "time": str(next_time)})
            if next_id and next_time and not paging.get("is_end")
            else None
        )
        return {
            "account": account,
            "items": items,
            "next_cursor": continuation,
            "read_receipt": "unknown",
            "scope": "platform_notifications; only video comments can be replied to",
        }

    def mention(self, uid: str) -> dict[str, str]:
        uid = positive_id(uid)
        credential, _ = self.credentials.credential_snapshot()
        # Mention assembly only needs the UID/name card. The SDK's full space
        # profile depends on HTML initial-state parsing, unrelated to a mention.
        try:
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                response = client.get(
                    "https://api.bilibili.com/x/web-interface/card",
                    params={"mid": uid},
                    cookies=credential.get_cookies(),
                    headers={"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
                raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BilibiliInteractionError(
                "mention_lookup_failed", "未能查询 @ 目标的账号卡片，尚未发布评论。"
            ) from exc
        if not isinstance(raw, dict) or raw.get("code") != 0:
            raise BilibiliInteractionError(
                "mention_lookup_failed", "B 站未返回有效账号卡片，尚未发布评论。"
            )
        data = raw.get("data")
        card = data.get("card") if isinstance(data, dict) else None
        if not isinstance(card, dict) or str(card.get("mid")) != uid:
            raise BilibiliInteractionError("identity_mismatch", "提及账号校验失败，未发布。")
        name = card.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise BilibiliInteractionError(
                "mention_lookup_failed", "目标账号卡片缺少有效昵称，尚未发布评论。"
            )
        return {"uid": uid, "name": name}

    def post(self, prepared: dict[str, Any]) -> dict[str, Any]:
        def dispatch(credential: Credential) -> dict[str, Any]:
            if not credential.has_sessdata() or not credential.has_bili_jct():
                raise BilibiliInteractionError("login_required", "账号已退出或缺少发布凭据。")
            body = {
                "oid": prepared["aid"],
                "type": "1",
                "message": prepared["text"],
                "plat": "1",
                "csrf": credential.bili_jct,
                "at_name_to_mid": json.dumps(
                    {m["name"]: int(m["uid"]) for m in prepared["mentions"]}, ensure_ascii=False
                ),
            }
            if prepared.get("parent_id"):
                body.update(root=prepared["root_id"], parent=prepared["parent_id"])
            try:
                # One HTTP attempt, no redirects or transparent retry for public writes.
                with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                    response = client.post(
                        "https://api.bilibili.com/x/v2/reply/add",
                        data=body,
                        cookies=credential.get_cookies(),
                        headers={
                            "Referer": "https://www.bilibili.com/",
                            "User-Agent": "Mozilla/5.0",
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()
            except (httpx.HTTPError, ValueError):
                return {
                    "status": "unknown",
                    "error": "send_outcome_unknown",
                    "message": "无法确认发布结果，请先查看评论，不要自动重发。",
                }
            if raw.get("code") != 0:
                return {
                    "status": "failed",
                    "error": "platform_rejected",
                    "platform_code": raw.get("code"),
                }
            rpid = str((raw.get("data") or {}).get("rpid", ""))
            if not re.fullmatch(r"[1-9][0-9]*", rpid):
                return {"status": "unknown", "error": "missing_comment_receipt"}
            return {
                "status": "accepted",
                "public_visibility": "unknown",
                "message": "平台已接收；尚未证明其他人可见或 @ 提醒送达。请按根评论 ID 读回检查 public_visibility，不要重复发布。",
                "comment_id": rpid,
                "url": f"https://www.bilibili.com/video/{prepared['bvid']}#reply{rpid}",
                "mention_delivery": "unknown",
            }

        try:
            return cast(
                dict[str, Any], self.credentials.with_credential(prepared["generation"], dispatch)
            )
        except SocialProviderError as exc:
            raise BilibiliInteractionError(
                "account_changed", "账号已变化，旧审批不再有效，请重新确认账号。"
            ) from exc
