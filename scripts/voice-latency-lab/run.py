"""Voice latency lab automation.

The lab has two safe boundaries:

1. ``fixtures`` decodes representative Volc callback envelopes locally.
2. ``replay`` talks to a running local voice_bridge and imitates the HTTP
   shapes Volc uses: callback envelopes plus CustomLLM streaming requests.
3. ``matrix`` starts isolated local bridge processes, then runs the same
   replay across low-latency experiment variants.
4. ``live`` starts isolated services plus cloudflared, then drives a real
   RTC room through Chromium + fake microphone audio.

``replay`` only calls the real LLM path when ``--allow-llm`` is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import httpx
from voice_bridge.callbacks import decode_volc_callback_payload
from voice_bridge.latency import SseTextObserver

from agent import log_dir as default_log_dir


@dataclass(frozen=True)
class LatencyEvent:
    source: Path
    timestamp: datetime | None
    fields: dict[str, str]


@dataclass(frozen=True)
class MatrixScenario:
    name: str
    env: dict[str, str]


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_handle: IO[str]
    log_path: Path


@dataclass(frozen=True)
class ReplayResult:
    scenario: str
    trace_id: str
    log_dir: Path
    events: list[LatencyEvent]


MATRIX_SCENARIOS: dict[str, MatrixScenario] = {
    "baseline": MatrixScenario(name="baseline", env={}),
    "old_rtc_defaults": MatrixScenario(
        name="old_rtc_defaults",
        env={
            "VOICE_BRIDGE_ASR_VAD_SILENCE_TIME_MS": "300",
            "VOICE_BRIDGE_LLM_PREFILL_ENABLED": "false",
            "VOICE_BRIDGE_VOICE_TYPE": "zh_female_linjianvhai_moon_bigtts",
        },
    ),
    "prefill_bigtts": MatrixScenario(
        name="prefill_bigtts",
        env={
            "VOICE_BRIDGE_LLM_PREFILL_ENABLED": "true",
            "VOICE_BRIDGE_VOICE_TYPE": "zh_female_linjianvhai_moon_bigtts",
        },
    ),
    "small_tts_no_prefill": MatrixScenario(
        name="small_tts_no_prefill",
        env={
            "VOICE_BRIDGE_LLM_PREFILL_ENABLED": "false",
            "VOICE_BRIDGE_VOICE_TYPE": "BV001_streaming",
        },
    ),
    "prefill_small_tts": MatrixScenario(
        name="prefill_small_tts",
        env={
            "VOICE_BRIDGE_LLM_PREFILL_ENABLED": "true",
            "VOICE_BRIDGE_VOICE_TYPE": "BV001_streaming",
        },
    ),
    "short_reply": MatrixScenario(
        name="short_reply",
        env={"VOICE_LATENCY_EXPERIMENT_SHORT_REPLY": "true"},
    ),
    "max_tokens_64": MatrixScenario(
        name="max_tokens_64",
        env={"VOICE_LATENCY_EXPERIMENT_MAX_TOKENS": "64"},
    ),
    "short_reply_max_tokens_64": MatrixScenario(
        name="short_reply_max_tokens_64",
        env={
            "VOICE_LATENCY_EXPERIMENT_SHORT_REPLY": "true",
            "VOICE_LATENCY_EXPERIMENT_MAX_TOKENS": "64",
        },
    ),
}


class SseTextCollector:
    """Collect content deltas from an OpenAI-compatible SSE stream."""

    def __init__(self) -> None:
        self._buffer = ""
        self.parts: list[str] = []

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            self._consume_frame(frame)

    def text(self) -> str:
        return "".join(self.parts)

    def _consume_frame(self, frame: str) -> None:
        data_lines = [
            line.removeprefix("data:").strip()
            for line in frame.split("\n")
            if line.strip().startswith("data:")
        ]
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        if raw == "[DONE]":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        if not isinstance(delta, dict):
            return
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.parts.append(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice latency lab automation.")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    subparsers.add_parser("fixtures", help="Decode built-in Volc callback fixtures.")

    replay = subparsers.add_parser("replay", help="Replay a near-real voice LLM round.")
    replay.add_argument("--voice-url", default="http://127.0.0.1:18900")
    replay.add_argument("--session-id", default="")
    replay.add_argument("--trace-id", default="")
    replay.add_argument("--user-text", default="hello, briefly introduce yourself")
    replay.add_argument("--model", default="agent-friend-voice-lab")
    replay.add_argument("--callback-path", choices=("state", "subtitle"), default="state")
    replay.add_argument("--skip-llm", action="store_true")
    replay.add_argument("--allow-llm", action="store_true")
    replay.add_argument("--synthetic-tts-delay-ms", type=int, default=400)
    replay.add_argument("--log-dir", type=Path, default=default_log_dir())
    replay.add_argument("--log-wait-ms", type=int, default=500)

    matrix = subparsers.add_parser(
        "matrix",
        help="Start isolated local services and run a real LLM A/B latency matrix.",
    )
    matrix.add_argument(
        "--allow-llm",
        action="store_true",
        help="Required: matrix sends one real LLM request per scenario run.",
    )
    matrix.add_argument(
        "--config-mode",
        choices=("desktop", "legacy-dotenv", "env-only"),
        default=os.environ.get("AGENT_FRIEND_CONFIG_MODE", "desktop"),
    )
    matrix.add_argument(
        "--scenario",
        choices=tuple(MATRIX_SCENARIOS),
        action="append",
        help="Scenario to run. Can be repeated; defaults to baseline, short_reply, max_tokens_64.",
    )
    matrix.add_argument("--runs-per-scenario", type=int, default=1)
    matrix.add_argument("--agent-port", type=int, default=0, help="0 selects a free local port.")
    matrix.add_argument("--voice-port", type=int, default=0, help="0 selects a free local port.")
    matrix.add_argument("--startup-timeout-s", type=float, default=30.0)
    matrix.add_argument(
        "--log-root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "agent-friend-voice-latency-lab",
    )
    matrix.add_argument("--user-text", default="hello, please answer in one short sentence")
    matrix.add_argument("--model", default="agent-friend-voice-lab")
    matrix.add_argument("--log-wait-ms", type=int, default=1000)

    live = subparsers.add_parser(
        "live",
        help="Start isolated services and run a real Volc RTC + LLM voice call.",
    )
    live.add_argument(
        "--allow-live",
        action="store_true",
        help="Required: live calls real Volc RTC/ASR/TTS and the real LLM path.",
    )
    live.add_argument(
        "--config-mode",
        choices=("desktop", "legacy-dotenv", "env-only"),
        default=os.environ.get("AGENT_FRIEND_CONFIG_MODE", "desktop"),
    )
    live.add_argument(
        "--scenario",
        choices=tuple(MATRIX_SCENARIOS),
        action="append",
        help="Scenario to run. Can be repeated; defaults to baseline.",
    )
    live.add_argument("--runs-per-scenario", type=int, default=1)
    live.add_argument("--agent-port", type=int, default=0, help="0 selects a free local port.")
    live.add_argument("--voice-port", type=int, default=0, help="0 selects a free local port.")
    live.add_argument("--http-port", type=int, default=0, help="0 selects a free local port.")
    live.add_argument("--startup-timeout-s", type=float, default=45.0)
    live.add_argument("--call-timeout-s", type=float, default=120.0)
    live.add_argument("--hold-ms", type=int, default=55_000)
    live.add_argument("--log-wait-ms", type=int, default=3000)
    live.add_argument(
        "--log-root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "agent-friend-voice-latency-lab",
    )
    live.add_argument(
        "--speech-text",
        default="你好，请用一句话介绍你现在能做什么。",
        help="Text used when generating the default fake microphone wav on Windows.",
    )
    live.add_argument(
        "--pre-silence-ms",
        type=int,
        default=8_000,
        help="Leading silence before generated speech, so Volc agent is listening first.",
    )
    live.add_argument("--audio-file", type=Path, default=None)
    live.add_argument("--headful", action="store_true", help="Show Chromium instead of headless.")
    live.add_argument("--cloudflared-bin", default="cloudflared")

    summarize = subparsers.add_parser("summarize", help="Summarize logs for a trace id.")
    summarize.add_argument("trace_id")
    summarize.add_argument("--log-dir", type=Path, default=default_log_dir())

    args = parser.parse_args()
    if args.cmd == "fixtures":
        run_fixtures()
    elif args.cmd == "replay":
        raise SystemExit(asyncio.run(run_replay(args)))
    elif args.cmd == "matrix":
        raise SystemExit(asyncio.run(run_matrix(args)))
    elif args.cmd == "live":
        raise SystemExit(asyncio.run(run_live(args)))
    elif args.cmd == "summarize":
        print_summary(load_latency_events(args.trace_id, args.log_dir))


def run_fixtures() -> None:
    for name, payload in {
        "listening": make_state_payload(stage="listening", code=1, round_id=1, user_id="user-1"),
        "subtitle": make_subtitle_payload(round_id=1, user_id="user-1", text="hello"),
        "answering": make_state_payload(stage="answering", code=3, round_id=1, user_id="user-1"),
    }.items():
        decoded = decode_volc_callback_payload(payload)
        print(f"[fixture] {name}: {json.dumps(asdict(decoded), ensure_ascii=False)}")


async def run_replay(args: argparse.Namespace) -> int:
    if not args.skip_llm and not args.allow_llm:
        print(
            "replay would call the real local agent_bridge/LLM path. "
            "Pass --allow-llm to continue, or --skip-llm to only test callbacks.",
            file=sys.stderr,
        )
        return 2

    voice_url = args.voice_url.rstrip("/")
    trace_id = args.trace_id or f"voice-lab-{uuid.uuid4()}"
    timeout = httpx.Timeout(None)
    async with httpx.AsyncClient(timeout=timeout) as client:
        await require_health(client, voice_url)
        call = await start_call(client, voice_url, trace_id, args.session_id)
        call_id = call["call_id"]
        user_id = call["user_id"]
        print(f"[lab] trace_id={trace_id}")
        print(f"[lab] call_id={call_id}")
        print(f"[lab] session_id={call['session_id']}")

        try:
            await post_callback(
                client,
                voice_url,
                call_id,
                args.callback_path,
                make_state_payload(stage="listening", code=1, round_id=1, user_id=user_id),
            )
            await post_callback(
                client,
                voice_url,
                call_id,
                args.callback_path,
                make_subtitle_payload(round_id=1, user_id=user_id, text=args.user_text),
            )
            await post_callback(
                client,
                voice_url,
                call_id,
                args.callback_path,
                make_state_payload(stage="thinking", code=2, round_id=1, user_id=user_id),
            )

            if args.skip_llm:
                await asyncio.sleep(max(args.synthetic_tts_delay_ms, 0) / 1000)
                await post_callback(
                    client,
                    voice_url,
                    call_id,
                    args.callback_path,
                    make_state_payload(stage="answering", code=3, round_id=1, user_id="bot-lab"),
                )
                await post_callback(
                    client,
                    voice_url,
                    call_id,
                    args.callback_path,
                    make_subtitle_payload(
                        round_id=1,
                        user_id="bot-lab",
                        text="synthetic lab response",
                    ),
                )
            else:
                bot_text = await run_custom_llm_round(
                    client,
                    voice_url,
                    call_id,
                    args.model,
                    args.user_text,
                    args.callback_path,
                )
                await post_callback(
                    client,
                    voice_url,
                    call_id,
                    args.callback_path,
                    make_subtitle_payload(
                        round_id=1,
                        user_id="bot-lab",
                        text=bot_text or "empty lab response",
                    ),
                )

            await post_callback(
                client,
                voice_url,
                call_id,
                args.callback_path,
                make_state_payload(stage="answerFinish", code=5, round_id=1, user_id=user_id),
            )
        finally:
            await client.post(f"{voice_url}/voice/calls/{call_id}/stop")

    await asyncio.sleep(max(args.log_wait_ms, 0) / 1000)
    print_summary(load_latency_events(trace_id, args.log_dir))
    return 0


async def run_matrix(args: argparse.Namespace) -> int:
    if not args.allow_llm:
        print(
            "matrix would call the real local agent_bridge/LLM path once per scenario run. "
            "Pass --allow-llm to continue.",
            file=sys.stderr,
        )
        return 2
    if args.runs_per_scenario < 1:
        print("--runs-per-scenario must be >= 1", file=sys.stderr)
        return 2

    scenario_names = dedupe_preserve_order(
        args.scenario or ["baseline", "short_reply", "max_tokens_64"]
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact_root = args.log_root / stamp
    artifact_root.mkdir(parents=True, exist_ok=True)
    total_llm_calls = len(scenario_names) * args.runs_per_scenario
    print(f"[matrix] scenarios={','.join(scenario_names)}")
    print(f"[matrix] real LLM requests={total_llm_calls}")
    print(f"[matrix] artifact_root={artifact_root}")

    results: list[ReplayResult] = []
    for scenario_name in scenario_names:
        scenario = MATRIX_SCENARIOS[scenario_name]
        scenario_dir = artifact_root / scenario.name
        log_dir = scenario_dir / "logs"
        data_dir = scenario_dir / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        agent_port = args.agent_port if args.agent_port > 0 else find_free_port()
        voice_port = (
            args.voice_port if args.voice_port > 0 else find_free_port(excluding={agent_port})
        )
        env = build_matrix_env(
            scenario=scenario,
            data_dir=data_dir,
            log_dir=log_dir,
            agent_port=agent_port,
            voice_port=voice_port,
        )
        processes: list[ManagedProcess] = []
        try:
            print(
                f"[matrix] starting scenario={scenario.name} "
                f"agent_port={agent_port} voice_port={voice_port}"
            )
            processes.append(
                start_managed_process(
                    "agent_bridge",
                    [sys.executable, "-m", "agent_bridge", "--config-mode", args.config_mode],
                    env,
                    log_dir,
                )
            )
            await wait_for_health(
                f"http://127.0.0.1:{agent_port}/healthz",
                "agent_bridge",
                timeout_s=args.startup_timeout_s,
            )
            processes.append(
                start_managed_process(
                    "voice_bridge",
                    [sys.executable, "-m", "voice_bridge", "--config-mode", args.config_mode],
                    env,
                    log_dir,
                )
            )
            await wait_for_health(
                f"http://127.0.0.1:{voice_port}/healthz",
                "voice_bridge",
                timeout_s=args.startup_timeout_s,
            )

            for run_index in range(args.runs_per_scenario):
                trace_id = f"voice-lab-{scenario.name}-{run_index + 1}-{uuid.uuid4()}"
                replay_args = argparse.Namespace(
                    voice_url=f"http://127.0.0.1:{voice_port}",
                    session_id="",
                    trace_id=trace_id,
                    user_text=args.user_text,
                    model=args.model,
                    callback_path="state",
                    skip_llm=False,
                    allow_llm=True,
                    synthetic_tts_delay_ms=400,
                    log_dir=log_dir,
                    log_wait_ms=args.log_wait_ms,
                )
                rc = await run_replay(replay_args)
                if rc != 0:
                    return rc
                events = load_latency_events(trace_id, log_dir)
                results.append(
                    ReplayResult(
                        scenario=scenario.name,
                        trace_id=trace_id,
                        log_dir=log_dir,
                        events=events,
                    )
                )
        except Exception as exc:
            print(f"[matrix] scenario={scenario.name} failed: {exc}", file=sys.stderr)
            for process in processes:
                print_tail(process.log_path)
            return 1
        finally:
            stop_processes(processes)

    print_matrix_summary(results)
    return 0


async def run_live(args: argparse.Namespace) -> int:
    if not args.allow_live:
        print(
            "live would call real Volc RTC/ASR/TTS and the real local agent_bridge/LLM path. "
            "Pass --allow-live to continue.",
            file=sys.stderr,
        )
        return 2
    if args.runs_per_scenario < 1:
        print("--runs-per-scenario must be >= 1", file=sys.stderr)
        return 2
    if not shutil.which(args.cloudflared_bin):
        print(
            f"cloudflared not found: {args.cloudflared_bin}. "
            "Install cloudflared or pass --cloudflared-bin.",
            file=sys.stderr,
        )
        return 2
    if not shutil.which("node"):
        print(
            "node not found. Install Node.js and run scripts/frontend/install first.",
            file=sys.stderr,
        )
        return 2
    if not playwright_package_path().exists():
        print(
            "frontend/node_modules/playwright is missing. Run scripts/frontend/install first.",
            file=sys.stderr,
        )
        return 2

    scenario_names = dedupe_preserve_order(args.scenario or ["baseline"])
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact_root = args.log_root / f"{stamp}-live"
    artifact_root.mkdir(parents=True, exist_ok=True)
    total_calls = len(scenario_names) * args.runs_per_scenario
    print(f"[live] scenarios={','.join(scenario_names)}")
    print(f"[live] real Volc RTC/LLM calls={total_calls}")
    print(f"[live] artifact_root={artifact_root}")

    results: list[ReplayResult] = []
    for scenario_name in scenario_names:
        scenario = MATRIX_SCENARIOS[scenario_name]
        scenario_dir = artifact_root / scenario.name
        log_dir = scenario_dir / "logs"
        data_dir = scenario_dir / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        agent_port = args.agent_port if args.agent_port > 0 else find_free_port()
        voice_port = (
            args.voice_port if args.voice_port > 0 else find_free_port(excluding={agent_port})
        )
        http_port = (
            args.http_port
            if args.http_port > 0
            else find_free_port(excluding={agent_port, voice_port})
        )
        env = build_matrix_env(
            scenario=scenario,
            data_dir=data_dir,
            log_dir=log_dir,
            agent_port=agent_port,
            voice_port=voice_port,
        )
        processes: list[ManagedProcess] = []
        try:
            print(
                f"[live] starting scenario={scenario.name} "
                f"agent_port={agent_port} voice_port={voice_port} http_port={http_port}"
            )
            processes.append(
                start_managed_process(
                    "agent_bridge",
                    [sys.executable, "-m", "agent_bridge", "--config-mode", args.config_mode],
                    env,
                    log_dir,
                )
            )
            await wait_for_health(
                f"http://127.0.0.1:{agent_port}/healthz",
                "agent_bridge",
                timeout_s=args.startup_timeout_s,
            )

            cloudflared = start_managed_process(
                "cloudflared",
                [
                    args.cloudflared_bin,
                    "tunnel",
                    "--url",
                    f"http://127.0.0.1:{voice_port}",
                ],
                env,
                log_dir,
            )
            processes.append(cloudflared)
            public_url = await wait_for_cloudflared_url(
                cloudflared.log_path,
                timeout_s=args.startup_timeout_s,
            )
            env["VOICE_BRIDGE_PUBLIC_URL"] = public_url
            print(f"[live] public_url={public_url}")

            processes.append(
                start_managed_process(
                    "voice_bridge",
                    [sys.executable, "-m", "voice_bridge", "--config-mode", args.config_mode],
                    env,
                    log_dir,
                )
            )
            await wait_for_health(
                f"http://127.0.0.1:{voice_port}/healthz",
                "voice_bridge",
                timeout_s=args.startup_timeout_s,
            )

            processes.append(
                start_managed_process(
                    "http_server",
                    [
                        sys.executable,
                        "-m",
                        "http.server",
                        str(http_port),
                        "--bind",
                        "127.0.0.1",
                    ],
                    env,
                    log_dir,
                )
            )
            await wait_for_health(
                f"http://127.0.0.1:{http_port}/scripts/voice-latency-lab/live_rtc_page.html",
                "http_server",
                timeout_s=args.startup_timeout_s,
            )

            audio_file = ensure_audio_file(args, scenario_dir)
            for run_index in range(args.runs_per_scenario):
                trace_id = f"voice-live-{scenario.name}-{run_index + 1}-{uuid.uuid4()}"
                live_result = run_live_browser(
                    voice_url=f"http://127.0.0.1:{voice_port}",
                    page_url=(
                        f"http://127.0.0.1:{http_port}/scripts/voice-latency-lab/live_rtc_page.html"
                    ),
                    trace_id=trace_id,
                    audio_file=audio_file,
                    hold_ms=args.hold_ms,
                    timeout_s=args.call_timeout_s,
                    headless=not args.headful,
                    log_dir=log_dir,
                )
                print(
                    f"[live] call_id={live_result.get('callId', '')} "
                    f"session_id={live_result.get('sessionId', '')}"
                )
                await asyncio.sleep(max(args.log_wait_ms, 0) / 1000)
                events = load_latency_events(trace_id, log_dir)
                print_summary(events)
                results.append(
                    ReplayResult(
                        scenario=scenario.name,
                        trace_id=trace_id,
                        log_dir=log_dir,
                        events=events,
                    )
                )
        except Exception as exc:
            print(f"[live] scenario={scenario.name} failed: {exc}", file=sys.stderr)
            for process in processes:
                print_tail(process.log_path)
            return 1
        finally:
            stop_processes(processes)

    print_matrix_summary(results)
    return 0


async def require_health(client: httpx.AsyncClient, voice_url: str) -> None:
    resp = await client.get(f"{voice_url}/healthz")
    resp.raise_for_status()


async def start_call(
    client: httpx.AsyncClient,
    voice_url: str,
    trace_id: str,
    session_id: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {"trace_id": trace_id, "defer_start": True}
    if session_id:
        body["session_id"] = session_id
    resp = await client.post(f"{voice_url}/voice/calls", json=body)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("voice call response is not an object")
    return data


async def run_custom_llm_round(
    client: httpx.AsyncClient,
    voice_url: str,
    call_id: str,
    model: str,
    user_text: str,
    callback_path: str,
) -> str:
    observer = SseTextObserver()
    collector = SseTextCollector()
    answered = False
    start = time.perf_counter()
    async with client.stream(
        "POST",
        f"{voice_url}/voice/llm/{call_id}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": user_text}],
            "stream": True,
        },
    ) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes():
            collector.feed(chunk)
            first_text = observer.feed(chunk)
            if first_text is not None and not answered:
                answered = True
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                print(f"[lab] first real text after {elapsed_ms}ms")
                await post_callback(
                    client,
                    voice_url,
                    call_id,
                    callback_path,
                    make_state_payload(stage="answering", code=3, round_id=1, user_id="bot-lab"),
                )
    if not answered:
        await post_callback(
            client,
            voice_url,
            call_id,
            callback_path,
            make_state_payload(stage="answering", code=3, round_id=1, user_id="bot-lab"),
        )
    return collector.text()


async def post_callback(
    client: httpx.AsyncClient,
    voice_url: str,
    call_id: str,
    callback_path: str,
    payload: dict[str, Any],
) -> None:
    resp = await client.post(f"{voice_url}/voice/callbacks/{callback_path}/{call_id}", json=payload)
    resp.raise_for_status()


def make_state_payload(*, stage: str, code: int, round_id: int, user_id: str) -> dict[str, Any]:
    body = {
        "EventTime": int(time.time() * 1000),
        "RoundID": round_id,
        "Stage": {"Code": code, "Description": stage},
        "TaskId": "voice-lab",
        "UserID": user_id,
    }
    return encode_volc_message("conv", body)


def make_subtitle_payload(*, round_id: int, user_id: str, text: str) -> dict[str, Any]:
    body = {
        "type": "subtitle",
        "data": [
            {
                "definite": True,
                "roundId": round_id,
                "sequence": 1,
                "text": text,
                "timestamp": int(time.time() * 1000),
                "userId": user_id,
            }
        ],
    }
    return encode_volc_message("subv", body)


def encode_volc_message(prefix: str, body: dict[str, Any]) -> dict[str, Any]:
    raw = prefix.encode("ascii") + b"\x00\x00\x00" + json.dumps(body).encode("utf-8")
    return {
        "message": base64.b64encode(raw).decode("ascii"),
        "binary": True,
        "signature": "agent-friend-lab",
    }


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_matrix_env(
    *,
    scenario: MatrixScenario,
    data_dir: Path,
    log_dir: Path,
    agent_port: int,
    voice_port: int,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "AGENT_FRIEND_DATA_DIR": str(data_dir),
            "AGENT_FRIEND_LOG_DIR": str(log_dir),
            "AGENT_BRIDGE_PORT": str(agent_port),
            "AGENT_BRIDGE_IM_ENABLED": "false",
            "AGENT_BRIDGE_VOICE_BRIDGE_BASE_URL": f"http://127.0.0.1:{voice_port}",
            "VOICE_BRIDGE_PORT": str(voice_port),
            "VOICE_BRIDGE_AGENT_BRIDGE_URL": f"http://127.0.0.1:{agent_port}",
        }
    )
    env.update(scenario.env)
    return env


def start_managed_process(
    name: str,
    command: list[str],
    env: dict[str, str],
    log_dir: Path,
) -> ManagedProcess:
    log_path = log_dir / f"{name}.stdout.log"
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=repo_root(),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print(f"[process] started {name} pid={process.pid} log={log_path}")
    return ManagedProcess(
        name=name,
        process=process,
        log_handle=log_handle,
        log_path=log_path,
    )


async def wait_for_health(url: str, name: str, *, timeout_s: float) -> None:
    deadline = time.perf_counter() + timeout_s
    last_error = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
        while time.perf_counter() < deadline:
            try:
                response = await client.get(url)
                if 200 <= response.status_code < 300:
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(0.25)
    raise RuntimeError(f"{name} did not become healthy at {url}: {last_error}")


async def wait_for_cloudflared_url(log_path: Path, *, timeout_s: float) -> str:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            url = first_cloudflared_url(text)
            if url:
                return url
        await asyncio.sleep(0.5)
    raise RuntimeError(f"cloudflared did not emit a trycloudflare URL in {log_path}")


def first_cloudflared_url(text: str) -> str:
    import re

    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
    return match.group(0) if match else ""


def stop_processes(processes: list[ManagedProcess]) -> None:
    for managed in reversed(processes):
        stop_process_tree(managed.process)
        with contextlib.suppress(Exception):
            managed.log_handle.close()


def stop_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def find_free_port(*, excluding: set[int] | None = None) -> int:
    excluded = excluding or set()
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in excluded:
            return port
    raise RuntimeError("could not allocate a free local port")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def playwright_package_path() -> Path:
    return repo_root() / "frontend" / "node_modules" / "playwright"


def ensure_audio_file(args: argparse.Namespace, scenario_dir: Path) -> Path:
    if args.audio_file is not None:
        path = args.audio_file.resolve()
        if not path.exists():
            raise RuntimeError(f"audio file does not exist: {path}")
        return path
    if os.name != "nt":
        raise RuntimeError("--audio-file is required outside Windows")

    audio_dir = scenario_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    raw_wav = audio_dir / "speech.raw.wav"
    final_wav = audio_dir / "speech-with-leading-silence.wav"
    generate_windows_tts_wav(args.speech_text, raw_wav)
    surround_with_silence_wav(
        raw_wav,
        final_wav,
        pre_silence_ms=max(args.pre_silence_ms, 0),
        post_silence_ms=max(args.hold_ms, 10_000),
    )
    print(
        "[live] generated fake microphone audio="
        f"{final_wav} pre_silence_ms={max(args.pre_silence_ms, 0)} "
        f"post_silence_ms={max(args.hold_ms, 10_000)}"
    )
    return final_wav


def generate_windows_tts_wav(text: str, output: Path) -> None:
    script = output.with_suffix(".ps1")
    payload = f"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -eq "zh-CN" }} | Select-Object -First 1
if ($voice) {{ $synth.SelectVoice($voice.VoiceInfo.Name) }}
$synth.Rate = 0
$synth.Volume = 100
$synth.SetOutputToWaveFile('{str(output).replace("'", "''")}')
$synth.Speak('{text.replace("'", "''")}')
$synth.Dispose()
"""
    script.write_text(payload, encoding="utf-8-sig")
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            script.unlink()
    if completed.returncode != 0 or not output.exists():
        raise RuntimeError(
            "Windows TTS wav generation failed: "
            f"stdout={completed.stdout[-500:]} stderr={completed.stderr[-500:]}"
        )


