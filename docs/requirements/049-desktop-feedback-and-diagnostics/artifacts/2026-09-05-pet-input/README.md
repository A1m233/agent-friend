# 2026-09-05 · 桌宠输入诊断证据归档

这是需求 049 离线证据增量的历史输入，不是已实现的产品诊断包，也不是原 DPR 故障已经修复的证明。原始 JSON 保持字节不变；逐文件来源、SHA-256、历史运行身份与局限见 `MANIFEST.json`。本目录只供项目内部复查。

## 证据性质

| 文件 | 覆盖与解释边界 |
|---|---|
| `raw/pet-input-evidence.json` | 主 agent 将原故障现场 CDP/原生只读观察整理成结构化摘录，并非完整原始逐帧流。无精确采集时间字段；关联 PID 42852 的 2026-09-05 本地 03:47 启动。确认画面 DPR 1.25 与 cursor feed 换算 1.75 失配；没有原始前端上报序列。 |
| `raw/pet-dpr-reload-2026-09-05T03-35-16-811Z.json` | 电脑重启后的安装版三次页面重载。数值可见的 DPR 上报及各轮末态为 1.25；两条 network 记录为 body-unavailable，不能补造其请求方法或内容。页面重载不等于原生冷启动。 |
| `raw/pet-dpr-transport-2026-09-05T03-35-33-888Z.json` | 人工构造 1.75→实际 DPR 的真实 IPC 对照，不是自然故障复现。串行 10/10 有效样本、并发 9/10 有效样本末态为 1.25；concurrent-8 没有稳定光标样本，不能计作成功或失败。末尾实际 DPR 恢复结果已保留。 |
| `raw/pet-dpr-dev-cold-1.json` | 当前源码加临时探针的普通 Tauri dev 冷启动：11 条 trace、末态一致；原生 setup 到首次写 DPR 约 6.687 秒。 |
| `raw/pet-dpr-dev-cold-2.json` | 预热缓存后再次普通 Tauri dev 冷启动：11 条 trace、末态一致；上述间隔约 3.509 秒。 |
| `raw/pet-dpr-dev-prebuilt-1.json` | Tauri dev 壳加载预构建的 development 前端资产，保留 StrictMode 与探针；不是安装器或独立浏览器预览。12 条 trace 记录 viewport 从 2926×915 到 4096×1280，而 DPR 始终 1.25；上述间隔约 244 毫秒。 |
| `raw/pet-dpr-dev-retained.json` | 恢复普通 Vite HMR dev 后的保留运行态快照：11 条 trace、末态一致，不是新的修复候选。 |

未观察到复现，不等于证明问题不存在。原故障时 WebView2 为 152.0.4191.62，dev 对照为 152.0.4191.66；版本、电脑重启及前端加载时序是混杂因素。.62 在重启电脑后也曾正常，因此不能把升级解释成恢复的唯一原因。

## 历史 provenance 与归档位置分开

- 原安装版 build 为 `20260902T153051Z-cb386a6a6e9e-278c9aef`，source commit 为 `cb386a6a6e9e3f60eb5e9c5d10b76550f95121d9`，构建标记 `dirty=true`；该 commit 不是完整安装内容的证明。
- dev 运行在旧 `fd7c/agent-friend` worktree、`fix/pet-input-dpr-desynchronization` 分支、`3d62404c2207c2d2307be262c7133c7c05b4d53b + 未提交诊断 overlay`。overlay 仅增加观测，不串行化、不重试或改写 DPR 上报。
- 新 049 工作区以 `c863ac793717f3b2335993a17471b5531d1326e4` 为归档基线；它没有运行这些历史实验，不能将其写成历史 clean revision。
- dev JSON 内的 `runtimeIdentityVerified: false` 只表示 collector 自身未检查运行实例身份。主 agent 另行通过 lease、进程路径/启动时间和 runtime journal 检查了实例归属；该外层核对单列于 manifest，不修改原始 JSON。
- 原生日志曾支持各次 PID、运行版本、setup 与 setter 时间的核对。本归档按授权不复制日志全文；这些外层事实是已有调查结果的派生记录，不伪装成原始日志文件。
- 原始位置采用 `fd7c/agent-friend/<相对路径>` 映射，指本机旧 worktree。原目录删除后可读取本归档，但不能假设该来源路径仍可用。

## Harness 与 overlay

`harness/pet-input-probe.mjs`、`pet-cursor-sample.mjs` 是原摘录的调查辅助脚本；其结果曾在工具输出中观察，未形成这里不存在的“完整原始响应文件”。它们依赖当时的安装 bundle/React 结构和原工作区依赖。

`harness/pet-dpr-experiment.mjs` 是当时保留下来的实验脚本。它会重载页面，transport 模式还会写临时 DPR；**本次归档没有运行它，也不授予再次操作安装版的权限**。它在 reload 报告生成后、transport 报告生成前加入了 network method 字段，因此这里只为 transport 保留对应的现存版本；reload 的精确旧脚本未保留。

`harness/scripts/pet-input-diagnostics/run.*` 是生成四份 dev JSON 的只读 raw CDP collector 快照。`overlay/App.tsx.diff` 是相对历史 dev HEAD 的精确 Git diff；对应新增模块位于 `overlay/frontend/src/pages/pet/petDprDiagnostics.ts`。探针条件是 `import.meta.env.DEV && VITE_PET_DPR_TRACE=1`，仅记录模块加载起两秒/最多 100 条内存事件。dev StrictMode 的重复 effect 不能外推为 release 重复挂载。

归档脚本保留原路径假设，不能在本目录直接运行并声称已还原现场。静态预构建资产、临时 Tauri 配置、Rust 二进制、依赖和用户模型未复制；这是最小测量代码锚点，不是可逐字节复原全部历史运行环境的 bundle。复跑需要另行授权并先恢复/核对原路径与运行边界。

## 完整性与隐私

复制前逐个解析检查七份 JSON 的键与字符串值，并人工核对选定 harness/overlay。没有保存认证 token、HTTP headers、聊天或模型输入输出；network 数组只含所选 DPR 请求的编号、方法（原文件有时缺失）、时间、DPR 数值与响应状态。保留的是有限坐标、DPR、构建/进程标识及 loopback/产品本地 URL。

没有归档截图、原生日志全文、聊天、预编译资产、整个临时目录或用户数据。所有复制项拒绝同名覆盖并校验源/目标 SHA-256；本目录的 .gitattributes 禁止 Git 换行转换，保持跨平台 checkout 字节一致。manifest 不包含自身 hash，避免自引用。派生 README/manifest 与原始观测性质不同，不应用作新的实验结果。
