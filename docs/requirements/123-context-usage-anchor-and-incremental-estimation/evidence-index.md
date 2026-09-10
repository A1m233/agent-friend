# 123 证据索引

## 用途与当前判断

服务本需求的计量现状、历史来源和后续验证。历史运行存在显著高估；123 候选已实现请求锚点和增量估算，确定性回归及 backend/frontend/rust 三段门禁全部通过；另有单次真实 Kimi 合成 smoke 通过，用户已验收。不据此推断所有真实会话的压缩频率改善幅度。

## 证据账本

| ID | 来源、范围与锚点 | 观察与局限 | 基础 / 置信度 / 状态 |
|---|---|---|---|
| E-001 | 2026-09-09 本地 agent_bridge.log，12:18:10–12:19:10 +08:00，同一 Kimi 会话请求链；2026-09-09 只读采集 | 首次请求估算 204845，返回 prompt_tokens 111680；后续估算 211310 超过阈值 209716 并产生压缩。前两值对应同一请求；211310 与 111680 不是同一请求，不能用它们计算精确误差。运行窗口 262144。未绑定安装包 revision。 | runtime-observed / confirmed（事件与数值） / active |
| E-002 | 009 design.md §3.3；初始提交 ed4a172 的 context/budget.py | 设计描述真实锚点加增量，初始代码仅全量字符估算；真实 usage 只收集到预算快照。 | code-supported / confirmed / active |
| E-003 | f6ce9bd 的 context/budget.py、Conversation._consume_usage；063 design.md §1.2 | 后续优化为全量估算乘校准系数，系数下限 1.0，不能修正高估。 | code-supported / confirmed / active |
| E-004 | openai/codex 73a1148c9c775c2a4616ce5096291740a00ed68a；2026-09-09 获取官方源码 | history.rs 使用最近真实 total_tokens 加最后模型输出之后的新增 item 估算，按 server_reasoning_included 决定是否补算历史 reasoning；session/context_window.rs 将合计用于阈值。utils/string/src/truncate.rs 使用约 UTF-8 字节数/4。仅说明该开源版本，不证明 Desktop 内部或其他 provider 完全等价。 | code-supported / confirmed / active |
| E-005 | 123 分支基线 dc9baaea0766b9a8cfd876aafa61fafae8c01958；2026-09-10 检查 conversation.py、context/protocol.py、sessions/rollout.py | 预算检查后仍有消息 renderer 和 reasoning 投影，压缩后有动态上下文重建；已有 rollout commit 与请求 witness，可用于设计身份绑定。基线不是 E-001 的历史运行身份。 | code-supported / confirmed / active |
| E-006 | 同一基线加 123 未提交 overlay，agent/tests/test_context_usage_anchor.py；2026-09-10 专项运行 | 26 项确定性测试通过，覆盖高估反例、工具循环、恢复、作用域变化、异常 usage、附件与最终预算恢复。测试输入为合成/fake；不是 E-001 的完整原始请求，不代表模型端真实效果。 | deterministic-tests / confirmed（机制） / active |
| E-007 | scripts/check/run.ps1 --stage frontend；同一基线加 overlay | 150 文件、1244 测试及 production build 通过；没有前端代码变更。 | deterministic-tests / confirmed / active |
| E-008 | scripts/check/run.ps1 --stage rust；同一基线加 overlay；外部 CARGO_TARGET_DIR | 282 测试与 release check 通过，无 Rust 代码变更。没有构建安装包或执行安装。 | deterministic-tests / confirmed / active |
| E-009 | 初轮 scripts/check/run.ps1 --stage backend | 2983 passed / 5 skipped / 2 failed。架构边界失败促使本次协议判断改为 capability；另一个 50ms 超时测试单文件复跑 57 passed，负载归因未确证。失败记录保留，不当作通过。 | deterministic-tests / confirmed（运行结果） / retained |
| E-010 | 最终 scripts/check/run.ps1 --stage backend；候选源码清单见 artifacts/candidate-source.sha256 | ruff、format、mypy（672 文件）通过；2992 passed / 5 skipped / 3 warnings，退出码 0。包含 26 项新增回归及已修正的架构边界检查；没有与前端/Rust 构建并发。全量复验不证明真实 LLM 效果。 | deterministic-tests / confirmed / active |
| E-011 | 2026-09-10 22:42 +08:00，用户明确授权，context-usage-smoke，Kimi k2.7-code-highspeed reasoning=on；报告内记录基线、实现与 harness SHA-256 | 4 次真实调用 passed；锚点后估算/实际为 14689/14533、14833/14631、14739/14682；旧估算均约 40000。人工缩小逻辑阈值至 27152，工具续轮与同进程 rollout 重载后均未误压缩。合成输入，非原会话 replay、非真正进程重启、非模型质量统计。 | runtime-supported / confirmed（本 smoke 链路） / active |

## 未决问题

- 单次真实合成 smoke 已验证计量链路；日常会话频率改善用户已验收或另行授权观测，不能由合成用例推算。
- 含附件历史与 opaque continuation 继续保守回退，未为这些路径声明精确增量能力。
- 用户已在真实 smoke 后授权合入推送，验收完成。

## 决策影响

E-001 至 E-003 支持计量优化需求；E-004 为方案参考，不要求复制其 provider 假设。E-005 要求方案覆盖实际请求投影及最终预算复核。具体接口待设计确认。

## Artifact 映射与隐私

E-001 原始文件位于本机标准日志根（Windows LOCALAPPDATA/agent-friend/Logs/agent_bridge.log），仅本机可访问且可能轮转。本索引只保留脱敏数值摘要，没有复制完整日志、会话标识、prompt 或用户内容，也未为原文件生成完整性 hash；摘要不冒充完整 replay fixture。

E-002、E-003、E-005 可由本仓库对应 Git revision 复查。E-004 可由 https://github.com/openai/codex/tree/73a1148c9c775c2a4616ce5096291740a00ed68a/codex-rs 复查。

E-006 及最终门禁的候选为上述基线加工作区 overlay；[candidate-source.sha256](./artifacts/candidate-source.sha256) 记录实际验证的 8 个实现/测试文件的 SHA-256（按本机文件字节计算，包含换行差异）。它是内容身份清单，不是代码备份；文件仍保存在本需求分支工作区，后续提交需核对这些内容未变，不能把未来提交写成历史执行 revision。

E-011 的 [原始脱敏 JSON](./artifacts/real-usage-smoke-20260910-01.json) SHA-256 为 `ce59f9b38931333843d1bec755bf97c15c1fc71bc957176699a6d83ffec931e6`。隔离临时会话包含纯合成内容，运行结束已删除；报告只留数值、配置、源码摘要和状态，不含凭据或真实用户内容。没有将临时会话假装成可访问的长期 artifact。

## 对账记录

2026-09-10：需求与设计已确认并授权实现，独立分支已远程占号；候选为未提交 overlay，三段门禁和 E-011 真实 smoke 已与 progress.md 对账。E-011 按用户单次授权完成 4 次真实请求；用户已验收。
