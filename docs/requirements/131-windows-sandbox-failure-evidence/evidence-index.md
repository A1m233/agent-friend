# 131 证据索引

## 范围与当前判断

服务沙箱失败现场保留和诊断包按轮次采集。仅保存脱敏事实与来源，不复制客户原始诊断包、聊天、附件或系统状态秘密。

盘点基线与分支起点：`fbca543f3af138676f0680f3677f0525ee43ca96`。历史运行源码基线不同且 dirty，不能把当前代码盘点直接当作客户运行树。

已确认现有诊断存在可定位性和留存缺口；客户当次 helper_incomplete 的具体底层原因仍未确认。客户明确补充后续为手动点击修复，不得据后续 ready 推断自动恢复。

## 证据账本

| ID | 主张与来源 | 结果、局限与置信度 | 状态 |
|---|---|---|---|
| E-001 | `agent-friend-diagnostics-20260914-020708.zip`；项目 inspector 原位检查 | CRC 通过，62 entries，format=1/revision=12，collector complete；会话 526427748 bytes 超过 64 MiB 消费上限，不能称完整会话检查已通过。runtime-supported / confirmed | active |
| E-002 | 包内 application、command-runtime、install integrity | build=`20260912T155450Z-366d285b0d3f-1616190a`，基线 `366d285b0d3f02a7716d2e49df53d831947b9eac`，dirty=true。事件 build hash 与该 build ID 的 SHA-256 前 16 位吻合；安装一致性仅证明导出状态。runtime-supported / confirmed | active |
| E-003 | command-runtime 的 start 事件及会话中 exec_command 错误 | 北京时间 01:51:18 请求、01:51:51 helper_incomplete；报助手成功退出但 setup 未完成。无法据此指定 DLL 或外部拦截原因。runtime-supported / confirmed（失败边界） | active |
| E-004 | command-runtime 的 helper/probe/setup；当前会话用户补充 | 01:59:55 helper ok，01:59:59 probe/setup ok；用户确认后续手动点击修复。成功不能否定此前故障，也不证明原业务恢复。runtime-supported + user-reported | active |
| E-005 | `agent/src/agent/command_runtime/diagnostics.py` | 128 条队列共用正常事件与故障；仅有限错误分类，setup/probe 使用缺省 operation；恢复清除 repairRequired。当前包含 evicted。code-supported / confirmed | active |
| E-006 | `runtime.py` 的 setup、失败处理与临时日志消费；`exec_server.py` | 显式 setup 临时日志读取后删除；持久诊断未保存底层详情；stderr 仅内存尾部，observer 主要处理网络事件。不能由此断言底层从未产生日志。code-supported / confirmed | active |
| E-007 | 锁定版官方 `setup.rs`、`identity.rs`、`setup_error.rs`、`logging.rs`，入口见下 | helper_incomplete 对应退出后 marker/users 的版本与可读解析检查；底层已有错误报告与日志，后续 setup 可清理报告。当前包未采集这些专用证据。code-supported / confirmed；客户发生时文件内容未知 | active |
| E-008 | Tauri diagnostics/mod.rs 的 command runtime collector | 通过 Bridge 请求当前快照；请求失败返回 unavailable，未回读持久故障文件。code-supported / confirmed | active |
| E-009 | 同文件会话采集；Bridge routes/meta.py::get_session | 默认 full 同时返回 events 与 active_events；Tauri 完整解析后移除 active_events。分页只允许 active 视图，不能不加判断直接替代诊断原始证据。code-supported / confirmed | active |
| E-010 | 用户追加需求与 E-001 | 用户报告 ZIP 可超过 100 MB、耗时长，要求最近 X 轮；E-001 直接证明会话 JSON 体积大，但未测得客户各阶段耗时和瓶颈占比。user-reported + runtime-supported；性能成因尚需测量 | active |
| E-011 | `scripts/diagnostics-smoke/run.ps1`，2026-09-14 本机合成 JSONL，固定最后 10 轮，仅增加旧历史 10 倍 | 源 16,942,795→169,441,221 bytes；读取均 65,587 bytes；会话输出 1429/1443 bytes，含事故的最小 ZIP 1384/1386 bytes；读取序列化 ZIP 8.11/9.74 ms。受控读取边界 confirmed；时延为单次观察，非客户整包指标 | active |
| E-012 | backend/frontend/Rust 门禁、诊断范围及故障专项、consumer 真实 ZIP fixture | 全量后端 3157 passed/5 skipped，前端1295 tests与构建，Rust284 tests与release；最后局部变更94项Python及37项Rust专项复验通过。源码机制 confirmed，具体客户事故根因仍未知 | active |
| E-013 | Windows 11 10.0.26200 的 package-sensitive frozen doctor、服务 smoke 与 embedded build-identity | build 20260914T025449Z-fbca543f3af1-5b8244d9；dirty=true；source tree 511abcfc54fe1f39051318c03e44a0939d838847a3f30f27801746931c26c832。新增 range/failure_retention/repair_link 全通过；不是 Windows 10 现场验证。runtime-supported / confirmed（本机构建） | active |

## 官方版本锚点

本节仅为复查来源，不作为产品需求或方案的决策规范。来源已于 2026-09-14 定向读取。

- [setup.rs](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/setup.rs)：`verify_setup_completed`、错误报告清理与读取。
- [identity.rs](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/identity.rs)：`sandbox_setup_is_complete`、命令前非提权 ACL refresh。
- [setup_error.rs](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/setup_error.rs)：底层错误分类与报告生命周期。
- [logging.rs](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/windows-sandbox-rs/src/logging.rs)：默认日志与仅 debug 开启的消息边界。

## Artifact 与查询映射

- 原始 ZIP 由用户在当前会话附件中提供，本机可访问；不上传或复制到仓库。文件名见 E-001，私有完整路径留在会话附件中。
- 使用 `.cursor/skills/inspect-agent-friend-diagnostics/scripts/inspect_bundle.py` 原位检查；随后仅定向读取 application、command-runtime、会话错误位置。
- 未执行客户机器复现、真实 LLM 调用、系统修改或新的安装包构建。
- 新增冻结运行时验证的 build、路径、命令与失败尝试见 progress；临时合成性能样本已随 harness 清理，指标保留于 E-011，未保存客户内容。
- 原始会话过大，未完成全量隐私契约验证；本索引仅消费已确认错误与结构统计，不包含内部推理正文。

## 未决问题与决策影响

- E-005～E-008 支持独立事故留存、底层证据采集、修复关联和 Bridge 离线回读；具体预算与捕获协议待设计确认。
- E-007 纠正初始“可能握手失败”的宽泛解释：当前已知判定是 setup 状态完整性失败；更深根因未知。
- E-009～E-010 支持专用诊断范围与源头限量，不能只裁 ZIP 或直接套用聊天分页丢失原始证据。
- 最近 X 轮及预算已在 design 确认并实现；嵌套快照不回带历史，异常边界明确 partial。单次合成时延不作为客户体验保证。
- Windows 11 冻结能力已验证；Windows 10 实际故障矩阵、客户现场恢复与真实整包体验仍待验证。

## 对账记录

- 2026-09-14：用户确认需求与编号并追加按轮次采集；已建立并推送 feature/131-windows-sandbox-failure-evidence 占号分支，requirement 为 CONFIRMED；未开始设计落盘或产品实现。
- 2026-09-14：设计与实现已获授权并完成本机验证，E-011～E-013 补充实现证据；原现场根因结论未提升，progress 保持 IN_PROGRESS 待验收。
