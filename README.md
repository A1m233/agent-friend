# agent-friend

一个陪伴式 AI 项目。长期形态是常驻桌面的 AI 朋友（桌宠），支持语音、长期记忆、用户自定义人格。

## 许可证

本项目自有代码与文档采用 **[Apache License 2.0](LICENSE)**，版权声明见 [NOTICE](NOTICE)。允许在遵守许可证的前提下使用、修改、分发和商业使用，包括闭源衍生；分发时须保留适用的许可证及声明，并按协议标明修改。该许可不授予项目商标使用权。

第三方代码、依赖、运行库、模型、图片、音频及数据集保留各自的版权与许可，**不因本仓库或包元信息标注 Apache-2.0 而改为 Apache-2.0**。主要随仓材料的许可入口如下：

当前保留 B 站集成。包含 `bilibili-api-python` 的 Python 引擎组合按适用的 GPLv3-or-later 条件准备分发，自有源码保留 Apache-2.0 声明；不能把这个组合仅按 Apache 条件再分发。具体范围、源码交付义务和未完成的整包核对见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

| 材料 | 许可与来源入口 |
| --- | --- |
| 内置第三方 Skills | [来源清单](agent/src/agent/skills/builtin_assets/manifest.json)及各 Skill 目录的许可证 |
| Vendored Tauri runtime | [Apache-2.0](frontend/src-tauri/vendor/tauri-runtime-wry/LICENSE_APACHE-2.0) / [MIT](frontend/src-tauri/vendor/tauri-runtime-wry/LICENSE_MIT) |
| 火山引擎语音 demo | [BSD-3-Clause 许可](experiments/voice-poc/rtc-aigc-demo/LICENSE) |
| Live2D Cubism Core | [专有软件许可入口](frontend/public/live2d-models/CUBISM_CORE_LICENSE.md) |
| Hiyori 样例模型 | [素材及样例模型许可入口](frontend/public/live2d-models/hiyori/LICENSE.md) |
| 明日方舟模型及测试样例 | [模型 NOTICE](frontend/public/spine-models/ark-models/NOTICE.md)、[测试样例 NOTICE](frontend/public/spine-spike-fixtures/ark-amiya/NOTICE.md) |
| Spine 运行库依赖 | 所使用版本随附的许可证及 [Spine 官方许可](https://esotericsoftware.com/spine-runtimes-license) |
| B 站连接器依赖 | 保留 `bilibili-api-python 17.4.1`：GPL-3.0-or-later；[许可全文](licenses/GPL-3.0.txt)与[组合分发说明](THIRD_PARTY_NOTICES.md)；[上游已宣布关停](https://github.com/Nemo2011/bilibili-api) |
| Live2D 封装库 | `easy-live2d 0.4.4`：MPL-2.0；与 Cubism Core、样例模型的许可分别适用 |

评测中的第三方截图、媒体和外部基准数据也不属于本项目自有素材。上述索引不是全部传递依赖的许可证清单；构建或分发完整应用时，仍须满足对应版本及使用方式的第三方授权条件。选择 Apache-2.0 不表示这些发行条件已经核验完成。

## 想法

- 以类似桌宠的形态出现在桌面上
- 能听用户说话（语音输入）
- 有记忆系统，让交互更像一个真实的人
- 不预设角色名，由用户自行命名

## 当前状态

项目处于持续开发阶段，包含桌面应用、对话引擎、长期记忆、工具执行和多入口接入。当前正在准备公开源码；第三方发行授权和 Git 历史清理尚未全部完成，源码许可证不代表完整安装包已经具备公开发行条件。

技术栈已锁定（见 [`docs/decisions/0002-incubation-tech-stack`](docs/decisions/0002-incubation-tech-stack/README.md)）：Python 3.12 + uv workspace + LiteLLM + SQLite + FastAPI(HTTP/SSE)。

当前源码包含的主要能力（具体范围见 [`docs/requirements/`](docs/requirements/)）：

- 多会话对话、流式响应、会话文件与历史检索
- 用户自定义人格、模型连接配置及推理选项
- 联网搜索、文件操作、受管理命令执行、Skills 与权限审批
- 上下文压缩、任务恢复与使用量观测
- 长期记忆、召回与更新管理
- `agent-bridge`：双协议（OpenAI / AG-UI）HTTP+SSE 服务端
- 语音通话、QQ 接入、待办、提醒与定时请求（部分能力需另配服务凭据）
- Tauri 桌面应用：聊天、设置与桌宠窗口；Live2D 使用内置 Hiyori，用户模型导入目前支持 Spine 3.8
- 记忆召回质量评测（[011](docs/requirements/011-memory-recall-eval/) 机制 + [012](docs/requirements/012-memory-eval-anchor-recall-scoring/) PerLTQA 锚点判分 + baseline 归档；详见 [`memory_eval/README.md`](memory_eval/README.md)）

## Monorepo 结构

| 包 | 职责 |
| --- | --- |
| `agent/` | 引擎核心：对话编排、会话、人格、system prompt、上下文、tools |
| `memory/` | 长期记忆（SQLite 抽取 / 召回） |
| `memory_eval/` | 记忆召回质量评测（外部 benchmark + 判分 + baseline 归档） |
| `agent_eval/` | Agent 行为契约评测与确定性验证 |
| `llm_providers/` | LLM 适配层（LiteLLM 封装） |
| `runtime_config/` | 运行时配置与系统凭据存储 |
| `agent_bridge/` | 把引擎通过 OpenAI / AG-UI 协议暴露为 HTTP+SSE 服务 |
| `voice_bridge/` | 语音通话控制平面 |
| `frontend/` | 桌面前端（Tauri 2 + React）：桌宠外壳 + 对话界面 |
| `tools/` | CLI 调试 UI 等开发工具 |

## 上手

准备 Python 3.12、uv、Node.js 22.13+、pnpm，以及桌面构建所需的 Rust 工具链和对应系统依赖。前端包管理器版本由 `frontend/package.json` 固定；安装脚本不会替你安装系统级工具。

Windows PowerShell：

```powershell
.\scripts\setup\run.ps1
.\scripts\credentials\run.ps1 set-bundle deepseek
.\scripts\dev\run.ps1
```

macOS / Linux：

```bash
./scripts/setup/run.sh
./scripts/credentials/run.sh set-bundle deepseek
./scripts/dev/run.sh
```

默认开发入口使用系统凭据存储，不创建或自动读取根目录 `.env`。上例以 DeepSeek 为例；其他模型和可选服务按[用户指南](https://github.com/A1m233/agent-friend-guide)配置。真实模型调用可能产生费用。`.env.example` 仅用于显式启用的存量兼容流程。

开发入口默认启动 Tauri 桌面端。需要浏览器调试时可显式使用 `--web`；启动后可通过 `scripts/dev/run.* status` 查看实例、`scripts/dev/run.* stop` 停止。发行安装包当前以 Windows x64 为目标，不将跨平台脚本的存在等同于所有平台均已通过验收。

自动化检查可分别运行 `scripts/check/run.* --stage backend`、`--stage frontend` 和 `--stage rust`；Windows 使用 `.ps1`，macOS / Linux 使用 `.sh`。真实模型评测需自行配置并另行执行，不属于仅凭静态检查即可证明的能力。

脚本清单见 [`scripts/README.md`](scripts/README.md)。用户数据（会话 / 人格 / 记忆）落在系统标准用户数据目录（见 [`0002 §3.19`](docs/decisions/0002-incubation-tech-stack/README.md)）。
