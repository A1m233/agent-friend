# 130 实现进度

## 总体状态

COMPLETED

## 任务列表

- [x] 需求与方案确认，feature/130-context-usage-continuity 远程占号
- [x] 实现版本化局部内容复用与安全边界
- [x] 接入候选、最终请求、恢复及可观测性
- [x] 确定性回归验证
- [x] backend / frontend / rust 全量门禁
- [x] 用户验收

## AC 验证

| AC | 当前证据 |
|---|---|
| AC-1 | 合成 330000 字符历史保留 118652 实测基数；小幅工具/系统替换不触发 217600 阈值；断言不调用全量 fallback。 |
| AC-2 | 重复次数、角色变更、重排、增量超阈值、实测更新均有断言；原有工具循环通过。 |
| AC-3 | 替换/删除不凭粗估扣减，重复预算检查不累加；较小候选可独立全量估算；相同消息数摘要替换能释放预算。 |
| AC-4 | V3 JSON 往返、非法字段、跨身份拒绝；原 V1/V2 兼容回归通过；Conversation 及真实 Responses adapter 的 MockTransport 回归覆盖工具变化后 rollout 恢复。 |
| AC-5 | 原生文件按既有预算而非 base64 长度；已计量 reasoning 复用，匹配输出使用输出上界，未知 reasoning 保留整窗预算；附件候选不上传且已知文本仍复用。 |
| AC-6 | 统一 estimate_input 接入候选与最终检查；新增复用/新增/保留片段及未知预算字段；原有异常 usage、缓存、最终检查与有界恢复测试通过。 |
| AC-7 | backend 全量通过：3136 passed / 5 skipped，lint 与 mypy（694 文件）通过；frontend 的 lint/typecheck、1294 测试及生产构建通过；Rust fmt、283 测试与 release check 通过；用户看到完整验证结果后明确要求合入主干并推送，验收完成。 |

专项命令 `scripts/test/run.ps1 agent/tests/test_context_usage_anchor.py agent/tests/test_context_usage_continuity.py agent/tests/test_context_summarizing.py -q` 的首批 72 项通过；后续补充的同长度摘要与完整 reasoning 估算回归以及 Responses 工具变化参数已在最终全量测试通过。最终新增连续性测试文件共 10 项，Responses 文件共 16 项。真实 LLM 未调用，未启动开发运行态，未构建安装包。

## 实现边界

- V3 使用片段多重集合，覆盖消息角色、字段、调用身份与内容，保留重复次数；旧锚点仍用原严格校验。
- 旧 V1/V2 锚点不具备片段证据，不伪造迁移；下一次可信响应建立 V3。因此升级后旧会话首次续聊仍可能沿用旧回退路径。
- 不兼容模型/连接、reasoning 配置和未知请求设置仍隔离；本期没有把任意设置变化都假设为纯内容变化。
- 附件候选不上传，未解析附件追加来源预算，最终渲染再计量；相同已渲染附件可复用，不承诺候选阶段已建立附件来源到远端 file ID 的精确关联。
- 删除/替换的旧成本是未分配安全余量，下一次实测刷新；需要降低预算时允许完整候选独立估算，不执行粗估差额扣减。
- 未知 reasoning 没有可靠上界时保留整窗预算，可能进入既有压缩/拒绝恢复，不以密文长度猜成本。

## 实现日志

| 日期 | 进展 | 证据 |
|---|---|---|
| 2026-09-13 | 用户确认设计并授权实现，需求/设计落盘 | 当前任务确认；历史证据见 evidence-index.md |
| 2026-09-13 | 完成 V3 片段摘要、局部估算、恢复和统一预算接入 | standard 打包影响，无依赖、资源或工具公开契约变化 |
| 2026-09-13 | 首批专项 72 项通过；补充真实 adapter MockTransport 与摘要/推理边界；lint/typecheck 通过 | backend 全量使用 no_external_http 护栏，不调用真实 LLM |
| 2026-09-13 | backend 全量通过 | 设置 PYTHONPATH 为 scripts/test 后执行 scripts/check/run.ps1 --stage backend -p no_external_http；退出码 0，3136 passed / 5 skipped，283.2 秒；保留第三方异步清理和 ZIP 重名 fixture 警告 |
| 2026-09-13 | frontend 全量通过 | scripts/check/run.ps1 --stage frontend，退出码 0，155 文件 / 1294 tests；生产构建通过，180.5 秒；保留已有 CSS highlight 与包体大小警告 |
| 2026-09-13 | Rust 全量通过 | 用 scripts/package/paths.py 的 create_build_paths/resolve_package_roots 解析本 worktree 的仓库外 cargo_target_dir，并设置 CARGO_TARGET_DIR 执行 scripts/check/run.ps1 --stage rust；退出码 0，fmt、283 tests、release check 通过，263.0 秒；保留既有 dead_code 警告 |
| 2026-09-13 | 完成验证与文件对账，待用户验收 | candidate-source.sha256 的 6 个文件均匹配；git diff --check 通过；实现尚未暂存/提交，未合入主干、未打包安装 |
| 2026-09-13 | 用户验收并授权合入 main、推送远端 | 最新 origin/main 仍为 5cf216d4，无新增代码或冲突；原有三段门禁证据继续有效 |
| 2026-09-13 | merge-main-and-push Guide 复核：无需更新 | Guide b57ecf22，公开产品基线 b875d88a，与当前主干文件树相同。130 改善内部预算计量，using-agent-friend 的整理提示、model-credentials 的上下文预算、troubleshooting/data-and-privacy 的恢复与隐私说明仍准确；无新用户操作，无需发布 Guide 或同步 builtin |
