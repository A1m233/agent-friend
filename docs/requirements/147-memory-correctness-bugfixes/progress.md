# 147 · 实施进度

## 状态

COMPLETED

2026-10-01：用户已验收五项修复并确认记忆响应等待改为 10 分钟，授权合入、推送与本机打包。全部 AC、合入、远端推送与本机测试包构建已完成。

## 任务

- [x] 严格生产抽取协议及显式旧格式兼容（AC-01、02）
- [x] 来源失效覆盖历史版本（AC-03）
- [x] 观察时间、写入时间及迟到冲突（AC-04、05）
- [x] 单条异常隔离与既有恢复边界（AC-06、07）
- [x] Core 解码及完整投影降级（AC-08）
- [x] 回归测试及项目检查（AC-09）
- [x] 用户验收（AC-10）
- [x] 两阶段记忆响应等待 600 秒及隔离测试（AC-11）

## 实施记录

- 2026-10-01：从 origin/main `71cc9094a89d96408aa24be314698809c885eab0` 创建并占号 `feature/147-memory-correctness-bugfixes`。原有相关测试基线 88 passed，详见 evidence-index；此基线不代替修复验证。
- 2026-10-01：落盘已确认方案。未修改用户数据、未执行真实 LLM、未启动用户实例、未构建安装包。
- 2026-10-01：五项实现落地，生产协议默认严格，历史 cassette / 配对与 factorial harness 显式启用兼容；普通测试 fixture 更新为当前协议。pending v8 迁移仅增加调度字段，不重跑 v7 已完成的旧 pinned 迁移。
- 2026-10-01：新增正确性回归初跑 12 failed / 2 passed；其中来源失效用例调用漏用 keyword 参数，修正后再验证，不能把该初始 TypeError 当作产品失败证据。其余失败与诊断一致。修复后记忆及 memory_eval 测试 263 passed / 2 skipped；补充边界用例后正确性回归 29 passed。
- 2026-10-01：全项目门禁进行中。frontend lint、1352 tests、production build 已通过；后端 lint、mypy（773 files）通过，全量 pytest 与 Rust 门禁仍在执行。此前测试 ignore 注释因格式化错位导致类型检查失败，已修正。

- 2026-10-01：全量 `scripts/check/run.ps1 --stage backend` 完成：3785 passed / 5 skipped，lint、mypy 通过。全量运行启动后的限定变更（数值/身份校验、时间错误隐私、blocked 与服务冷却组合）另跑 `scripts/check/run.ps1 --stage backend memory/tests memory_eval/tests`：287 passed / 2 skipped，lint、mypy 同步通过，含 38 项新正确性回归。未重复无关前端/Rust 验证。
- 2026-10-01：frontend gate 通过（1352 tests、lint/typecheck、production build）；Rust gate 通过（fmt、test、release check）。`git diff --check`、四份文档 UTF-8 无 BOM、相对链接和尾空格检查通过。依赖层存在 LiteLLM coroutine、Bilibili atexit 清理警告，pytest 和 gate 均以 0 退出；没有将这些提示归为本次产品修复。

## AC 对账

| AC | 证据 | 结论 |
| --- | --- | --- |
| 01、02 | test_memory_correctness 的协议矩阵、合法空提交幂等、显式 legacy；test_extractor_pass1 历史 cassette；factory / paired / factorial 测试 | 通过 |
| 03 | 当前与退休来源失效、跨会话 replacement 后历史搜索、stale dense hit 回查 | 通过；保留审计，不自动复活旧事实 |
| 04、05 | 观察/写入时间断言、历史日期查询、Core remove 后迟到 add、Semantic 迟到更新原子拒绝、最大观察时间与同轮多值 | 通过；旧数据不回写 |
| 06、07 | v7→v8 迁移、blocked 重启/编辑/两条预算、畸形 payload、身份不符、时间冲突后正常前进；原 test_deferred_extraction 冷却/Retry-After/生命周期及组合退避回归 | 通过 |
| 08 | Core 多种损坏、完整投影不可用、普通召回保留、共享损坏不逐条隔离、基础设施异常不伪装为数据错误 | 通过 |
| 09 | 全量 backend、最终改动专项 backend、frontend、Rust 三阶段门禁与文档检查，详见 E-010 | 通过，skip 与 warning 已保留 |
| 10 | 用户明确要求按确认方案修改、合入主干、推送远端并打包 | 通过 |
| 11 | test_extraction 两阶段 policy 与重试控制参数化测试；timeout_policy 原默认测试，共 52 passed | 通过 |

