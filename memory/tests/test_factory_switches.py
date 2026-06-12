"""pass-1 M13.4 ``build_memory`` 切片开关 smoke 测试。

验证两个 ablation 开关（``extraction_keep_specifics`` / ``pinned_relevance_gate``）
能正确装配 :class:`Memory`，互不影响、组合可控。design §6 / progress M13.4。

不烧 LLM token（fake client 只返回固定 reply，不走真实抽取行为）；这里只测
*装配行为*，抽取/召回的语义正确性由各自的专门测试（test_extractor_pass1 /
test_pinned_gate / test_store）覆盖。
"""

from __future__ import annotations

from memory import build_memory


class _FakeLLM:
    def complete(self, *_a: object, **_kw: object) -> str:
        return '{"episodic_entries": [], "semantic_ops": []}'


def test_build_memory_default_is_pass1_full() -> None:
    """默认（不传开关）= pass-1 终态：两开关都开（gate 在、新 prompt 在）。"""
    mem = build_memory(":memory:", _FakeLLM())  # type: ignore[arg-type]
    try:
        assert mem._pinned_gate_enabled is True
        # 抽取走默认新 prompt（不是 legacy），简单粗略地验证 prompt 含 pass-1 标志词
        prompt = mem._worker._extractor._prompt
        assert "保留对话里的具体词" in prompt
    finally:
        mem.close()


def test_build_memory_extraction_off_uses_legacy_prompt() -> None:
    """关 ``extraction_keep_specifics`` → extractor 用 ``extract_legacy.md`` 旧 prompt。"""
    mem = build_memory(":memory:", _FakeLLM(), extraction_keep_specifics=False)  # type: ignore[arg-type]
    try:
        prompt = mem._worker._extractor._prompt
        # 旧 prompt 的特征短语
        assert "原子化" in prompt
        # 新 prompt 的硬约束词不应出现
        assert "保留对话里的具体词" not in prompt
    finally:
        mem.close()


def test_build_memory_pinned_gate_off_disables_gate() -> None:
    """关 ``pinned_relevance_gate`` → ``Memory`` 内部标记为 False，retrieve 跳过 gate。"""
    mem = build_memory(":memory:", _FakeLLM(), pinned_relevance_gate=False)  # type: ignore[arg-type]
    try:
        assert mem._pinned_gate_enabled is False
    finally:
        mem.close()


def test_build_memory_both_off_yields_pass1_baseline_slice() -> None:
    """两开关都关 = ``pass-1-baseline`` 切片（仅 jieba 永远开）。"""
    mem = build_memory(
        ":memory:",
        _FakeLLM(),  # type: ignore[arg-type]
        extraction_keep_specifics=False,
        pinned_relevance_gate=False,
    )
    try:
        assert mem._pinned_gate_enabled is False
        assert "原子化" in mem._worker._extractor._prompt
    finally:
        mem.close()


def test_build_memory_explicit_extractor_prompt_overrides_switch() -> None:
    """``extractor_prompt`` 显式传值时覆盖 ``extraction_keep_specifics`` 开关（design §6.1）。"""
    mem = build_memory(
        ":memory:",
        _FakeLLM(),  # type: ignore[arg-type]
        extractor_prompt="custom test prompt",
        extraction_keep_specifics=False,  # 应被显式参数覆盖
    )
    try:
        assert mem._worker._extractor._prompt == "custom test prompt"
    finally:
        mem.close()
