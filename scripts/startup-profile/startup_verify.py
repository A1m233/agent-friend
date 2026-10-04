"""Only invoke the read-only installer helper verify command; never run an installer."""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from startup_evidence import digest, now, read_json

METRICS = (
    "totalMs",
    "metadataMs",
    "readMs",
    "hashMs",
    "manifestFiles",
    "metadataChecked",
    "filesVerified",
    "bytesRead",
)


def summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    values = [row["wallElapsedMs"] for row in samples if row["ok"]]
    return {
        "successfulSamples": len(values),
        "sampleCount": len(samples),
        "medianMs": statistics.median(values) if values else None,
        "maxMs": max(values) if values else None,
    }


def verification_metrics(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {key: value.get(key) for key in METRICS}
    if any(
        isinstance(number, bool)
        or not isinstance(number, (float, int))
        or not math.isfinite(number)
        or number < 0
        for number in result.values()
    ):
        return None
    return result


def measure(
    helper: Path,
    install_root: Path,
    repeats: int,
    timeout: float,
    report: dict[str, Any],
    build_report: Path | None = None,
) -> None:
    helper = helper.resolve(strict=True)
    root = install_root.resolve(strict=True)
    receipt_path = root / "active-install.json"
    receipt_hash = digest(receipt_path)
    receipt = read_json(receipt_path)
    payload = (root / receipt["payloadRelativePath"]).resolve(strict=True)
    if not payload.is_relative_to(root) or payload == root:
        raise ValueError("payload escaped installation root")
    manifest_path = payload / "payload-manifest.json"
    identity_path = payload / "build-identity.json"
    manifest_hash = digest(manifest_path)
    identity_hash = digest(identity_path)
    if (
        manifest_hash != receipt["payloadManifestSha256"]
        or identity_hash != receipt["buildIdentitySha256"]
    ):
        raise ValueError("receipt/manifest/build identity mismatch before measurement")
    helper_hash = digest(helper)
    binding = read_json(build_report) if build_report else None
    if binding is not None and (
        binding.get("helperSha256") != helper_hash
        or binding.get("ok") is not True
        or binding.get("sourceUnchangedDuringBuild") is not True
        or not isinstance(binding.get("helperSource"), dict)
        or not binding["helperSource"].get("measurementInputHashes")
    ):
        raise ValueError("helper build report mismatch")
    manifest = read_json(manifest_path)
    file_count = len(manifest["files"])
    total_bytes = sum(item["size"] for item in manifest["files"])
    report.update(
        {
            "helperSha256": helper_hash,
            "helperBuild": {key: value for key, value in binding.items() if key != "helperPath"}
            if binding
            else None,
            "helperSourceBound": binding is not None,
            "buildIdentity": read_json(identity_path),
            "manifestSha256": manifest_hash,
            "payloadFiles": file_count,
            "payloadBytes": total_bytes,
            "requestedSamples": repeats,
            "timeoutSeconds": timeout,
            "samples": [],
            "limitations": [
                "helper subprocess, not end-to-end startup",
                "initial OS cache state unknown; no cache eviction",
                "receipt/manifest identity reads precede timing",
                "reads warm caches; repeats are not independent cold starts",
            ],
        }
    )
    args = [
        str(helper),
        "verify",
        "--install-root",
        str(root),
        "--payload-relative-path",
        receipt["payloadRelativePath"],
        "--profile-id",
        receipt["profileId"],
        "--build-id",
        receipt["buildId"],
        "--timings",
    ]
    for index in range(repeats):
        sample: dict[str, Any] = {
            "sample": index + 1,
            "startedAt": now(),
            "ok": False,
            "cacheState": "unknown" if index == 0 else "previously-read-not-guaranteed-warm",
        }
        start = time.perf_counter()
        try:
            child = subprocess.run(
                args,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            sample["wallElapsedMs"] = (time.perf_counter() - start) * 1000
            sample["exitCode"] = child.returncode
            sample["stderrPresent"] = bool(child.stderr.strip())
            try:
                output = json.loads(child.stdout)
                if not isinstance(output, dict):
                    raise ValueError("not object")
            except (ValueError, TypeError):
                output = {}
                sample["failure"] = "invalid-helper-output"
            sample["verification"] = verification_metrics(output.get("verification"))
            sample["timingsAvailable"] = sample["verification"] is not None
            matching = (
                output.get("buildIdentitySha256") == identity_hash
                and output.get("payloadManifestSha256") == manifest_hash
            )
            complete = ("verification" not in output) or (
                sample["verification"] is not None
                and (
                    sample["verification"]["filesVerified"] == file_count
                    and sample["verification"]["bytesRead"] == total_bytes
                )
            )
            sample["ok"] = (
                child.returncode == 0 and output.get("ok") is True and matching and complete
            )
            if not sample["ok"]:
                # Never persist raw stderr/errors, which can contain private local paths.
                sample.setdefault("failure", "helper-failed-or-identity/count-mismatch")
        except subprocess.TimeoutExpired:
            sample["wallElapsedMs"] = (time.perf_counter() - start) * 1000
            sample["failure"] = "timeout"
        except OSError:
            sample["wallElapsedMs"] = (time.perf_counter() - start) * 1000
            sample["failure"] = "helper-launch-failed"
        report["samples"].append(sample)
        if not sample["ok"]:
            break
    report["inputsUnchanged"] = (
        digest(receipt_path) == receipt_hash
        and digest(helper) == helper_hash
        and digest(manifest_path) == manifest_hash
        and digest(identity_path) == identity_hash
    )
    report["firstSample"] = report["samples"][0] if report["samples"] else None
    report["repeatSummary"] = summary(report["samples"][1:])
    report["ok"] = (
        len(report["samples"]) == repeats
        and report["inputsUnchanged"]
        and all(row["ok"] for row in report["samples"])
    )