def surround_with_silence_wav(
    source: Path,
    target: Path,
    *,
    pre_silence_ms: int,
    post_silence_ms: int,
) -> None:
    with wave.open(str(source), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())
    pre_silence = silence_bytes(params, pre_silence_ms)
    post_silence = silence_bytes(params, post_silence_ms)
    with wave.open(str(target), "wb") as dst:
        dst.setparams(params)
        dst.writeframes(pre_silence)
        dst.writeframes(frames)
        dst.writeframes(post_silence)


def silence_bytes(params: Any, silence_ms: int) -> bytes:
    silence_frames = int(params.framerate * max(silence_ms, 0) / 1000)
    return b"\x00" * silence_frames * params.nchannels * params.sampwidth


def run_live_browser(
    *,
    voice_url: str,
    page_url: str,
    trace_id: str,
    audio_file: Path,
    hold_ms: int,
    timeout_s: float,
    headless: bool,
    log_dir: Path,
) -> dict[str, Any]:
    config = {
        "repoRoot": str(repo_root()),
        "voiceUrl": voice_url,
        "pageUrl": page_url,
        "traceId": trace_id,
        "audioFile": str(audio_file),
        "holdMs": hold_ms,
        "headless": headless,
    }
    config_path = log_dir / f"live-browser-{trace_id}.json"
    browser_log = log_dir / f"live-browser-{trace_id}.log"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [
        "node",
        str(repo_root() / "scripts" / "voice-latency-lab" / "live_rtc_runner.mjs"),
        str(config_path),
    ]
    process = subprocess.Popen(
        command,
        cwd=repo_root(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        stop_process_tree(process)
        stdout, stderr = process.communicate()
        browser_log.write_text(stdout + stderr, encoding="utf-8")
        raise RuntimeError(f"live browser runner timed out after {timeout_s}s") from exc
    browser_log.write_text(stdout + stderr, encoding="utf-8")
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    if process.returncode != 0:
        raise RuntimeError(f"live browser runner failed, see {browser_log}")
    return parse_live_browser_result(stdout)


def parse_live_browser_result(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if not line.startswith("LIVE_RTC_RESULT "):
            continue
        payload = line.removeprefix("LIVE_RTC_RESULT ")
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
    raise RuntimeError("live browser runner did not print LIVE_RTC_RESULT")


def print_tail(path: Path, *, max_lines: int = 40) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    print(f"[process] tail {path}:")
    for line in lines:
        print(f"  {line}")


def load_latency_events(trace_id: str, logs_dir: Path) -> list[LatencyEvent]:
    paths = [
        logs_dir / "dev-voice.log",
        logs_dir / "voice_bridge.log",
        logs_dir / "dev-bridge.log",
        logs_dir / "agent_bridge.log",
        logs_dir / "tauri.log",
    ]
    paths.extend(sorted(logs_dir.glob("live-browser-*.log")))
    events: list[LatencyEvent] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            event = parse_latency_line(path, line)
            if event and event.fields.get("trace_id") == trace_id:
                events.append(event)
    return events


def parse_latency_line(path: Path, line: str) -> LatencyEvent | None:
    raw_fields = ""
    marker = "voice_latency "
    if marker in line:
        raw_fields = line.split(marker, 1)[1]
    else:
        marker = "[voice][latency] "
        if marker not in line:
            return None
        raw_fields = line.split(marker, 1)[1]

    fields: dict[str, str] = {}
    for part in shlex.split(raw_fields):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key] = value
    normalize_latency_fields(fields)
    if "event" not in fields:
        return None
    return LatencyEvent(source=path, timestamp=parse_timestamp(line), fields=fields)


def normalize_latency_fields(fields: dict[str, str]) -> None:
    aliases = {
        "traceId": "trace_id",
        "callId": "call_id",
        "sessionId": "session_id",
        "roundId": "round_id",
        "elapsedMs": "elapsed_ms",
        "roundElapsedMs": "round_elapsed_ms",
        "remoteUserId": "remote_user_id",
        "volumeLevel": "volume_level",
    }
    for source, target in aliases.items():
        if source in fields and target not in fields:
            fields[target] = fields[source]


def parse_timestamp(line: str) -> datetime | None:
    try:
        if line.startswith("["):
            raw = line[1:24]
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S,%f")
        raw = line.split(" ", 1)[0]
        if "T" in raw:
            return _to_local_naive(datetime.fromisoformat(raw))
    except ValueError:
        return None
    return None


def _to_local_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def print_summary(events: list[LatencyEvent]) -> None:
    if not events:
        print("[summary] no matching voice_latency events found")
        return

    print(f"[summary] matched events: {len(events)}")
    experiments = [
        event for event in events if event.fields.get("event") == "agent_bridge_voice_experiments"
    ]
    if experiments:
        latest = experiments[-1].fields
        print(
            "[summary] experiments: "
            f"short_reply={latest.get('short_reply')} "
            f"max_tokens={latest.get('max_tokens', '')} "
            f"disable_tools={latest.get('disable_tools')}"
        )

    start_call = first_event(events, "call_created")
    start_voice = first_event(events, "start_voice_chat_ok")
    if start_call:
        print(f"[summary] call_created elapsed_ms={start_call.fields.get('elapsed_ms')}")
    if start_voice:
        print(f"[summary] start_voice_chat_ok elapsed_ms={start_voice.fields.get('elapsed_ms')}")

    for round_id in sorted(round_ids(events)):
        print_round_summary(round_id, events)


def print_matrix_summary(results: list[ReplayResult]) -> None:
    if not results:
        print("[matrix] no replay results")
        return
    print("[matrix] compact summary")
    for result in results:
        fields = compact_round_metrics(result.events)
        print(
            f"  {result.scenario} trace_id={result.trace_id} "
            f"round={fields.get('round_id', 'n/a')} "
            f"first_text_ms={fields.get('first_text_ms', 'n/a')} "
            f"last_chunk_ms={fields.get('last_chunk_ms', 'n/a')} "
            f"inbound_to_bot_subtitle_ms={fields.get('inbound_to_bot_subtitle_ms', 'n/a')} "
            f"inbound_to_remote_audio_ms={fields.get('inbound_to_remote_audio_ms', 'n/a')} "
            f"inbound_to_remote_volume_ms={fields.get('inbound_to_remote_volume_ms', 'n/a')} "
            f"log_dir={result.log_dir}"
        )


def compact_round_metrics(events: list[LatencyEvent]) -> dict[str, str]:
    for round_id in sorted(round_ids(events)):
        metrics: dict[str, str] = {"round_id": str(round_id)}
        round_events = [
            event
            for event in events
            if int_or_none(event.fields.get("round_seq")) == round_id
            or int_or_none(event.fields.get("round_id")) == round_id
        ]
        first_text = first_event(round_events, "llm_proxy_first_real_text_chunk")
        last_chunk = first_event(round_events, "llm_proxy_last_chunk")
        inbound = first_event(round_events, "llm_proxy_inbound")
        bot_subtitle = first_callback_after(round_events, stage="subtitle", after=inbound)
        remote_audio = first_event_after(
            round_events,
            "frontend_remote_audio_first_frame",
            after=inbound,
        )
        remote_volume = first_event_after(
            round_events,
            "frontend_first_remote_audio_volume",
            after=inbound,
        )
        if first_text:
            metrics["first_text_ms"] = first_text.fields.get("elapsed_ms", "")
        if last_chunk:
            metrics["last_chunk_ms"] = last_chunk.fields.get("elapsed_ms", "")
        if inbound and bot_subtitle:
            metrics["inbound_to_bot_subtitle_ms"] = delta_ms(inbound, bot_subtitle)
        if inbound and remote_audio:
            metrics["inbound_to_remote_audio_ms"] = delta_ms(inbound, remote_audio)
        if inbound and remote_volume:
            metrics["inbound_to_remote_volume_ms"] = delta_ms(inbound, remote_volume)
        if first_text or last_chunk or bot_subtitle or remote_audio or remote_volume:
            return metrics
    return {}


def print_round_summary(round_id: int, events: list[LatencyEvent]) -> None:
    round_events = [
        event
        for event in events
        if int_or_none(event.fields.get("round_seq")) == round_id
        or int_or_none(event.fields.get("round_id")) == round_id
    ]
    inbound = first_event(round_events, "llm_proxy_inbound")
    first_text = first_event(round_events, "llm_proxy_first_real_text_chunk")
    last_chunk = first_event(round_events, "llm_proxy_last_chunk")
    user_subtitle = first_callback(round_events, stage="subtitle", speaker="user")
    answering = first_callback(round_events, stage="answering")
    bot_subtitle = first_callback_after(round_events, stage="subtitle", after=inbound)
    finish = first_callback(round_events, stage="answerFinish")
    remote_audio = first_event_after(
        round_events, "frontend_remote_audio_first_frame", after=inbound
    )
    remote_volume = first_event_after(
        round_events,
        "frontend_first_remote_audio_volume",
        after=inbound,
    )

    print(f"[round {round_id}]")
    if user_subtitle and inbound:
        print(f"  user subtitle -> llm_proxy_inbound: {delta_ms(user_subtitle, inbound)}ms")
    if first_text:
        print(f"  llm_proxy_inbound -> first text: {first_text.fields.get('elapsed_ms')}ms")
    if last_chunk:
        print(f"  llm_proxy_inbound -> last chunk: {last_chunk.fields.get('elapsed_ms')}ms")
    if inbound and answering:
        print(f"  llm_proxy_inbound -> answering: {delta_ms(inbound, answering)}ms")
    if inbound and bot_subtitle:
        print(f"  llm_proxy_inbound -> bot subtitle: {delta_ms(inbound, bot_subtitle)}ms")
    if inbound and remote_audio:
        print(
            f"  llm_proxy_inbound -> remote audio first frame: {delta_ms(inbound, remote_audio)}ms"
        )
    if inbound and remote_volume:
        print(
            "  llm_proxy_inbound -> first remote nonzero volume: "
            f"{delta_ms(inbound, remote_volume)}ms"
        )
    if inbound and finish:
        print(f"  llm_proxy_inbound -> answerFinish: {delta_ms(inbound, finish)}ms")


def round_ids(events: list[LatencyEvent]) -> set[int]:
    ids: set[int] = set()
    for event in events:
        for key in ("round_seq", "round_id"):
            value = int_or_none(event.fields.get(key))
            if value is not None:
                ids.add(value)
    return ids


def first_event(events: list[LatencyEvent], name: str) -> LatencyEvent | None:
    return next((event for event in events if event.fields.get("event") == name), None)


def first_event_after(
    events: list[LatencyEvent],
    name: str,
    *,
    after: LatencyEvent | None,
) -> LatencyEvent | None:
    if after is None or after.timestamp is None:
        return first_event(events, name)
    for event in events:
        if event.fields.get("event") != name:
            continue
        if event.timestamp is None or event.timestamp < after.timestamp:
            continue
        return event
    return None


def first_callback(
    events: list[LatencyEvent],
    *,
    stage: str,
    speaker: str | None = None,
) -> LatencyEvent | None:
    for event in events:
        if not event.fields.get("event", "").startswith("volc_"):
            continue
        if event.fields.get("state") != stage:
            continue
        if speaker is not None and event.fields.get("speaker") != speaker:
            continue
        return event
    return None


def first_callback_after(
    events: list[LatencyEvent],
    *,
    stage: str,
    after: LatencyEvent | None,
) -> LatencyEvent | None:
    if after is None or after.timestamp is None:
        return first_callback(events, stage=stage)
    for event in events:
        if event.timestamp is None or event.timestamp < after.timestamp:
            continue
        if not event.fields.get("event", "").startswith("volc_"):
            continue
        if event.fields.get("state") == stage and event.fields.get("speaker") != "user":
            return event
    return None


def delta_ms(a: LatencyEvent, b: LatencyEvent) -> str:
    if a.timestamp is None or b.timestamp is None:
        return "n/a"
    return str(int((b.timestamp - a.timestamp).total_seconds() * 1000))


def int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
