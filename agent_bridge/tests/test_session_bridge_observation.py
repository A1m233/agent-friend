"""SessionBridge 的 post-turn 记忆观察所有权装配测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_bridge.session_bridge import PersistentBootstrap, SessionBridge

from agent import SessionNotFoundError


def test_bind_persistent_forwards_external_post_turn_ownership() -> None:
    runtime = MagicMock()
    manager = runtime.persistent_session_manager
    session = object()
    conversation = object()
    manager.open_for_execution.return_value = session
    manager.start_conversation.return_value = conversation
    bridge = SessionBridge(runtime)
    boot = PersistentBootstrap(
        thread_id="session-1",
        new_user_input="hello",
        default_persona="default",
        default_model="test/model",
    )

    result = bridge.bind_persistent(boot, post_turn_external=True)

    assert result is conversation
    manager.start_conversation.assert_called_once_with(
        session,
        post_turn_external=True,
    )


def test_bind_persistent_applies_initial_auto_only_when_creating_session() -> None:
    runtime = MagicMock()
    manager = runtime.persistent_session_manager
    session = object()
    conversation = object()
    manager.open_for_execution.side_effect = SessionNotFoundError("session-1")
    manager.create.return_value = session
    manager.start_conversation.return_value = conversation
    bridge = SessionBridge(runtime)
    boot = PersistentBootstrap(
        thread_id="session-1",
        new_user_input="hello",
        default_persona="default",
        default_model="test/model",
        initial_approval_mode="session_auto",
    )

    result = bridge.bind_persistent(boot)

    assert result is conversation
    runtime.approval_broker.set_session_mode.assert_called_once_with(
        "session-1",
        mode="session_auto",
        expected_version=0,
    )


def test_bind_persistent_does_not_override_existing_session_mode() -> None:
    runtime = MagicMock()
    manager = runtime.persistent_session_manager
    session = object()
    manager.open_for_execution.return_value = session
    manager.start_conversation.return_value = object()
    bridge = SessionBridge(runtime)
    boot = PersistentBootstrap(
        thread_id="session-1",
        new_user_input="hello",
        default_persona="default",
        default_model="test/model",
        initial_approval_mode="session_auto",
    )

    bridge.bind_persistent(boot)

    runtime.approval_broker.set_session_mode.assert_not_called()
