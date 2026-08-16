"""Trusted values that exist only for one tool-running turn."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from html import escape
from uuid import uuid4

from ..filesystem.models import FilesystemAuthorizationLedger
from .evidence import (
    EvidenceSource,
    PublicEvidence,
    PublicEvidenceLedger,
    is_persistable_public_url,
    make_public_evidence,
    normalize_url,
)

_URL_PATTERN = re.compile(r"https?://[^\s<>\"'，。；：！？、）】》」』]+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）】》"


def _system_now() -> datetime:
    return datetime.now().astimezone()


@dataclass(frozen=True)
class UrlAuthorization:
    normalized_url: str
    source: EvidenceSource
    source_call_id: str | None = None
    evidence_ref: str | None = None


@dataclass
class UrlAuthorizationLedger:
    _entries: dict[str, UrlAuthorization] = field(default_factory=dict)

    def register(
        self,
        url: str,
        source: EvidenceSource,
        source_call_id: str | None = None,
        evidence_ref: str | None = None,
    ) -> None:
        normalized = normalize_url(url)
        self._entries[normalized] = UrlAuthorization(
            normalized, source, source_call_id, evidence_ref
        )

    def register_user_text(self, text: str) -> None:
        for match in _URL_PATTERN.findall(text):
            candidate = match.rstrip(_TRAILING_PUNCTUATION)
            try:
                self.register(candidate, "user_input")
            except ValueError:
                continue

    def authorize(self, url: str) -> UrlAuthorization | None:
        try:
            normalized = normalize_url(url)
        except ValueError:
            return None
        return self._entries.get(normalized)

    def authorizations(self) -> tuple[UrlAuthorization, ...]:
        """Return the trusted URLs registered for this tool-running turn."""

        return tuple(self._entries.values())


@dataclass
class ToolRunContext:
    run_id: str
    trusted_now: datetime
    trusted_timezone: str
    url_authorizations: UrlAuthorizationLedger = field(default_factory=UrlAuthorizationLedger)
    public_evidence: PublicEvidenceLedger = field(default_factory=PublicEvidenceLedger)
    filesystem_authorizations: FilesystemAuthorizationLedger = field(
        default_factory=FilesystemAuthorizationLedger
    )
    _clock: Callable[[], datetime] = field(default=_system_now, repr=False)
    _command_handles: dict[str, Callable[[str], None]] = field(default_factory=dict, repr=False)
    _command_handles_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _command_resources: dict[str, object] = field(default_factory=dict, repr=False)
    _command_resource_cleanups: dict[str, Callable[[], None]] = field(
        default_factory=dict, repr=False
    )

    @classmethod
    def create(
        cls,
        user_input: str,
        *,
        now: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> ToolRunContext:
        if now is not None and clock is not None:
            raise ValueError("now and clock cannot be provided together")
        effective_clock = (lambda: now) if now is not None else clock or _system_now
        trusted_now, trusted_timezone = _read_trusted_clock(effective_clock)
        context = cls(
            f"toolrun_{uuid4().hex}",
            trusted_now,
            trusted_timezone,
            _clock=effective_clock,
        )
        context.register_user_text_evidence(user_input, persistable_only=False)
        return context

    def refresh_time(self) -> None:
        """Refresh the trusted clock immediately before one model inference.

        Tool calls emitted by that inference continue using this exact snapshot.  The
        next model continuation refreshes it again, so long-running tool loops do not
        inherit stale wall-clock time.
        """

        self.trusted_now, self.trusted_timezone = _read_trusted_clock(self._clock)

    def register_command_handle(self, process_id: str, terminate: Callable[[str], None]) -> None:
        """Bind an interactive process handle to this single tool-running turn."""

        with self._command_handles_lock:
            self._command_handles[process_id] = terminate

    def owns_command_handle(self, process_id: str) -> bool:
        with self._command_handles_lock:
            return process_id in self._command_handles

    def release_command_handle(self, process_id: str) -> None:
        with self._command_handles_lock:
            self._command_handles.pop(process_id, None)

    def close_commands(self, reason: str = "run_finished") -> None:
        """Terminate all live command handles without keeping a detached process."""

        with self._command_handles_lock:
            handles = tuple(self._command_handles.values())
            self._command_handles.clear()
        for terminate in handles:
            try:
                terminate(reason)
            except Exception:
                continue
        with self._command_handles_lock:
            cleanups = tuple(reversed(tuple(self._command_resource_cleanups.values())))
            self._command_resource_cleanups.clear()
            self._command_resources.clear()
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception:
                continue

    def command_resource(self, key: str) -> object | None:
        with self._command_handles_lock:
            return self._command_resources.get(key)

    def register_command_resource(
        self, key: str, value: object, cleanup: Callable[[], None]
    ) -> object:
        """Keep one run-scoped command resource and its eventual cleanup."""

        with self._command_handles_lock:
            existing = self._command_resources.get(key)
            if existing is not None:
                return existing
            self._command_resources[key] = value
            self._command_resource_cleanups[key] = cleanup
            return value

    def register_user_text_evidence(
        self,
        text: str,
        *,
        source_event_uuid: str | None = None,
        persistable_only: bool = True,
    ) -> None:
        ledger = UrlAuthorizationLedger()
        ledger.register_user_text(text)
        for authorization in ledger.authorizations():
            if persistable_only and not is_persistable_public_url(authorization.normalized_url):
                continue
            existing = self.public_evidence.find_url(authorization.normalized_url)
            if existing is not None:
                continue
            evidence = make_public_evidence(
                authorization.normalized_url,
                source="user_input",
            )
            if source_event_uuid is not None:
                evidence = replace(evidence, source_event_uuid=source_event_uuid)
            self.public_evidence.register(evidence)
            self.url_authorizations.register(
                evidence.normalized_url,
                evidence.source,
                evidence_ref=evidence.evidence_ref,
            )

    def register_tool_result(
        self,
        name: str,
        call_id: str,
        source_urls: object,
        public_evidence: object = None,
        ephemeral_evidence: object = None,
    ) -> None:
        for raw_value, require_persistable in (
            (public_evidence, True),
            (ephemeral_evidence, False),
        ):
            raw_items = (
                raw_value.get("items")
                if isinstance(raw_value, dict) and raw_value.get("version") == 1
                else None
            )
            if not isinstance(raw_items, list):
                continue
            for value in raw_items:
                evidence = PublicEvidence.from_meta(
                    value,
                    require_persistable=require_persistable,
                )
                if evidence is None:
                    continue
                self.register_evidence(evidence)
        if name == "web_search" and isinstance(source_urls, list):
            for url in source_urls:
                if not isinstance(url, str):
                    continue
                try:
                    existing = self.public_evidence.find_url(url)
                    evidence = existing or make_public_evidence(
                        url,
                        source="web_search_result",
                        source_call_id=call_id,
                    )
                    self.register_evidence(evidence)
                except ValueError:
                    continue

    def register_persisted_evidence(
        self, value: object, *, source_event_uuid: str | None = None
    ) -> None:
        if not isinstance(value, dict) or value.get("version") != 1:
            return
        items = value.get("items")
        if not isinstance(items, list):
            return
        for item in items:
            evidence = PublicEvidence.from_meta(item, source_event_uuid=source_event_uuid)
            if evidence is not None:
                self.register_evidence(evidence)

    def register_evidence(self, evidence: PublicEvidence) -> None:
        self.public_evidence.register(evidence)
        self.url_authorizations.register(
            evidence.normalized_url,
            evidence.source,
            evidence.source_call_id,
            evidence.evidence_ref,
        )

    def resolve_evidence(self, evidence_ref: str) -> PublicEvidence | None:
        return self.public_evidence.resolve(evidence_ref)

    def persistable_evidence(self) -> tuple[PublicEvidence, ...]:
        return tuple(
            item
            for item in self.public_evidence.entries()
            if is_persistable_public_url(item.normalized_url)
        )

    def render_for_model(self) -> str:
        """Render the same trusted clock snapshot that hidden tool args receive.

        The block is transient input for one inference and is never persisted in session
        history. Keeping it on this object prevents model-visible and tool-visible time
        from drifting apart for tool calls emitted by the same inference.
        """
        current_datetime = self.trusted_now.isoformat(timespec="seconds")
        today = self.trusted_now.date()
        runtime_time = (
            f'<runtime_now at="{current_datetime}" tz="{self.trusted_timezone}" '
            f'yesterday="{today - timedelta(days=1)}" today="{today}" '
            f'tomorrow="{today + timedelta(days=1)}" />\n'
            "本轮时间以此为准；日历锚点同样可信，历史日期不覆盖，勿反推。"
        )
        skill_scopes = [
            capability
            for capability in self.filesystem_authorizations.scopes()
            if capability.source == "skill"
        ]
        if not skill_scopes:
            return runtime_time
        scope_lines = [
            '<filesystem_scopes lifetime="current_user_turn" absolute_paths_exposed="false">'
        ]
        for capability in skill_scopes:
            label = escape(capability.label or "activated Skill", quote=True)
            permissions = ",".join(sorted(capability.permissions))
            scope_lines.append(
                f'  <scope id="{capability.scope_id}" label="{label}" '
                f'permissions="{permissions}" relative_root="." />'
            )
        scope_lines.append(
            "  <instruction>只在 Skill 指引或当前任务需要时按相对路径读取；"
            "scope 不提供包外权限。</instruction>"
        )
        scope_lines.append("</filesystem_scopes>")
        return runtime_time + "\n" + "\n".join(scope_lines)


def _read_trusted_clock(clock: Callable[[], datetime]) -> tuple[datetime, str]:
    trusted_now = clock()
    if trusted_now.tzinfo is None:
        raise ValueError("trusted now must be timezone-aware")
    zone = getattr(trusted_now.tzinfo, "key", None) or _offset_text(trusted_now)
    return trusted_now, str(zone)


def _offset_text(value: datetime) -> str:
    offset = value.utcoffset()
    if offset is None:
        return "UTC"
    total = int(offset.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"
