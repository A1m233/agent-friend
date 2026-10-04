# 149 · 启动加载时间证据采集进度

## 总体状态

COMPLETED — 用户在查看最终验收结果后明确要求“合入主干推送远端”，确认验收并授权交付。最终 candidate 4 的隔离采集、14 项自动安装矩阵、标准报告校验及后端/前端/Rust 检查通过。

## 任务与 AC

- [x] 需求/方案回溯确认，保留历史证据。
- [x] 基础 phase、只读 helper、源码检查与局部基线（E-001～E-008）。
- [x] 普通启动接入 payload 统计，真实安装样本采集成功。
- [x] 页面终点与 ZIP 消费已实现；早期混用端口的解释撤回，后续路由修复和新样本通过。
- [x] AC-1/2/3：新埋点定向测试与安装态实际时间线。
- [x] AC-4：真实隔离安装正常/失败或中断记录及诊断 ZIP 导出。
- [x] AC-5：本次新证据、源码/构建锚点和局限归档。
- [x] AC-6 机器部分：项目检查、安装矩阵通过。
- [x] AC-6 用户部分：用户明确要求合入主干并推送，确认本次验收。

## 实现日志

| 日期 | 完成内容 | 验证或备注 |
| --- | --- | --- |
| 2026-10-04 | 用户确认测量阶段，写入 design 并进入 M0 实现 | 无生产重启、安装或模型调用授权扩大 |
| 2026-10-04 | 完成粗粒度启动 phase、校验累计统计、verify/report/build-helper 入口 | Rust 库测试和 release check 通过；脚本定向测试 13 项通过；首次 helper release 构建完成 |
| 2026-10-04 | installer-contract 机器检查通过 | 当前路径 floor=full-matrix，后续合入前须满足；M0 局部验证不替代安装矩阵 |
| 2026-10-04 | M0 源码检查与局部校验基线完成 | 后端3809通过/5跳过，前端1362通过，Rust309通过/1忽略，当前脚本14项定向测试通过；细节和时间范围见 E-008 |
| 2026-10-04 | 5 次只读真实 payload 校验全部通过 | 首轮3.54秒，后续中位数1.20秒；E-007。没有修改安装、重启 AF 或调用真实模型；未实施性能优化 |

| 2026-10-04 | 用户明确收窄为加载时间证据采集，并同意补齐实际启动统计、前端终点和安装态导出验证 | 更新 requirement/design；取消优化算法、收益门槛和复现 84 秒的交付要求 |

## 后续与保留状态

继续采集与导出闭环。历史 84 秒原因未知，helper 基线不能解释它。未提交、未合入；用户 AF 保持不变。

## 安装采集进展（未替代最终门禁）

- candidate 1：20261004T091824Z-fedb4fc637da-a2133230，sourceTreeSha256=2a2a393e702939f10e97b80c1df2a0fb8c7fdfad5949fa745b13e05454ca1614；保留的 overlay 可重算到相同值。
- installed-01：Codex MSIX AppData 写重定向被预检拒绝，无安装动作；改用项目已有 Explorer broker 启动同一用户的正常 Windows 进程，同一预检通过，未关闭检查。
- installed-02：已取得真实校验与后端分段，但 DOM 脚本选到隐藏 textarea，未完成导出；失败日志保留。
- installed-03：等待 bridge ready 后打开 chat，定位可见 textarea，真实输入并清空通过。正常进程 74696-1791107737700；受控中断进程 55924-1791107737383。ZIP outcome=complete，消费者能取回两个进程及中断的未结束阶段。
- 该次校验 1331.39 ms，7,489 文件 / 1,218,270,509 bytes；bridge readiness 等待 9176.40 ms；frontend-ready 原生接收时间 15416.30 ms。仅证明采集闭环，不是性能基线或历史 84 秒归因。
- 实际观测推翻了 setup 必定嵌套于 build 的假设，新增 tauri-initialize 覆盖 build 到 setup 入口；前端 ref 假设未获证实，保持原实现，仅修测试等待与可见 locator。
- 最终 candidate 2：20261004T095950Z-fedb4fc637da-cf7861db；sourceTreeSha256=a2df67304d46c99be5949983a518e677c0f40d073dd00fc9e5135717c8c1d66a，overlay 重算相符。候选已构建，新增区间已取得原生实测；完整 E2E 与安装矩阵等待隔离缺陷处理。
- 最新源码检查：backend 3812 passed / 5 skipped；frontend 1365 passed；Rust test/release check 通过；最新定向脚本/consumer 61 passed，lint、adapter sync/doctor 通过。

