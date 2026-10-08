"""Versioned storage encoding; logical rollout v1 hashes keep their original meaning."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from hashlib import sha256
from typing import Any
from uuid import uuid4

from .errors import SessionCorruptError
from .events import Event


def fingerprint(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


class RolloutEncoder:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._active: list[str] = []
        self._commit: str | None = None

    def fork(self) -> RolloutEncoder:
        encoder = RolloutEncoder()
        encoder._values = dict(self._values)
        encoder._active = self._active
        encoder._commit = self._commit
        return encoder

    def encode(self, event: Event) -> list[Event]:
        if event.type not in {"rollout_commit", "rollout_request_prepared"}:
            return [event]
        payload = dict(event.payload)
        records: list[Event] = []
        references: dict[str, str] = {}
        manifest = payload.get("epoch_manifest")
        values = {"model_context": payload.get("model_context")}
        if isinstance(manifest, dict):
            values["epoch_envelope"] = manifest.get("envelope")
        for kind, value in values.items():
            digest = fingerprint(value)
            if self._values.get(kind) != digest:
                records.append(
                    Event(
                        type="rollout_value",
                        uuid=str(uuid4()),
                        ts=event.ts,
                        payload={
                            "encoding_version": 1,
                            "kind": kind,
                            "sha256": digest,
                            "value": value,
                        },
                    )
                )
                self._values[kind] = digest
            references[kind] = digest
        payload.pop("model_context", None)
        if isinstance(manifest, dict):
            payload["epoch_manifest"] = {
                key: value for key, value in manifest.items() if key != "envelope"
            }
        payload["storage_refs"] = references
        payload["encoding_version"] = 1
        if event.type == "rollout_commit":
            active = payload.pop("active_event_uuids", [])
            keep = 0
            if payload.get("base_commit_id") == self._commit:
                for old, new in zip(self._active, active, strict=False):
                    if old != new:
                        break
                    keep += 1
            payload["active_event_delta"] = {
                "prefix": keep,
                "suffix": active[keep:],
                "sha256": fingerprint(active),
            }
            self._active = active
            self._commit = payload.get("commit_id")
        records.append(replace(event, payload=payload))
        return records


class RolloutDecoder:
    def __init__(self) -> None:
        self._values: dict[str, tuple[str, Any]] = {}
        self._active: list[str] = []
        self._commit: str | None = None

    def decode(self, event: Event, *, materialize: bool = True) -> Event:
        payload = event.payload
        if event.type == "rollout_value":
            kind, digest = payload.get("kind"), payload.get("sha256")
            if (
                payload.get("encoding_version") != 1
                or kind not in {"model_context", "epoch_envelope"}
                or digest != fingerprint(payload.get("value"))
            ):
                raise SessionCorruptError("invalid rollout value version or digest")
            self._values[str(kind)] = (str(digest), payload.get("value"))
            return event
        if event.type not in {"rollout_commit", "rollout_request_prepared"}:
            return event
        if "encoding_version" in payload:
            if payload["encoding_version"] != 1:
                raise SessionCorruptError("unsupported rollout storage encoding")
            payload = dict(payload)
            references = payload.pop("storage_refs", None)
            if not isinstance(references, dict) or set(references) != {
                "model_context",
                "epoch_envelope",
            }:
                raise SessionCorruptError("missing rollout storage references")
            for kind, digest in references.items():
                current = self._values.get(kind)
                if current is None or current[0] != digest:
                    raise SessionCorruptError("unresolved rollout storage reference")
                # Business readers need the same integrity checks, but discard
                # internal events immediately. Avoid copying large model state.
                value = copy.deepcopy(current[1]) if materialize else current[1]
                if kind == "model_context":
                    payload[kind] = value
                else:
                    manifest = payload.get("epoch_manifest")
                    if not isinstance(manifest, dict):
                        raise SessionCorruptError("missing rollout epoch manifest")
                    payload["epoch_manifest"] = {**manifest, "envelope": value}
            if event.type == "rollout_commit":
                delta = payload.pop("active_event_delta", None)
                if not isinstance(delta, dict):
                    raise SessionCorruptError("missing rollout identity delta")
                keep, suffix = delta.get("prefix"), delta.get("suffix")
                if (
                    type(keep) is not int
                    or keep < 0
                    or keep > len(self._active)
                    or not isinstance(suffix, list)
                    or any(not isinstance(item, str) for item in suffix)
                ):
                    raise SessionCorruptError("invalid rollout identity delta")
                if keep and payload.get("base_commit_id") != self._commit:
                    raise SessionCorruptError("rollout identity delta has a different base")
                active = self._active[:keep] + suffix
                if fingerprint(active) != delta.get("sha256"):
                    raise SessionCorruptError("rollout identity digest mismatch")
                payload["active_event_uuids"] = active
            payload.pop("encoding_version")
            event = replace(event, payload=payload)
        if event.type == "rollout_commit":
            active = payload.get("active_event_uuids", [])
            if isinstance(active, list):
                self._active = active
            self._commit = payload.get("commit_id")
        return event
