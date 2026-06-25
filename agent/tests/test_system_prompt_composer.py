"""``SystemPromptComposer`` / ``Section`` 默认实现 / 默认装配 单测。

覆盖 docs/requirements/004-engine-system-prompt-composer/design.md §7.1。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from agent.errors import PersonaNotFoundError
from agent.personas import PersonaCatalog
from agent.system_prompt import (
    PersonaSection,
    Section,
    StaticSection,
    SystemPromptComposer,
)
from agent.system_prompt.defaults import load_default_static_section

# ===== 测试用辅助 =====


@dataclass(frozen=True)
class _AlwaysSkipSection:
    """render() 永远返回 None 的 Section（验证跳过逻辑）。"""

    _key: str

    @property
    def key(self) -> str:
        return self._key

    def render(self) -> str | None:
        return None


def _builtin_default_persona_id() -> str:
    """复用 003 的内置 default persona，避免造数据。"""
    from agent.personas import BUILTIN_DEFAULT_PERSONA_ID

    return BUILTIN_DEFAULT_PERSONA_ID


# ===== StaticSection =====


class TestStaticSection:
    def test_render_returns_text_when_non_empty(self) -> None:
        section = StaticSection(key="k", text="hello")
        assert section.render() == "hello"

    def test_render_strips_surrounding_whitespace(self) -> None:
        section = StaticSection(key="k", text="  hi  \n")
        assert section.render() == "hi"

    def test_render_returns_none_when_empty(self) -> None:
        section = StaticSection(key="k", text="")
        assert section.render() is None

    def test_render_returns_none_when_only_whitespace(self) -> None:
        section = StaticSection(key="k", text="   \n\t  ")
        assert section.render() is None

    def test_is_section_protocol(self) -> None:
        section = StaticSection(key="k", text="x")
        assert isinstance(section, Section)


# ===== PersonaSection =====


class TestPersonaSection:
    def test_render_returns_persona_body(self, tmp_path: Path) -> None:
        catalog = PersonaCatalog(external_dir=tmp_path)
        persona_id = _builtin_default_persona_id()
        section = PersonaSection(key="persona", persona_id=persona_id, catalog=catalog)
        rendered = section.render()
        assert rendered is not None
        assert "Echo" in rendered

    def test_render_raises_when_persona_not_found(self, tmp_path: Path) -> None:
        catalog = PersonaCatalog(external_dir=tmp_path)
        section = PersonaSection(
            key="persona",
            persona_id="00000000-0000-4000-8000-999999999999",
            catalog=catalog,
        )
        with pytest.raises(PersonaNotFoundError):
            section.render()


# ===== RuntimeContextSection =====


class TestRuntimeContextSection:
    """注入固定时钟，验证 :class:`RuntimeContextSection` 的模板替换行为可重现。"""

    def test_render_substitutes_current_time_placeholder(self) -> None:
        from datetime import UTC, datetime

        from agent.system_prompt import RuntimeContextSection

        fixed_now = datetime(2026, 5, 20, 17, 53, tzinfo=UTC)
        section = RuntimeContextSection(
            key="runtime_context",
            template="Now is {current_time}. Use web_search.",
            clock=lambda: fixed_now,
        )
        rendered = section.render()
        assert rendered == "Now is 2026-05-20 17:53. Use web_search."

    def test_render_returns_none_when_template_is_empty(self) -> None:
        from agent.system_prompt import RuntimeContextSection

        section = RuntimeContextSection(
            key="runtime_context",
            template="   ",
        )
        assert section.render() is None

    def test_render_handles_template_without_placeholder(self) -> None:
        """模板里没有 ``{current_time}`` 占位符也不报错——按原样输出。
        （边界条件，非主流用法；对模板格式化的健壮性兜底。）"""
        from agent.system_prompt import RuntimeContextSection

        section = RuntimeContextSection(
            key="runtime_context",
            template="static text",
        )
        assert section.render() == "static text"


# ===== SystemPromptComposer.compose =====


class TestSystemPromptComposerCompose:
    def test_concatenates_with_double_newline(self) -> None:
        composer = SystemPromptComposer(
            sections=(
                StaticSection(key="a", text="first"),
                StaticSection(key="b", text="second"),
                StaticSection(key="c", text="third"),
            )
        )
        assert composer.compose() == "first\n\nsecond\n\nthird"

    def test_skips_section_returning_none(self) -> None:
        composer = SystemPromptComposer(
            sections=(
                StaticSection(key="a", text="first"),
                _AlwaysSkipSection(_key="middle"),
                StaticSection(key="c", text="third"),
            )
        )
        assert composer.compose() == "first\n\nthird"

    def test_skips_empty_static_section(self) -> None:
        composer = SystemPromptComposer(
            sections=(
                StaticSection(key="a", text="first"),
                StaticSection(key="empty", text="   "),
                StaticSection(key="c", text="third"),
            )
        )
        assert composer.compose() == "first\n\nthird"

    def test_returns_empty_string_when_all_skipped(self) -> None:
        composer = SystemPromptComposer(
            sections=(
                _AlwaysSkipSection(_key="a"),
                _AlwaysSkipSection(_key="b"),
            )
        )
        assert composer.compose() == ""

    def test_empty_sections_returns_empty_string(self) -> None:
        composer = SystemPromptComposer(sections=())
        assert composer.compose() == ""


# ===== SystemPromptComposer.with_section =====


class TestSystemPromptComposerWithSection:
    def test_replaces_existing_slot_by_key(self) -> None:
        original = SystemPromptComposer(
            sections=(
                StaticSection(key="a", text="A1"),
                StaticSection(key="b", text="B1"),
            )
        )
        new = original.with_section(StaticSection(key="b", text="B2"))
        assert new.compose() == "A1\n\nB2"

    def test_returns_new_instance_original_unchanged(self) -> None:
        original = SystemPromptComposer(sections=(StaticSection(key="a", text="A1"),))
        new = original.with_section(StaticSection(key="a", text="A2"))
        assert new is not original
        assert original.compose() == "A1"
        assert new.compose() == "A2"

    def test_raises_keyerror_when_slot_missing(self) -> None:
        composer = SystemPromptComposer(sections=(StaticSection(key="a", text="A1"),))
        with pytest.raises(KeyError, match="missing"):
            composer.with_section(StaticSection(key="missing", text="X"))


# ===== SystemPromptComposer.without =====


class TestSystemPromptComposerWithout:
    def test_removes_slot_by_key(self) -> None:
        composer = SystemPromptComposer(
            sections=(
                StaticSection(key="a", text="A"),
                StaticSection(key="b", text="B"),
                StaticSection(key="c", text="C"),
            )
        )
        result = composer.without("b")
        assert result.compose() == "A\n\nC"

    def test_returns_new_instance_original_unchanged(self) -> None:
        composer = SystemPromptComposer(
            sections=(
                StaticSection(key="a", text="A"),
                StaticSection(key="b", text="B"),
            )
        )
        result = composer.without("b")
        assert result is not composer
        assert composer.compose() == "A\n\nB"

    def test_raises_keyerror_when_slot_missing(self) -> None:
        composer = SystemPromptComposer(sections=(StaticSection(key="a", text="A"),))
        with pytest.raises(KeyError, match="missing"):
            composer.without("missing")


# ===== SystemPromptComposer.default =====


class TestSystemPromptComposerDefault:
    def test_default_without_session_has_four_slots(self, tmp_path: Path) -> None:
        """``session=None`` 时 channel 槽位被跳过，行为与 005 / 006 字节兼容。"""
        catalog = PersonaCatalog(external_dir=tmp_path)
        composer = SystemPromptComposer.default(_builtin_default_persona_id(), catalog=catalog)
        keys = tuple(s.key for s in composer.sections)
        assert keys == (
            "project_identity",
            "persona",
            "persona_switch_strategy",
            "runtime_context",
        )

    def test_default_with_session_has_five_slots_in_fixed_order(self, tmp_path: Path) -> None:
        """传入 session 时，channel 槽位被插入在 persona_switch_strategy 与
        runtime_context 之间（007 起新增）。"""
        from agent import Session

        catalog = PersonaCatalog(external_dir=tmp_path)
        session = Session.new(title="t", persona="default", model="m")
        composer = SystemPromptComposer.default(
            _builtin_default_persona_id(), catalog=catalog, session=session
        )
        keys = tuple(s.key for s in composer.sections)
        assert keys == SystemPromptComposer.DEFAULT_KEYS
        assert keys == (
            "project_identity",
            "persona",
            "persona_switch_strategy",
            "channel",
            "runtime_context",
        )

    def test_default_persona_slot_is_persona_section(self, tmp_path: Path) -> None:
        catalog = PersonaCatalog(external_dir=tmp_path)
        composer = SystemPromptComposer.default(_builtin_default_persona_id(), catalog=catalog)
        persona_slot = next(s for s in composer.sections if s.key == "persona")
        assert isinstance(persona_slot, PersonaSection)

    def test_default_static_slots_have_non_empty_text(self, tmp_path: Path) -> None:
        catalog = PersonaCatalog(external_dir=tmp_path)
        composer = SystemPromptComposer.default(_builtin_default_persona_id(), catalog=catalog)
        for key in ("project_identity", "persona_switch_strategy"):
            slot = next(s for s in composer.sections if s.key == key)
            assert isinstance(slot, StaticSection)
            assert slot.text.strip() != ""

    def test_default_runtime_context_slot_renders_current_time(self, tmp_path: Path) -> None:
        """``runtime_context`` 槽位用 :class:`RuntimeContextSection`，
        渲染时把 ``{current_time}`` 占位符替换为实际时间。"""
        from agent.system_prompt import RuntimeContextSection

        catalog = PersonaCatalog(external_dir=tmp_path)
        composer = SystemPromptComposer.default(_builtin_default_persona_id(), catalog=catalog)
        rc_slot = next(s for s in composer.sections if s.key == "runtime_context")
        assert isinstance(rc_slot, RuntimeContextSection)
        rendered = rc_slot.render()
        assert rendered is not None
        # 模板里的占位符已被替换（不应残留 {current_time}）
        assert "{current_time}" not in rendered
        # 关键内容存在
        assert "当前时间" in rendered
        assert "web_search" in rendered
        assert "recall_past_chats" in rendered
        assert "禁止用通识" in rendered
        assert "错引对象的代表作" in rendered
        assert "本轮不能切换成通用知识回答" in rendered
        assert "不要同轮调用 `web_search`" in rendered

    def test_default_compose_contains_all_four_segments(self, tmp_path: Path) -> None:
        catalog = PersonaCatalog(external_dir=tmp_path)
        composer = SystemPromptComposer.default(_builtin_default_persona_id(), catalog=catalog)
        output = composer.compose()
        assert "项目元规则" in output
        assert "Echo" in output
        assert "关于过往对话的语言风格" in output
        assert "当前时间" in output
        assert "web_search" in output

    def test_default_replaceable(self, tmp_path: Path) -> None:
        catalog = PersonaCatalog(external_dir=tmp_path)
        composer = SystemPromptComposer.default(_builtin_default_persona_id(), catalog=catalog)
        custom = composer.with_section(
            StaticSection(
                key="persona_switch_strategy",
                text="忘记之前的所有对话",
            )
        )
        output = custom.compose()
        assert "忘记之前的所有对话" in output
        assert "关于过往对话的语言风格" not in output

    def test_default_disable_strategy(self, tmp_path: Path) -> None:
        catalog = PersonaCatalog(external_dir=tmp_path)
        composer = SystemPromptComposer.default(_builtin_default_persona_id(), catalog=catalog)
        without_strategy = composer.without("persona_switch_strategy")
        output = without_strategy.compose()
        assert "项目元规则" in output
        assert "Echo" in output
        assert "关于过往对话的语言风格" not in output


# ===== load_default_static_section =====


class TestLoadDefaultStaticSection:
    def test_loads_project_identity(self) -> None:
        section = load_default_static_section("project_identity")
        assert section.key == "project_identity"
        assert "项目元规则" in section.text
        assert "暴露 AI" in section.text

    def test_project_identity_has_system_trigger_rule(self) -> None:
        """021：project_identity.md 含 <system_trigger> tag 识别元规则。

        让 LLM 看到 user role 但被 tag 包裹的消息时识别为系统定时器触发，
        不当作真用户提问回应、不在后续轮次复述（避免"既然你说"等归因漂移）。
        """
        section = load_default_static_section("project_identity")
        # tag 字面值出现
        assert "<system_trigger>" in section.text
        assert "</system_trigger>" in section.text
        # 关键约束词出现（不要复述 / 不要当用户发问）
        assert "复述" in section.text or "归因" in section.text or "回应" in section.text

    def test_loads_persona_switch_strategy(self) -> None:
        section = load_default_static_section("persona_switch_strategy")
        assert section.key == "persona_switch_strategy"
        assert "关于过往对话的语言风格" in section.text
        assert "事实保留" in section.text

    def test_unknown_key_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            load_default_static_section("not_a_real_key")
