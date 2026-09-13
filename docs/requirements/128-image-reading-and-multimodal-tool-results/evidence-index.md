# 128 验证证据索引

本索引记录 2026-09-13 的实现验证；范围是本地图像工具、模型投递、历史治理和权限文案，不提供所有模型或真实图片分布的质量保证。

## 当前判断与代码身份

工程检查、真实 Tauri 文案/交互观察、DeepSeek Flash 单次看图观察、冻结 runtime 检查均已完成。用户已在验收结果后要求合入主干并推送，当前进入提交与合入流程。

历史运行代码为 `366d285b0d3f02a7716d2e49df53d831947b9eac + 未提交 overlay`，分支 `feature/128-image-reading-and-multimodal-tool-results`，不是 clean HEAD。Windows 11 / Python 3.12.6。代码及 harness 内容锚点见本机归档 `source-overlay.sha256`；其 SHA256 为 `94348a1886bc3a24fec72676244f58b6e720d4cd63b709615715d451b6c1ec62`。该清单排除文档和构建临时 Guide overlay；Guide 单独记录于 E-004。

## 证据账本

| ID | 证据与覆盖 | 结果及边界 | 状态 |
|---|---|---|---|
| E-001 | code-supported / deterministic：后端、前端、Rust 完整检查 | 后端 3108 passed / 5 skipped；前端 1294 passed；Rust 283 passed。权限、静态图片校验、调用关联、持久化、恢复、切换路由、资源清理/预算及旧能力回归通过 | active / confirmed（对应断言） |
| E-002 | runtime-observed / human-reviewed：真实 Tauri WebView 的 UI fixture | “读取图片”文案正常；拒绝按钮命中且点击后卡片消失。未执行真实后端批准；不外推授权全链路 | active / confirmed（UI 范围） |
| E-003 | runtime-observed：DeepSeek Flash 两次原生 Responses 请求 | 首次调用真实 view_image，第二次识别蓝色；源文件删除后从快照投递。严格输出格式断言失败，视觉语义人工复核通过 | active / confirmed（单次观察）；稳定性 unconfirmed |
| E-004 | runtime-observed：package-sensitive 冻结 runtime | doctor 的 image_reading 检查 PNG/JPEG/WebP 和快照回读全部成功；其余 doctor、服务启动 smoke 通过；命令退出 0 | active / confirmed（当前冻结构建） |

### E-001 命令及行为覆盖

- 后端：`scripts/check/run.ps1 --stage backend -p no_external_http`，执行前 `PYTHONPATH` 指向本仓库 `scripts/test`。lint/format、mypy 688 文件通过；测试 3108 passed / 5 skipped。禁用外部 HTTP，不存在隐式真实模型验证。
- 前端：`scripts/check/run.ps1 --stage frontend`，静态门禁、155 个测试文件 / 1294 测试及生产构建通过。
- Rust：`scripts/check/run.ps1 --stage rust`，fmt、283 测试及 release check 通过。
- 关键回归入口：`agent/tests/test_image_reading.py`、`agent/tests/test_session_rollout.py`、`agent/tests/test_tool_factory.py`、`llm_providers/tests/test_tool_images.py`、`scripts/package/test_package.py`。OpenAI/DeepSeek native function_call_output 契约均覆盖；Chat、Kimi、缺失/损坏资产有明确降级。
- 最终全量检查取代实现中失败的初轮检查：曾发现 view_image 错入通用文件工具工厂、隐藏参数影响旧恢复调用、旧 catalog 断言以及导入循环，均在最终检查前修正。没有把初轮失败算作通过。
- 保留局限：skipped 项未验证；现存依赖警告、Vite 大 chunk 等警告仍存在，不影响命令退出码。

### E-002 运行身份与局限

入口为 `scripts/desktop-visual/run.ps1 start`，随后执行 `scripts/image-reading-visual/run.ps1`。worktree 与当前分支匹配，mode=Tauri；CDP 9222，Vite 1420/1421，bridge 18800，voice 18900。Tauri PID 17804 于 13:37:00 启动，bridge PID 34424 于 13:37:02 启动，晚于涉及源码的最后修改。数据与日志隔离在本机临时目录。

截图为真实 1300×900 WebView；目标按钮约 41.6×24，中心命中测试通过；拒绝后卡片隐藏。fixture 只在页面注入合成授权请求，不调用模型、不真实批准文件访问。早期 fixture 因新会话列表同步及运行校准清理而失败；修正验收 harness 后第 5 次观察成功，不把它们归因为产品故障。

