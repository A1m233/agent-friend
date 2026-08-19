# 054 · 非法工具参数被误报为运行边界并正常完成

- **状态**：resolved
- **严重度**：high
- **发现日期**：2026-08-19
- **关联需求**：085-governed-local-command-runtime

## 现象

在会话 `f41eb97c-e570-420b-918b-968844ac6f37` 中，用户要求更新已有 Skill，并连续明确表示“直接执行”“开始吧”。模型实际多次尝试调用 `exec_command`，但每次参数都缺少公开 schema 的必填字段；四个用户轮次内共出现 8 次 `Rejected malformed tool-call batch before execution`，没有任何命令进入执行或授权阶段。

会话中没有出现有效命令卡片，Agent 却反复回复“当前工具执行已到达运行边界”“下一轮会自动执行”，并继续让用户等待或手动运行。运行日志最终将这些轮次记录为 `outcome=completed`，没有向前端暴露真实失败原因。

该案例同时暴露出触发背景：`skill-installer` 明确不负责更新已有 Skill，安装脚本也拒绝覆盖现有目录且不存在 Agent 编造的 `--force` 参数；当前缺少清晰的同源 GitHub Skill 更新路径。

## 根因初判

`Conversation` 对 malformed tool-call 只允许一次模型重试；第二次失败后以 `malformed_tool_call` 停止，但统一进入工具运行停止收尾。该收尾向模型注入“当前工具执行已经到达运行边界”的泛化指令，没有区分参数校验失败、预算耗尽、无进展或需要用户操作。

因此真实的 schema 失败被模型改写为不存在的运行边界。malformed 调用在发布工具请求事件之前即被拒绝，前端没有卡片可以呈现；调度器又把最终轮次记录为正常完成，进一步隐藏失败。

目前日志只保留校验错误，没有保留安全脱敏后的非法参数结构，尚不能确定 Kimi K2.5 是生成了空对象，还是生成了缺少 `cmd` / `reason` 的部分结构。

## 影响与判断

这是核心执行体验问题：用户已经授权行动，系统既不执行，也不显示真实失败，还承诺未来自动继续，导致用户在同一目标上反复重述。问题不是 Windows、沙箱、审批或前端交互造成的；这些层级从未收到合法命令请求。

本次修复只闭环非法工具参数的运行时错误语义与前端投影，不扩张到同源 GitHub Skill 更新能力。后者仍是独立的产品能力边界。

## 可选修复方向

- 按实际 stop reason 生成专用收尾指令；`malformed_tool_call` 必须明确说明命令未执行、未发起授权，禁止声称预算或运行边界。
- 将重复 malformed tool-call 作为用户可见失败和非 completed 终态投影，同时继续避免生成空授权卡片。
- 记录不含凭据值的非法参数结构摘要与缺失字段，补足 Kimi 工具调用兼容性的可观测性。
- 为同源 GitHub Skill 更新提供明确的确定性工作流；在能力未提供前，禁止编造 `--force` 或承诺下一轮自动执行。
- 用原模型 Kimi K2.5 固化真实回归案例，覆盖连续 malformed、用户重复“开始”、无授权卡片及最终状态语义。

## 修复结果

- 首次非法工具参数仍允许一次带具体校验原因的模型自修复；连续第二次非法参数会直接抛出 `MalformedToolCallError`，不再进入通用“运行边界”模型收尾。
- 非法调用不会发布工具请求、执行工具或发起授权；Runtime 以 `outcome=failed`、`reason_code=malformed_tool_call` 结束，AG-UI 投影为可恢复的 `RUN_ERROR`，不再发送 `RUN_FINISHED`。
- 失败轮会从后续模型上下文中清理可能误导用户的执行声明，并替换为“工具参数连续无效、宿主未执行或授权”的稳定事实。
- 日志新增不含参数值的结构摘要，记录工具名、已知字段、字段类型、未知字段数量和缺失必填字段，便于区分模型输出形态而不泄露凭据。

## 验证

- 合并最新主干后，backend 门禁通过：`1847 passed, 4 skipped`，ruff 与 mypy 同时通过。
- frontend 门禁通过：98 个测试文件、554 条测试通过，lint、typecheck 和 production build 通过。
- Rust 门禁通过：Tauri core 201 条测试与 installer helper 6 条测试通过，另有 1 条设备 smoke 测试按预期忽略；release check 通过。
- 使用事故模型 Kimi K2.5 运行 `C24-01` 正常工具链 smoke 时，模型连续调用 `request_filesystem_access` 后以 `no_progress` 停止，未调用 `exec_command`，也未产生 malformed。该结果保存在 [`agent_eval` report](../../../agent_eval/reports/2026-08-19T14-10-05-335a0a5-C24-01.json)，只作为邻近失败证据，不作为本 issue 的通过证明。

修复指向：commit `1b19a56`（`fix/054-malformed-tool-call-misreported-as-runtime-boundary`）。