## 交付限制

- 用户验收与本次交付已完成；源码、指南和制品绑定见下方记录。
- 未调用真实 LLM、未操作个人记忆库、未重启或覆盖安装态；已构建 local-test 安装包，未做桌面视觉验收，本次没有 UI 变更。
- PDF 内容接入、历史 Core 补齐和历史有效期修复不在本期范围内。blocked 材料需另行明确处理，不自动恢复或删除。

## 2026-10-01 交付续跑

用户确认既有修复结果，并要求两个记忆阶段的完整响应等待先改为 10 分钟，随后合入 main、推送远端和打包。此授权覆盖本次产品提交及 merge-main-and-push 的 Guide 自动复核/必要维护；不含自动安装。

- [x] 两阶段显式响应等待 600 秒，保持默认连接/写入/空闲与原重试控制。
- [x] AC-11 与最终后端门禁。
- [x] Guide 复核、必要发布与 package-baseline 解析。
- [x] 提交、合入 main、推送。
- [x] standard local-test 完整构建与制品核验。

初始打包预检：项目/本机最新正式版本均为 0.3.0，正式同版本目录已存在，trusted-beta 目标过期。本次按本机测试用途选择 local-test。代码 floor=standard、packageRequired=false；用户明确要求产包，因此执行完整构建。修改前 packageInputSha256=2909f029697c041a5389392df0481eb47fca1e85c28883994103c3f19ee1614f，构建前以 runner 重算的最终输入为准。

- 超时增补专项：`scripts/test/run.ps1 memory/tests/test_extraction.py llm_providers/tests/test_timeout_policy.py -q --tb=short`，52 passed；无真实模型调用。Guide 预览确认需补充记忆等待与持续未更新的排查说明，基线 `99b7643ae83411052760274658a8b1db9dbe5300` 为当前目标祖先；公开兼容范围沿用 0.3.0 至 0.4.0（不含），新行为使用版本限定表述。

- 最终源码全量 `scripts/check/run.ps1 --stage backend`：3796 passed / 5 skipped / 8 warnings，lint、mypy 通过，退出码 0。与前端和 Rust 已通过的代码输入无新增差异；依赖层既有清理警告仍保留。

## 交付结果

- 产品修复提交 `3e3dbeccae2daad30c3c724ceac3b15fd6d1f249`；merge commit `87bb34d2d779cc03ca8d26b4e0d286fd5329f925` 已推送 origin/main，feature 分支同步推送。合入树与修复提交完全一致。
- Guide 已完成 validator 与 verify，发布 [r72](https://github.com/A1m233/agent-friend-guide/releases/tag/agent-friend-guide-r72)，commit `a44d491c708b8c7e947f0ddcc1f7c23afb2a0642`；API 确认为 immutable，兼容范围 `[0.3.0, 0.4.0)`。后续仅补充本交付记录，无新增用户知识变化，不重复发布。
- 安装包：`agent-friend_0.3.0_windows_x64-local-test-NOT-FOR-DISTRIBUTION-setup.exe`，391628868 bytes；版本 0.3.0，Windows x64，ordinary/local-test，publishable=false，sensitive=false。属于同版本本机测试包，不是正式升级或分发制品。
- build ID：`20261001T144253Z-87bb34d2d779-8259578d`；基于上述 main merge commit，standard 完整构建返回 0。冻结 doctor 全部通过，包括 memory 模块、模型资源和 512 维本地查询；没有真实 LLM 请求，也没有执行安装。
- installer SHA-256：`afd5662b58ffb848e3bf6457c955ab8e14506a9e2a52d9135f8c41296c905f7b`，实算与 sidecar、release.json 一致。
- 最终 packageInputSha256：`547fc3d49c67e1aa8bd94b896ba4ad98ab3868beef0737239021b35cd82b05fd`；Guide snapshot `6c4d208bb8e7c5f36fbbbbce05d307d5d92844c286de908615c3156284cdb63d`。清单 source.dirty=true 来自受治理的临时 Guide baseline，lifecycle=transient；构建返回后工作区已恢复 clean，build workspace 由 runner 自动回收。
- 本记录属于构建完成后的文档收尾，未改变该安装包对应的运行时代码；不为文档提交重新打包。