runner 删除自己新建的会话；`scripts/dev/run.ps1 stop` 后 status 确认 lease 释放、进程退出、开发端口空闲。截图与 DOM 结果见本机归档。

### E-003 单次真实模型观察

用户明确授权最多 2 次请求，实际为 `deepseek/deepseek-flash` / Responses / 默认关闭推理配置；API 返回模型 `deepseek-flash`，没有重试。两次请求 token 分别 658 和 894，共 1552。夹具是本地随机从三种颜色选取的 256×256 纯色 PNG，实际蓝色；文件名和请求不透露所选颜色。已有 read_image 精确文件 scope，模型正确生成 view_image 参数；工具本身未调用其它模型。

第 2 次回答识别为蓝色，但包含解释和 Markdown。原脚本 `passed=false`、退出 1 的事实保留：该字段同时要求严格单词匹配。人工复核把两个结论分开：颜色识别通过；请求的单词输出格式未遵循。最后正确点是图像投递及颜色语义；失败位于输出格式断言，不能据此判断图像链路失败，也不据单例推断模型稳定能力。若复用 harness，应把视觉正确性与格式遵循分项判定；本次未修改原报告或追加请求。

该观察只覆盖预先授权后的工具调用到模型回答，不覆盖模型主动申请权限、复杂图像或 OpenAI 真实服务。完整应用编排的恢复/持久化使用 E-001 的 fake 模型集成回归验证。

### E-004 冻结构建

命令：`scripts/package/run.ps1 runtime --local-test --verification-level package-sensitive --guide-repo <本机维护 checkout> --guide-commit 0007ae65a3cc684f72d7014c75319f9e1503b6b5`。

Build ID：`20260913T054612Z-366d285b0d3f-49388ae5`；productVersion `0.3.0`；profile `production`；sourceTreeSha256 `161fed2f38734b1f511515d08a7ece86b7a889cdfaec05a7a8f0de9a02c66dd0`；dirty=true。PyInstaller 阶段约 440 秒，runtime-stage-and-smoke 约 286 秒。实际观察 `capability:image_reading.ok=true`、formats=[PNG,JPEG,WEBP]、snapshot_roundtrip=true。未生成或安装安装包。

Guide 使用已发布 immutable r42 的临时基线：commit `0007ae65a3cc684f72d7014c75319f9e1503b6b5`，snapshot `b38f9626ce410eca17f5b3350ac9698e9d3bcf5abf3f3401dee6b6b9501206bd`。runner 完成后恢复原 builtin。公开 Guide 仍需描述新增能力，本次 package-baseline 只读检查未修改或发布公开知识。

## 原始材料与完整性

以下归档仅本机可访问，不依赖临时 worktree 保留：`C:/Users/example-user/AppData/Local/agent-friend/maintenance/requirement-128-evidence/20260913/`。无凭据或生产用户数据；原报告保留必要的合成模型输出，未复制进仓库。

| 归档文件 | 原始位置 | SHA256 |
|---|---|---|
| desktop-permission.png | Temp/agent-friend-128-visual-proof-5/permission.png | 9073792bf51d6f12cfa564e0747e15983dc7832ea45e7cafafb2c4e5446ca08c |
| desktop-report.json | 同目录 report.json | d53e8d638f44d8e23a17f93f3a6e5e294a98a02232b55e2ae7049c10a94adad0 |
| real-image-report.json | Temp/agent-friend-128-real-image-smoke/report.json | 00a300f533e81b2d866a374199e59463f60d553d9f231edfa95fbac4e62f25c9 |
| real-image-smoke.py | Temp/agent-friend-128-real-image-smoke.py | 1665abf71cad60e0f3b709001535d92e390502031149ca7ebf4068cbe1a719a5 |

完整性清单为本机 `MANIFEST.sha256`。项目检查和冻结 doctor 的逐项原始输出在当前任务工具记录中；未另行保存完整原始日志，不虚构日志 artifact。冻结构建目录在本机 packaging/work 下，build identity 单独归档。

## 未决项与交付影响

用户已确认交付；OpenAI 真实调用和复杂图片质量不在已完成观察中。E-001 支持接口与兼容行为，E-002 支持权限卡片文案，E-003 支持 DeepSeek 单次真实投递，E-004 支持新增 Pillow 在冻结环境可用。没有依据这些观察扩大已确认需求/设计。

2026-09-13 对账：工程任务全部完成，AC 证据已同步到 progress；用户于本日确认交付，progress 已标记 COMPLETED。
