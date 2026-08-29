# 110 · 动态上下文缓存连续性 · 进度

## 总体状态

COMPLETED

## 任务列表

- [x] 将跨 dispatch 的模型历史提升为 session 级进程内 `SessionModelHistory`，不保存 prepared provider wire。
- [x] 统一记录 model-visible context、user、provider assistant turn 和 tool transaction。
- [x] 删除单 run 的 `RunContinuationProjection` 权威历史路径，保留 final-wire witness 作为验证与诊断。
- [x] 引入固定 contributor 的 `SessionModelContext`，实现 calendar、surface、channel、permission、runtime capability 与 capability catalog 的 snapshot/delta。
- [x] 将基础 system prompt 中的 channel 动态段移到 append-only channel context。
- [x] 引入宿主 `TimeProvider`，实现粗粒度日期/timezone delta 和 `datetime_calculate(current)`。
- [x] 将 datetime、Todo、Scheduled Task 和历史时间查询的隐藏时间改为工具 action-time 读取。
- [x] 让 Skill activation、filesystem scope 和 Memory recall 进入 session model history，并避免工具续轮重复 scope。
- [x] 将 compaction/FIFO/tool-context rewrite 标记为明确 history rewrite/cache epoch，并在 provider 接受后才提交候选历史。
- [x] 保持动态 model-only 正文仅在进程内；compaction summary 排除 `model_only` item。
- [x] 完成项目全量门禁并整理 AC 证据。
- [x] 在用户单次明确授权后执行真实 LLM 长会话 smoke。
- [x] 用户确认验收完成。

## AC 验证

- [x] AC-1：普通用户回合 strict extension，见 `test_dynamic_context_cache_continuity.py::test_ordinary_user_turn_strictly_extends_previous_wire`。
- [x] AC-2：工具续轮不重复 calendar，见 `test_tool_continuation_only_appends_assistant_and_tool_result`。
- [x] AC-3：跨分钟、跨日、timezone 变化，见 `test_calendar_ignores_minutes_and_appends_day_and_timezone_deltas`。
- [x] AC-4：`current` 使用 action-time，见 `test_current_tool_reads_fresh_action_time_not_turn_snapshot` 及 datetime/Todo/Scheduled Task 单测。
- [x] AC-5：surface/channel/permission/runtime capability 局部 delta，见本需求新增 contributor 测试；Skill scope 由 Skill 集成测试覆盖。
- [x] AC-6：Memory recall 保持原位置并 append-only；当前回合 recall 的出现、相同内容、内容变化、消失和相对 user 位置均有端到端诊断用例。
- [x] AC-7：persona/system/tools/settings/model/provider route 由 cache epoch 分类；compaction/FIFO 为 history rewrite；provider transform rebase 单独建立诊断 epoch。
- [x] AC-8：同进程复用 runtime state；新 manager/restart 标记 `process_resume_degraded`。
- [x] AC-9：新增 model context 不写 Session JSONL，compaction summary 排除 model-only 正文。
- [x] AC-10：普通扩展、单 contributor delta、工具续轮与 compaction 重写均有确定性 fixture。
- [x] AC-11：真实长会话对照 smoke；普通回合由 runtime rebase 收敛为 strict extension，current tool、跨日和 timezone delta 均保持 append-only。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
|---|---|---|
| 2026-08-29 | 建立 session model history、固定 contributor state、cache epoch 与 provider-accept commit 边界 | 新增确定性 continuity 测试；agent 测试 `1084 passed, 2 skipped` |
| 2026-08-29 | 完成粗日期/timezone、current tool 与 action-time 注入 | 当前时间、跨日/timezone、Todo/Scheduled Task 相关单测覆盖 |
| 2026-08-29 | 清理旧 runtime snapshot / run projection 语义并更新既有测试 | `turn_context`、Skill scope、Memory 等 item 在 provider 接受后只从 session model history 继承，不在工具续轮重复装配 |
| 2026-08-30 | 完成 contributor、Memory、cache epoch、provider transform 和隐私确定性收口 | backend ruff/mypy + `2352 passed, 4 skipped`；frontend lint + `670 passed` + production build；Rust test + release check。既有 bilibili atexit、duplicate `uv.exe`、CSS/chunk 与 Rust dead-code warning 均非本需求引入 |
| 2026-08-30 | 扩展真实 smoke harness | 同一安全长会话副本支持普通三轮对照，以及 current-time tool、跨日、timezone delta 的独立内容无关报告；安全 ref `24f9616a08f5` 仍可只读解析为 83,633 chars / 80 messages / 13 turns / 50 tool events / 0 attachments；未调用真实 LLM |
| 2026-08-30 | 经用户本次明确授权执行 7 次 `deepseek/deepseek-v4-flash` 真实调用 | 代码基线 `af033a5` + overlay `a4f2d144...`。普通三轮 cache read/input total 为 `1280/37258=3.44%`、`37248/37359=99.70%`、`37376/37444=99.82%`；第 2/3 请求均 `cross_user_turn/strict_extension`、首个新 item 为 messages、lost prefix `0`。动态场景为 current initial `4608/38519=11.96%`、工具续轮 `38528/38635=99.72%`、跨日 `38656/38752=99.75%`、timezone `38656/38866=99.46%`；后 3 次均 strict extension，跨日/timezone 只追加 runtime delta、lost prefix `0`。current 工具仅调用一次且 action-time 匹配，calendar items `1→2→3`，精确 runtime marker `0`。运行后原 session 仍为 118 events、`updated_at=2026-08-11T17:51:28.445874Z`，临时副本已删除；未归档 stdout、prompt、response、Memory、工具正文或逐 item digest |
| 2026-08-30 | 用户确认验收完成并要求合入 `main`、推送远端 | AC-1～AC-11 全部完成；`maintain-agent-friend-guide` 合入前复核判定无需更新公开知识：本次没有新增用户操作、设置、凭据、数据落盘或可见错误语义 |
