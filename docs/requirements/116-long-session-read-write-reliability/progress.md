# 116 · 长会话查询与读写可靠性 - 进度

## 总体状态

COMPLETED

需求、方案、实现和验收均已完成；用户于 2026-09-06 明确确认“通过”，并授权合入主干、推送远端。开发验证在独立 feature 分支完成，用户原运行实例保持原状。

## 任务列表

- [x] 存储存在性、锁外解析和锁观测及回归测试。
- [x] Manager / Bridge 轻量授权和详情复用及回归测试。
- [x] 前端授权刷新合并、事件过滤和版本保护及回归测试。
- [x] 集成受影响测试、项目 backend / frontend / rust 检查。
- [x] 汇总验收证据与局限。
- [x] 用户验收。

## AC 验证

| AC | 状态 | 证据 |
| --- | --- | --- |
| 1 轻量查询 | 已通过 | 短 / 2 MiB 合成会话 GET 禁止 load 仍返回授权状态；存储 exists 不解析历史 |
| 2 错误与权限边界 | 已通过（平台边界见日志） | 缺失 404、存储错误不伪装 404、坏历史只读允许；PUT / 组合审批仍拒绝坏历史；路径编码、目录与属性错误覆盖 |
| 3 刷新合并 | 已通过 | 专用 refresher 的共享 GET、尾随失效与失败清理，以及 content 不查询授权 |
| 4 运行中与跨会话一致性 | 已通过 | active、乱序 revision、导航 / generation / 响应身份、冷启动 / 重连 / 修改失败及旧详情回填保护 |
| 5 详情读取 | 已通过 | full / active × 稳定 / 读取期间变化四组断言，full load 分别 1 / 2 次 |
| 6 并发与恢复 | 已通过 | 1 / 4 读者并发解析期间真实追加完成，旧 / 新快照无混合；rollout 和工具恢复定向回归 |
| 7 锁观测 | 已通过 | 成功 / 慢等锁 / 超时 / I/O / 解析失败、锁外日志、计时分离和内容隐私；日志自身失败不影响存储 |
| 8 锁范围决策 | 已通过 | design.md 锁内 bytes / 锁外解析；同步 barrier 证明写入进度，不依赖性能阈值 |
| 9 开发隔离 | 已完成 | 临时目录 / 合成数据；测试加载外部 HTTP guard；未操作用户真实会话或 runtime，未运行 MAA / 真实 LLM |
| 10 项目门禁与验收 | 已通过 | backend、frontend、rust 均 exit 0；用户明确验收通过，详见下方日志 |

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-09-06 | 写入确认方案，进入实现 | 基线 b88341a09ec6955a6da01d3f0ff6adafdc9f39ae；历史事故未稳定复现 |
| 2026-09-06 | Manager / Bridge 与回归完成 | `scripts/test/run.ps1 -p no_external_http agent_bridge/tests/test_command_approval_routes.py agent_bridge/tests/test_meta_channel_routes.py`：53 passed / 49.23s；PYTHONPATH 指向 scripts/test；初次插件包路径不匹配，修正搜索路径后执行成功 |
| 2026-09-06 | 准备独立工作区依赖 | 前端 frozen-lockfile 安装成功，817 包复用缓存；uv 创建本工作区 .venv，安装 139 项依赖；未启动产品实例 |
| 2026-09-06 | 前端实现和定向验证完成 | `scripts/frontend/test.ps1 src/stores/approvalModeRefresh.test.ts src/stores/conversation.test.ts src/stores/sessionSync.test.ts src/pages/chat/components/SessionApprovalModeController.test.ts`：4 files / 94 passed / 4.60s；diff whitespace 检查通过 |
| 2026-09-06 | 存储 / 恢复定向验证完成 | `scripts/test/run.ps1 -p no_external_http agent/tests/test_session_store.py agent/tests/test_session_store_concurrency.py agent/tests/test_session_store_observation.py agent/tests/test_session_changes.py agent/tests/test_session_rollout.py agent/tests/test_session_recovery.py agent/tests/test_conversation_history_tool.py agent/tests/test_session_history_source.py agent/tests/test_session_search.py memory_eval/tests/test_adapter.py`：首轮 123 passed / 1 skipped（Windows 不支持的 POSIX raw 冒号 legacy）；后续 store 三组 42 passed / 1 skipped；日志保护最后复验 14 passed / 11.81s |
| 2026-09-06 | 进入项目全量检查 | frontend / rust / backend 各阶段使用 scripts/check；Rust target 由 scripts/package/paths.py 解析到当前 worktree 专属外部缓存，不构建安装包 |
| 2026-09-06 | 前端全量门禁通过 | `scripts/check/run.ps1 --stage frontend`：lint / 类型与样式守卫、128 files / 836 tests、生产构建全部成功（287.6s）；构建仍提示既有 CSS highlight 优化与大 chunk warning，本期未修改这些资源 |
| 2026-09-06 | Rust 测试通过，release 检查执行中 | cargo fmt 通过；库 252 passed / 1 ignored，installer helper 6 passed；源码无 Rust 变更 |
| 2026-09-06 | 后端 lint / 全包类型检查通过 | 首轮 mypy 指出新增测试通过被测模块访问未显式导出的 hashlib；改用测试已有的标准库 import 后，719 个文件 lint/format 和 619 个文件 mypy 均通过。定向 mypy 的旧导出解析疑点未在全包入口出现，无需修改旧导出 |
| 2026-09-06 | Rust 全量门禁通过 | `scripts/check/run.ps1 --stage rust`：fmt、258 tests、release check 全部成功（445.1s）；1 项现有 Windows 电源策略真机 smoke 按其 ignore 标记未执行；保留既有 dead-code warning |
| 2026-09-06 | 后端全量门禁通过 | `scripts/check/run.ps1 --stage backend -p no_external_http`（PYTHONPATH=scripts/test）：lint/format、619 source files mypy、2641 passed / 5 skipped 全部成功（448.6s，pytest 433.54s）；包含重复 ZIP 名称测试警告，退出时第三方 bilibili_api 清理遇到已关闭 event loop，runner exit 0 |
| 2026-09-06 | 整理交付 | 三份需求文档 UTF-8 without BOM、相对链接检查和 git diff --check 通过；实现保留在 feature/116-long-session-read-write-reliability，尚未提交 / 合入，等待用户验收 |
| 2026-09-06 | 用户验收通过 | 用户明确回复“通过，可以合入主干推送远端了”；进入保留 merge commit 的主干交付流程 |

## 验收边界

- 自动化已证明轻量查询、授权版本保护和解析期间写入进度；历史 10 秒锁超时的具体持锁者仍未知，没有宣称完整复现或彻底消除所有超时。实际磁盘读取仍持共享锁。
- 本机为 Windows，POSIX raw 冒号旧文件名用例跳过；其余项目原有平台 / 可选跳过保留。Rust 的 Windows 电源策略真机 smoke 不在本期范围。
- 本期为内部查询 / 状态 / 锁范围调整，未执行真实桌面端视觉验收，也没有启动、重启或切换用户运行实例。
- 模型切换返回旧配置属于独立问题，本期未修改该路径；协议字段和 supervisor 等架构优化仍按原拆分处理。
- 用户已确认验收完成，总体状态为 COMPLETED。
