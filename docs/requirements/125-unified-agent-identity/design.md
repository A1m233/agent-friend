# 125 · 统一身份技术方案

## 状态
CONFIRMED

## 需求文档
[requirement.md](./requirement.md)

## 现状与入口盘点

源码基线：`1279a7d0487037e0fbaa03c55c51b3320b50c8a5`。下表为代码证据，不代表每个入口已完成真实模型验收。

| 入口或消费者 | 当前实现 | 本次处理 |
|---|---|---|
| 普通聊天、CLI | `agent/prompts.py`、`system_prompt/composer.py` | 默认装配公共身份 section |
| 桌面聊天 | `agent_bridge/protocols/ag_ui/routes.py` | 通过既有运行链路继承默认装配 |
| 语音 | `agent_bridge/protocols/openai/routes.py`、`voice_bridge/rtc/scenes.py` | 同上，保留语音表达规则 |
| IM | `agent_bridge/protocols/im/router.py` | 同上，保留渠道和用户边界 |
| 定时任务 | `agent_bridge/scheduled_tasks/runner.py` | 同上，不改变任务会话和运行状态 |
| 提醒及授权结果跟进 | `agent_bridge/reminders.py`、`social_auth.py` | 普通运行链路继承；通知路径见下一项 |
| 气泡通知 | `agent_bridge/notifications/generation.py`、`prompt.py` | 接入同一公共身份文本，明确事件 reply 的自身来源 |
| 人格编辑 | `agent/personas/editor.py`、`prompt_sections/persona_editor.md` | 接入公共身份，正式人格用于身份表达；候选仍为待应用数据 |
| 历史检索 | `agent/tools/builtin/conversation_history/render.py`、`memory_search/session_history_source.py` | 保留 persona 过滤和说话人映射，验证自身与引用内容不混淆 |
| 记忆召回 | `memory/retrieval/renderer.py` | 保留结构化说话人，公共契约解释自身历史，不做文本替换 |
| 压缩摘要 | `agent/context/summary_prompt.py` | 保留内部处理职责，补强发言人与承诺、引用及完成状态保真 |
| 记忆抽取 | `memory/extraction/prompts/extract.md` | 保留内部第三人称事实及 speaker_origin，不加载对用户发言人格 |
| 通知历史 | `agent/notifications/store.py` | 已按“我通过桌宠气泡……”记录实际呈现，保留原始证据 |
| 模型连接探测 | `agent_bridge/model_connections/inspection.py` | 连接能力检查，不代表朋友发言，不接入身份契约 |

## 方案设计

### 公共契约与装配

在现有 `agent.prompt_sections` 资源包增加 `identity_continuity.md`，作为唯一身份连续性正文。
沿用现有 `SystemPromptComposer` 的静态 section，不另建 Agent 注册框架。
公共资源加载由 `agent.system_prompt` 下无业务依赖的资源 helper 承担，供默认 section、通知和编辑器复用。

公共正文只定义：同一主体、自身历史承接、第三方来源边界、非全知、非权限扩张，以及自然表达不强制重复主语。
用户人格仍定义名字、性格和表达方式；局部任务规则只定义通知或编辑的职责。公共身份不覆盖工具安全、真实性或任务生命周期。

### 气泡

生成请求先提供公共身份，再提供正式人格和通知专用规则。通过宿主确定的事件来源说明 reply 是自身会话回复，其内部引用仍归原作者。
不改通知数据库或 UI wire schema；不增加代词替换或关键词拦截，不将 end_turn 升级为任务成功。

### 人格编辑

调整原“维护一个虚拟朋友”的旁观定位为与用户讨论自己的表达方式。
正式人格用于当前身份和表达；候选及历史提案只作为编辑数据，未经应用不成为当前人格。
仍禁止 persona 数据中的越权指令改变编辑任务、工具和候选应用边界。
保留每轮正式人格快照、现有唯一提案工具、独立会话及无长期记忆访问机制。

### 上下文

不合并 session，不迁移 persona_id，不改历史记录。保留现有 speaker/source 标记，让消费端公共契约正确解释自身历史。
摘要补充保留说话人和被引用者、将承诺与已执行结果分开的要求；记忆抽取继续维护 speaker_origin。
无需向内部摘要器和记忆抽取器注入人格；不把第三人称存储形式误判为人格分裂。

### Coding Agent 治理

新增 `.cursor/rules/agent-prompt-composition.mdc`，按提示词和模型调用入口相关路径触发，并明确新入口也适用。
约束先分类职责、寻找既有公共 owner、按职责组合、维护 `agent/prompt_sections/README.md` 的消费者清单、验证最终装配及来源边界。
Rule 不复制产品身份正文；工具契约、失败归因和 eval 仍由各自 Rule 负责。
通过项目 `scripts/codex-adapters/run.* sync/doctor` 更新生成索引。

## 影响与验证

- 新增 Markdown 仍在现有资源包、文件类型及收集范围内，沿用 importlib.resources；不改依赖、动态导入或资源路径协议，分类为 standard。通过资源 loader 和后端检查验证，不启动安装包构建。
- 默认 prompt 改变会使之后新建的请求前缀变化；不改已有持久化历史。已有运行态须重启后才能作为新版本验收证据。
- 确定性测试覆盖默认两种交流模式、通知 provider 实际请求、编辑器快照及候选隔离、历史归属；fake 输出不证明自然语言行为通过。
- 运行项目后端门禁及可用的前端/Rust 阶段，记录环境阻塞，不以未运行代替通过。
- 真实模型观察需另行给出配置、次数及授权；未执行前 AC-2 至 AC-6、AC-9 的模型行为证据保持待验收。若新增 agent_eval case，先执行相应 Skill 和治理规则。
- 无 UI 布局修改，不启动桌面视觉验收；用户最终验收和合入另行完成。

## 变更记录

| 日期 | 变更 | 是否需要重新实现 |
|---|---|---|
| 2026-09-11 | 用户确认方案边界并授权写入后开始实现 | 首次实现 |
