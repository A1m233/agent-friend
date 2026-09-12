# 127 · 验证记录

日期：2026-09-12。历史执行身份：基线 `0cb77e630d26b5eb8eca89bb2cc86c273535aede` 加本需求未提交前端 overlay；不是已提交候选。

## 自动化

| 入口 | 结果 |
| --- | --- |
| `scripts/frontend/test.ps1 src/pages/chat/components/executionDisclosurePolicy.test.ts src/stores/assistantPhase.test.ts` | 65 passed |
| `scripts/check/run.ps1 --stage frontend` | lint/typecheck、颜色/来源/token guard、1294 tests、production build 通过 |
| 设置 `PYTHONPATH=scripts/test` 后 `scripts/check/run.ps1 --stage backend -p no_external_http` | lint、680 文件 mypy、3067 passed / 5 skipped，exit 0 |
| `scripts/check/run.ps1 --stage rust` | fmt、cargo test、release check 通过，exit 0 |

首次 backend 命令漏设插件模块路径，在 pytest 收集前失败；纠正调用环境后完成整段复验。门禁输出保留既有第三方依赖、CSS highlight、chunk size 和 Rust dead-code warnings；没有通过隐藏 warning 或跳过失败用例取得通过。

## 真实桌面

用户明确授权真实桌面验收，并自行退出正式版。最终使用当前 worktree 的 Tauri / WebView2，CDP `127.0.0.1:9222`，页面 `http://localhost:1420/chat.html`，viewport 1040×720。

最终 runtime lease：`feature/127-unified-execution-disclosure`，owner PID 3924，启动于 `2026-09-12T22:42:00.568709+08:00`；Tauri owner PID 27224。该实例晚于所有生产源码变更，探针还通过页面实际加载的 policy 核对 mixed/收起/过程正文 ID 行为。

14 项检查全部通过，pageErrors 为空，探针未发现意外 HTTP 写请求，结束后 restored=true：

- 无阶段完成轮自动收起、真实点击展开/收起；收起状态复制仍保留过程和回复。
- 通过真实引用按钮引用展开的过程正文，保留原 mid、正文及 UTF-16 定位；按钮中心命中自身，pointer-events 为 auto。
- 搜索目标指向原 mid 时自动展开并定位。
- 原生完整阶段收纳 commentary，unknown 与 final 保持可见。
- 同一挂载消息从运行中转正常终态后默认收起。
- 取消、失败、缺可信旧终态、conflict 和纯直答边界保持。
- CPA 型 legacy/commentary-only、native/partial、mixed unknown 三种状态均能收起过程；末尾和未知正文可见，手动展开不被更新覆盖，原始 phase/validation 不变。

证据层级为 desktop WebView screenshot / desktop DOM via CDP / desktop interaction。采用合成内存消息和运行事实，不发送真实 LLM 请求、不执行工具、不改写会话文件；历史持久化与投影由自动化测试覆盖。引用与复制验证到 UI handler/合成 draft/截获 clipboard-write 边界，不写系统剪贴板；不声明已验证真实 CPA 输出质量或模型调用 E2E。

## 失败探针与恢复

run1–5 均保留为失败验证尝试，不计入通过数量。先观察到空根和 `SessionApprovalModeController is missing`；后确认直接导入的 store 与页面 HMR 带时间戳实例不相同。期间隔离挂载探针还遇到 Vite CommonJS 默认导出及容器位置问题，均未修改产品代码。最终停止本任务运行态，重启无 HMR overlay 干扰的实例，改用页面 barrel 导出后，完整 ChatApp 上的 run6 通过；不再把此前错误作为产品启动缺陷。

## 本机私有产物

目录为 `C:/Users/example-user/.codex/visualizations/2026/09/12/01a09449-c015-7463-abdd-ef412fd3f8a8/`，仅本机可访问。`probe127.mjs` 基于 118 的已有探针调整；`desktop127-run6/report.json` 和截图为最终证据，run1–5 为失败尝试。截图包含正常侧栏，未复制进仓库。

| 产物 | SHA-256 |
| --- | --- |
| `probe127.mjs` | `d28a706d0cd9b39e1ec6cbe14d39d9a41941ca45008bca00f7f91626d5d11af3` |
| `desktop127-run6/report.json` | `fc1142b41eec23d19b91d2c0409b245af27d4574ce3866abba60c960f444373f` |
| `executionDisclosurePolicy.ts` | `12e0b3a6b25a3e892e7168ddef2eaf9e73ce954ecf98b727fd234576dde40e05` |
| `assistantPhase.ts` | `30dc5490df6dc976d0c8be54b63b458ab9b3b6370a5fb5ee3802fb332a99e094` |

验收后已通过 `scripts/dev/run.ps1 stop` 清理本任务实例；status 确认无活动 lease、owner 已退出、开发端口空闲。未自动重新启动正式版。用户随后要求合入主干并推送远端，产品最终验收已确认。
