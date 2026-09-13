# 130 证据索引

## 用途与当前判断

服务本需求的诊断、外部实践与验证。当前确认 AF 存在请求作用域变化后全量回退的触发链路；新方案已通过确定性机制回归及全部项目门禁。未获得原事件中未压缩新请求的真实 token，不能断言原次压缩必然多余，也不能外推真实长期压缩频率收益。

## 证据账本

| ID | 来源与范围 | 观察与局限 | 基础 / 置信度 / 状态 |
|---|---|---|---|
| E-001 | AF Windows packaged 0.3.0；2026-09-13 15:18–15:19 +08；source_commit `5cf216d4f07517dcc5992d0df4989ad86bbdd4cc` | 首轮 context build：last_input_tokens=118652，before=254111，after=138221，threshold=217600，compacted=true，duration_ms=43578；估算日志 fallback=request_scope_changed。前后工具 schema 指纹不同。原始新请求未发送，真实占用未知。 | runtime-observed + code-supported / confirmed（触发链路）/ active |
| E-002 | AF 同提交：`agent/src/agent/context/usage_anchor.py` estimate_input，`conversation.py` _usage_scope / _estimate_rendered_input，`context/budget.py` | scope 包含工具与设置；不一致时全量回退。字符估算系数 0.75。源码说明机制，不独立证明任意请求都被高估。 | code-supported / confirmed / active |
| E-003 | Codex 官方提交 `1715e55076737158ba61d43158ede504de6d4ce1`，2026-09-13 读取 | history.get_total_token_usage 使用上次 total_tokens 加最后模型条目后的本地增量，区分服务端是否包含历史 reasoning；context_window 将其用于阈值。该函数没有整份请求指纹门禁，不等于实现任意编辑的精确差量。仅代表开源 core，不外推闭源桌面内部。 | code-supported / confirmed（所读实现）/ active |
| E-004 | 用户在当前需求讨论中确认 | 确认 130 编号与范围，明确“应该去尽量复用”。 | human-reviewed / confirmed / active |
| E-005 | 2026-09-13，本工作树 `5cf216d4f07517dcc5992d0df4989ad86bbdd4cc` + 未提交实现，文件绑定见 candidate-source.sha256 | 确定性合成回归及 Responses MockTransport 工具循环/恢复通过；backend 全量 3136 passed / 5 skipped，lint/mypy 通过；frontend 1294 tests/构建与 Rust 283 tests/release check 通过。未执行真实 LLM，不是原用户会话精确 replay；不推断真实压缩频率或延迟收益。 | controlled-comparison + code-supported / confirmed（断言覆盖的机制）/ active |

## 来源与 artifact 映射

- E-001 原始来源是本机按 `agent.paths.log_dir()` 解析的 `agent_bridge.log`，查询上述时间窗的 ContextCompactionDbg、context_input_estimate、context_build_done 和 prompt_cache_observation；日志会轮转，只在本机可访问。本文只保留必要统计，不归档完整会话、prompt 或原始日志；未生成原始文件完整性清单。
- E-002 可由上述 AF commit 重建源码；该提交是运行日志声明的身份，不将未来需求分支当作历史运行版本。
- E-003：[history.rs](https://github.com/openai/codex/blob/1715e55076737158ba61d43158ede504de6d4ce1/codex-rs/core/src/context_manager/history.rs#L665-L696)、[context_window.rs](https://github.com/openai/codex/blob/1715e55076737158ba61d43158ede504de6d4ce1/codex-rs/core/src/session/context_window.rs)、[turn.rs](https://github.com/openai/codex/blob/1715e55076737158ba61d43158ede504de6d4ce1/codex-rs/core/src/session/turn.rs#L1262-L1360)。未保存临时网页副本，固定官方 commit 为复查入口。
- E-005：[candidate-source.sha256](./candidate-source.sha256) 固定候选实现及相关回归文件字节；不是历史运行的 clean commit。命令、结果和局限见 progress.md；没有复制完整测试输出或用户数据。

## 未决问题与决策影响

- E-001/E-002 支持需求 R-1；E-005 验证局部变更保留基数、变化计数及独立候选估算，不直接用旧粗估差额扣减实测总量。
- E-003 只提供实践参考；AF 的 provider 与投影边界须独立验证。
- 附件、不透明 reasoning、历史重写、计量身份变化及旧锚点的兼容策略已明确并有回归，具体降级局限见 progress.md。
- E-004 确认优先目标；用户随后确认方案并授权实现，E-005 提供机制性验证；用户看到全量结果后明确授权合入主干并推送，验收完成。

## 对账记录

2026-09-13：需求、方案、实现及三段项目门禁完成，用户验收完成并授权主干交付；提交与合入指向以 Git 历史为准。E-005 的历史验证身份仍是基线加未提交实现，不改写为事后提交。无新真实 LLM 调用。