私有运行报告、原始 ZIP 和 overlay 均保存在本机 `C:/Users/example-user/.cache/agent-friend/149-startup-evidence/`；最终收口时补充仓库内脱敏索引和 hash。production AF 未重启或替换。

## 最新状态修订

installed-04 推翻 installed-03 的空数据隔离结论。前端固定生产端口，测试 supervisor 使用独立端口；实际截图出现生产会话标题。保留原始成功/失败输出，撤回前端隔离及端到端通过判断；原生计时、日志保留和 ZIP 携带两个进程的事实仍有效。详见 installed-evidence.md 和 artifacts/isolation-assessment.json。

当前不继续安装测试或矩阵；测试进程/测试状态已清理。新增脚本防护在生产服务正在监听时提前拒绝，定向测试 62 项通过。既有路由问题的修复不在已确认采集范围内，已向用户询问是否纳入独立修复。没有提交/合入，也没有重启或替换 production AF。

### 用户确认后的推进

用户随后回复“可以”，授权修复路由并继续采集。本次作为 149 验收的必要依赖一并处理，不扩展到性能优化。前端所有 WebView 在模块运行前接收 embedded profile 的 bridge/voice 地址；缺失/非法配置 fail closed。原生 push subscriber 也改为读取同一 profile，移除不被 supervisor 使用的独立 URL override。

新增 profile 路由回归：前端 164 文件 / 1367 测试及 lint/typecheck/build 通过；Rust 311 通过 / 1 忽略，release check 通过；脚本和诊断 consumer 62 项通过。安装采集改为检查 bootstrap、实际请求 origin、初始空会话数量、ZIP 中 readiness 身份，并从启动前启用 Chromium NetLog，拒绝生产端口事件。旧样本继续保留为部分证据，不追认其隔离结论。

installed-06 已取得有效隔离实测：原生计时、前端终点、真实输入、空数据和 ZIP 中正常/中断进程均通过。详见 installed-evidence.md。installed-05 的等待失败与 matrix-03 的输入指纹拒绝保留为失败证据；candidate 4 构建前冻结全部代码与采集器，不用修改报告或降低门禁来复用旧包。

### 最终机器验收

candidate 4：20261004T113552Z-fedb4fc637da-f01e57f1。installed-07 通过，最终样本及源码/ZIP 哈希见 E-011；14 项自动矩阵全通过，标准 installer-smoke-verify 返回 0。临时 Guide baseline、隔离安装状态与本次 candidate 构建工作区已恢复/清理。

后端测试并行于打包时有 1 项固定 Guide pin 断言失败，3812 项通过、5 项跳过。原因有明确输入证据：测试读到临时发布 baseline d8ae318，而源码断言预期 b26c637；未修改断言。所有打包动作结束后重跑项目 backend 阶段，3813 passed / 5 skipped，lint/typecheck 通过，退出码 0。

最终检查汇总见 artifacts/final-validation.json：frontend 164 文件 / 1367 测试通过，lint/typecheck/build 通过；Rust 311 passed / 1 ignored，release check 通过；14 项自动安装矩阵及标准绑定校验通过。现有第三方退出清理 warning 保留，不影响退出码。artifact hash 已复核；隔离 install/data/settings/logs/move roots 均不存在，测试端口已释放，production PID 58512、启动时间 16:07:16 未变化。没有真实模型调用或 production 覆盖安装。

### 用户验收与合入准备

用户已确认合入并推送。origin/main 仍为 fedb4fc637da0b6d1fb171dd2e9c06811fb70429，与实现基线一致；无需冲突解决或重建安装包。归档 JSON 保留测量时尚未验收/提交的历史状态，本节记录后续确认，不改写原证据。公开 Guide 按 merge-main-and-push 自动流程补充启动慢时的诊断取证入口和保留边界，完成公开发布后合入。
