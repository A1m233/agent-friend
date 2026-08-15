"""SessionBridge 的 post-turn 记忆观察所有权装配测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_bridge.session_bridge import PersistentBootstrap, SessionBridge


def test_bind_persistent_forwards_external_post_turn_ownership() -> None:
    runtime = MagicMock()
    manager = runtime.persistent_session_manager
    session = object()
    conversation = object()
    manager.open.return_value = session
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
