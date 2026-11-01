"""Opt-in source runner using AF's real sessions, tool loop and scheduler."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.paths import attachments_dir, sessions_dir
from agent.runtime import AgentRuntime
from agent.runtime.inbox import SystemTriggerEvent
from agent.runtime.models import ConsumerAbandonPolicy, RunOutcome
from agent.skills.loader import SkillLoader
from agent.skills.models import SkillRoot
from agent.skills.package_snapshot import package_digest
from agent.tool_runtime.policy import ToolRunPolicy
from agent.tools.registry import ToolRegistry
from llm_providers.binding import bind_language_model
from llm_providers.multimodal.tool_images import supports_tool_images

from agent import (
    AttachmentStore,
    JsonlSessionStore,
    MarkdownPromptBuilder,
    SessionManager,
    default_context_manager,
)
from llm_providers import LanguageModel, ModelRuntimeConfig, MultimodalInputAdapter

from .af_tools import PokemonAct, PokemonFinish, PokemonNotes, PokemonObserve
from .budget import BudgetModel, PlayBudget, PlayStopped
from .runtime.client import GameClient
from .runtime.protocol import GameError, atomic_json, controller_lock

GOAL = (
    "独立游玩此中文版宝可梦火红，直至首次击败冠军并进入名人堂。只根据游戏截图与自己的探索笔记决策。"
)


def play(
    root: Path,
    skill_path: Path,
    *,
    model: str,
    persona: str,
    max_calls: int,
    max_seconds: int,
    allow_llm: bool,
    reasoning_preset: str | None = None,
    continue_existing: bool = False,
    model_factory: Callable[[ModelRuntimeConfig], LanguageModel] | None = None,
    session_root: Path | None = None,
    asset_root: Path | None = None,
) -> dict[str, Any]:
    if not allow_llm:
        raise GameError(
            "llm_not_authorized",
            "此命令实际调用模型；说明模型与预算并获得本次授权后，显式传 --allow-llm。",
        )
    if not 1 <= max_calls <= 100000 or not 1 <= max_seconds <= 604800:
        raise GameError("invalid_budget", "调用数必须为 1–100000，时长为 1–604800 秒。")
    client = GameClient(root)
    catalog = SkillLoader().load((SkillRoot("personal", skill_path.parent),), generation=1)
    matches = [
        r
        for r in catalog.entries
        if r.name == "pokemon-firered" and r.package_root.resolve() == skill_path.resolve()
    ]
    if len(matches) != 1:
        raise GameError("skill_invalid", "个人游戏 Skill 未通过 AF catalog 验证。")
    digest = package_digest(skill_path)
    instructions = (skill_path / "SKILL.md").read_text(encoding="utf-8")
    # A separate lifetime lock prevents two supervisors; per-command lock remains available
    # to pause/status commands, and is never held while waiting for the model.
    supervisor_root = root / "supervisor"
    supervisor_root.mkdir(exist_ok=True)
    with controller_lock(supervisor_root):
        client.supervisor_owned = True
        if model_factory is None:
            from agent_bridge.runtime_config import resolve_bridge_runtime_config

            from runtime_config import config_mode_from_env, credential_source_for_mode

            model_factory = resolve_bridge_runtime_config(
                credential_source_for_mode(config_mode_from_env())
            ).llm_client_factory
        return _play_locked(
            client,
            skill_path,
            digest,
            instructions,
            model,
            persona,
            max_calls,
            max_seconds,
            continue_existing,
            model_factory,
            session_root or sessions_dir(),
            asset_root or attachments_dir(),
            reasoning_preset,
        )


def _play_locked(
    client: GameClient,
    skill_path: Path,
    digest: str,
    instructions: str,
    model: str,
    persona: str,
    max_calls: int,
    max_seconds: int,
    continue_existing: bool,
    model_factory: Callable[[ModelRuntimeConfig], LanguageModel],
    session_root: Path,
    asset_root: Path,
    reasoning_preset: str | None,
) -> dict[str, Any]:
    root = client.root
    if (root / "recovery_pending.json").exists():
        raise GameError("recovery_unknown", "有尚未提交的恢复事务，保持暂停，不允许启动模型。")
    budget = PlayBudget(root)
    store = JsonlSessionStore(session_root)
    assets = AttachmentStore(asset_root)
    registry = ToolRegistry(
        [
            PokemonObserve(client, assets),
            PokemonAct(client, assets),
            PokemonNotes(root),
            PokemonFinish(root),
        ]
    )
    model_bindings = ExitStack()

    def factory(config: ModelRuntimeConfig) -> LanguageModel:
        underlying = model_bindings.enter_context(bind_language_model(model_factory(config)))
        if not supports_tool_images(underlying):
            raise GameError(
                "vision_required", "所选模型没有已确认的工具图像输入能力；不能开始纯视觉游戏。"
            )
        return BudgetModel(underlying, budget)

    manager = SessionManager(
        store=store,
        llm_client_factory=factory,
        prompt_builder_factory=lambda pid: MarkdownPromptBuilder(persona_id=pid),
        context_manager_factory=default_context_manager,
        tool_registry=registry,
        message_renderer=MultimodalInputAdapter(open_asset=assets.open).render_message,
        message_budget_renderer=MultimodalInputAdapter(
            open_asset=assets.open
        ).render_budget_message,
    )
    runtime = None
    conv = None
    try:
        if budget.path.exists():
            if not continue_existing:
                raise GameError("play_exists", "已有游玩记录；使用 continue-play，原预算不会重置。")
            data = budget.read()
            if data.get("reasoning_preset") != reasoning_preset:
                raise GameError("identity_changed", "推理档位变化；不能隐式改变原任务配置。")
            if (data["model"], data["persona"], data["skill_digest"], data["rom_sha256"]) != (
                model,
                persona,
                digest,
                client.config["rom_sha256"],
            ):
                raise GameError(
                    "identity_changed", "模型、人格、Skill 或游戏身份变化；禁止隐式续跑。"
                )
            # Re-observation is safe while paused; a new emulator needs paired save/context recovery.
            observed = client.request("observe")
            if observed.get("emulator_boot") != data["emulator_boot"]:
                raise GameError(
                    "recovery_required",
                    "模拟器已重启，需要配对恢复游戏与 AF 上下文；不能直接用旧会话操作新游戏。",
                )
            session = manager.open(data["session_id"])
            finish = root / "finish.json"
            if finish.exists():
                archived = root / "stop_history"
                archived.mkdir(exist_ok=True)
                finish.rename(archived / f"{uuid4().hex}.json")
        else:
            if continue_existing:
                raise GameError("no_play", "尚无原任务；不能凭空继续。")
            observed = client.request("observe")
            if observed["status"] != "completed":
                raise GameError("emulator_not_ready", "模拟器尚未完成有效观察。")
            session = manager.create(
                persona=persona,
                model=model,
                title="火红自主游玩",
                origin_kind="pokemon",
                origin_id=client.config["instance_id"],
                reasoning_preset=reasoning_preset,
            )
            data = {
                "schema": 1,
                "session_id": session.session_id,
                "model": model,
                "persona": persona,
                "reasoning_preset": reasoning_preset,
                "rom_sha256": client.config["rom_sha256"],
                "skill_digest": digest,
                "emulator_boot": observed["emulator_boot"],
                "goal": GOAL,
                "max_calls": max_calls,
                "calls_reserved": 0,
                "started_at": time.time(),
                "deadline": time.time() + max_seconds,
                "status": "prepared",
                "turns": 0,
            }
            atomic_json(budget.path, data)
        client.resume()
        budget.check()
        with budget.lock:
            data = budget.read()
            data["status"] = "running"
            data.setdefault("episodes", []).append({"started_at": time.time()})
            atomic_json(budget.path, data)
        conv = manager.start_conversation(
            session,
            tool_run_policy=ToolRunPolicy(
                max_model_continuations=12, max_total_tool_calls=32, max_elapsed_seconds=300
            ),
        )
        runtime = AgentRuntime(conversation_factory=lambda _sid: conv)
        runtime.start()
        while True:
            budget.check()
            if package_digest(skill_path) != digest:
                raise PlayStopped("skill_changed")
            data = budget.read()
            prompt = (
                f"这是用户已授权游戏任务的系统续玩，第 {data['turns'] + 1} 轮，不是新的用户消息。\n"
                f"原目标：{data['goal']}\n先观察游戏并读取自己的笔记，然后自主推进。"
                "到阶段边界写好笔记；观察到名人堂或确需用户处理时用 pokemon_finish 停止。\n"
                f"以下是用户选定且宿主核验未变的个人 Skill（用户级玩法说明）：\n<personal_skill>\n{instructions}\n</personal_skill>"
            )
            handle = runtime.submit(
                SystemTriggerEvent(
                    session_id=session.session_id,
                    source_kind="pokemon_autoplay",
                    system_prompt_addendum=prompt,
                    presentation="conversation",
                    tool_mode="normal",
                    event_metadata={
                        "instance_id": client.config["instance_id"],
                        "skill_digest": digest,
                    },
                ),
                consumer_abandon_policy=ConsumerAbandonPolicy.DETACH,
                control_scope="pokemon",
            )
            done = threading.Event()

            def watch(done_event: threading.Event = done, run_handle: Any = handle) -> None:
                while not done_event.wait(0.1):
                    # Do not cancel the last *already reserved* call merely because the call count
                    # reached its cap; no subsequent request can reserve another slot.
                    if (root / "paused").exists() or time.time() >= budget.read()["deadline"]:
                        client.pause()
                        run_handle.cancel()
                        return

            monitor = threading.Thread(target=watch, daemon=True)
            monitor.start()
            try:
                for _event in handle.events():
                    pass  # AF persists actual event/usage history; no hidden second model loop.
            finally:
                done.set()
                monitor.join(timeout=1)
            terminal = handle.wait(1)
            with budget.lock:
                data = budget.read()
                data["turns"] += 1
                data["last_run_id"] = handle.request.run_id
                atomic_json(budget.path, data)
            if terminal is None or terminal.outcome != RunOutcome.COMPLETED:
                raise PlayStopped("af_turn_interrupted_or_failed")
            # At a settled AF turn, preserve a paired checkpoint, never a future-context/old-save mix.
            if not (root / "paused").exists():
                _checkpoint_pair(client, conv.session.to_dict(), digest)
    except PlayStopped as exc:
        if budget.path.exists():
            with budget.lock:
                data = budget.read()
                data.update(status="paused", stop_reason=str(exc), stopped_at=time.time())
                atomic_json(budget.path, data)
            return data
        raise
    finally:
        client.pause()
        if runtime is not None:
            runtime.stop()
        if budget.path.exists():
            with budget.lock:
                data = budget.read()
                if data.get("episodes") and "finished_at" not in data["episodes"][-1]:
                    data["episodes"][-1]["finished_at"] = time.time()
                if data.get("status") == "running":
                    data.update(status="paused", stop_reason="runner_exited")
                atomic_json(budget.path, data)
        if conv is not None and client.status().get("pending") is None:
            try:
                checkpoint = _checkpoint_pair(client, conv.session.to_dict(), digest)
                with budget.lock:
                    data = budget.read()
                    data["final_checkpoint"] = checkpoint.name
                    atomic_json(budget.path, data)
            except (GameError, OSError, ValueError, PlayStopped) as exc:
                with budget.lock:
                    data = budget.read()
                    data["final_checkpoint_error"] = getattr(exc, "code", type(exc).__name__)
                    atomic_json(budget.path, data)
        manager.close()
        model_bindings.close()


def _checkpoint_pair(client: GameClient, session: dict[str, Any], skill_digest: str) -> Path:
    observed = client.request("observe")
    saved = client.request("checkpoint", observation_id=observed["observation_id"])
    if saved["status"] != "completed":
        raise PlayStopped("checkpoint_failed")
    root = client.root / "checkpoints" / uuid4().hex
    root.mkdir(parents=True)
    atomic_json(root / "session.json", session)
    notes = client.root / "notes.json"
    atomic_json(
        root / "notes.json",
        json.loads(notes.read_text(encoding="utf-8")) if notes.exists() else {"text": ""},
    )
    files = [
        root / "session.json",
        root / "notes.json",
        Path(saved["save_path"]),
        Path(saved["screenshot"]),
    ]
    manifest = {
        "schema": 1,
        "rom_sha256": client.config["rom_sha256"],
        "skill_digest": skill_digest,
        "session_id": session["session_id"],
        "emulator_boot": saved["emulator_boot"],
        "operation_id": saved["operation_id"],
        "created_at": time.time(),
        "files": [
            {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files
        ],
    }
    atomic_json(root / "manifest.json", manifest)
    return root
