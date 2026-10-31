# 168 · 证据索引

## 用途与当前判断

本索引记录 AF 独立游玩中文版《宝可梦 火红》的现状、外部能力与资源候选。需求范围见 [requirement.md](./requirement.md)。

- 最新状态：用户授权追加5轮“试玩→分析→修复”，每轮最多30分钟；仅Pokemon专用tool/个人Skill可自动修复，需要修改AF通用harness时暂停等方案确认。本次5轮已全部完成并完成允许范围内的修复，见E-020；以下E-019保留此前第六轮的历史结论。

- 需求与方案已确认，个人 Skill、外置控制器和 AF 源码长跑入口已实现；累计十一轮真实 AF 个人 Skill 试玩（此前六轮、本次五轮）。已取得灰色徽章和跑步鞋，BUD14级、SKY3级、独角虫5级；最新停在尼比中心护士治疗选项，尚未确认治疗或通关。本次证据见E-020，历史流式长停顿与取消计数缺口见E-019。
- AF 有图像工具结果、会话持久化和后台运行基础，可以作为接入起点；这不是长时间游玩已经验证通过的证据。
- mGBA 0.10.5 源码能力已得到 E-006/E-007 的 Windows 基础实测补充；完整长跑恢复仍需真实 AF 验证。
- 公开 GPT Plays Pokémon FireRed 是旧版辅助框架，不是近期纯视觉成绩对应的实现；其非商业许可证限制必须保留。
- 已取得一个 SP 中文候选，验证标题/操作说明、战斗、128KB 存档文件大小及模拟器快照存读；游戏内读档和自主通关尚未验证。

## 证据账本

### E-001 · AF 接入基础

- 类型：本地代码，`code-supported`；采集日期：2026-10-31。
- 锚点：产品基线 `89162a4499ca1c6137714692f58c653fb565dd9b`；当前仅有本需求文档 overlay。
- 来源：`agent/src/agent/tools/protocol.py`、`agent/src/agent/tools/builtin/view_image/tool.py`、`agent/src/agent/runtime/models.py`、`agent/src/agent/runtime/inbox.py`、`agent/src/agent/conversation.py`、`agent_bridge/src/agent_bridge/session_requests/service.py`。
- 观察：`ToolResult.images` 可承载图像；运行支持取消、终态观察和 detached 消费方式；`SystemTriggerEvent` 与 `continue_after_external_action` 提供有来源的系统触发及正常工具循环入口。
- 局限：当前图像能力校验、工具上下文注入和 Skill 生命周期有专用分支，新增工具不能仅靠注册即假定完整接通；自然结束后的游戏续玩及跨进程恢复未实现。
- 置信度：基础接口存在 `confirmed`；游玩适用性 `directional`。状态：`active`。

### E-002 · GPT Plays Pokémon 公开旧版

- 类型：上游源码和许可证，`code-supported`；核对日期：2026-10-31。
- 锚点：`Clad3815/gpt-play-pokemon-firered` commit `9619b101d607ee79b68e1274174ac3b6eeb6ff06`。
- 来源：[源码](https://github.com/Clad3815/gpt-play-pokemon-firered/tree/9619b101d607ee79b68e1274174ac3b6eeb6ff06)、[许可证](https://github.com/Clad3815/gpt-play-pokemon-firered/blob/9619b101d607ee79b68e1274174ac3b6eeb6ff06/LICENSE)。
- 观察：Lua 模拟器控制、Python 状态桥接、Node 模型循环和网页展示分层；桥接中的对话、碰撞和战斗判断依赖游戏内存。README 指定美版 FireRed 资源兼容条件。
- 许可证：CC BY-NC 4.0，要求署名并限制商业用途。产品当前主仓库采用 Apache-2.0，不能把上游代码直接当作同许可代码并入。
- 局限：未执行上游代码；不能据其旧版实现推出新版纯视觉行为或中文版兼容性。
- 置信度：`confirmed`。状态：`active`。

### E-003 · mGBA 0.10.5 基础能力

- 类型：官方发布信息、Context7 查询与指定 tag 源码，`code-supported`；采集日期：2026-10-31。
- 来源：[发布](https://github.com/mgba-emu/mgba/releases/tag/0.10.5)、[core scripting](https://github.com/mgba-emu/mgba/blob/0.10.5/src/core/scripting.c)、[socket](https://github.com/mgba-emu/mgba/blob/0.10.5/src/script/socket.c)、[Qt scripting](https://github.com/mgba-emu/mgba/blob/0.10.5/src/platform/qt/scripting/ScriptingController.cpp)、[许可证](https://github.com/mgba-emu/mgba/blob/0.10.5/LICENSE)。
- 观察：指定 tag 提供 `setKeys`、`clearKeys`、`currentFrame`、`runFrame`、`screenshot`、`saveStateFile`、`loadStateFile` 及 TCP socket 绑定；发布页列出 Windows x64 制品；许可证为 MPL-2.0。
- 局限：Context7 部分示例来自 master，未将 master 的回调、图像对象或输入能力当成 0.10.5 承诺；尚未验证 Windows 制品启动、加载脚本、真实回调时序、取消和 UI 响应。
- 置信度：源码接口存在 `confirmed`；具体控制方案 `directional`。状态：`active`。

### E-004 · 中文火红候选来源

- 类型：候选维护者源码与发布列表，`code-supported`；采集日期：2026-10-31。
- 锚点：`Xzonn/PretFRLG` 的 `chinese` 分支，commit `a04f63173ca5fcf9c2db18ee223cf057c7f232c0`。
- 来源：[README](https://github.com/Xzonn/PretFRLG/blob/a04f63173ca5fcf9c2db18ee223cf057c7f232c0/README.md)、[发布列表](https://github.com/Xzonn/PretFRLG/releases)、[构建 workflow](https://github.com/Xzonn/PretFRLG/blob/a04f63173ca5fcf9c2db18ee223cf057c7f232c0/.github/workflows/build.yml)。
- 观察：README 声明基于卧看微尘中文显示代码和 SP 2012 汉化、更新中文译名，并说明不含完整 ROM；查询时 releases 为空，既存 workflow 针对 master，不证明中文分支已有可用发行物。
- 补充：`Wokann/Pokemon_GBA_Font_Patch` 是汉字字库基础能力线索，不能当作完整汉化成品；公开索引中的 DP 化、386 或剧情改版标签不能当作原版机制汉化。
- 局限：尚未取得完整资源、确定适用基础 ROM、核实最终分发许可、编译或验证游戏；不据 README 中原英文 ROM 的 SHA-1 推导中文产物身份。
- 置信度：候选来源存在 `confirmed`；正式可用基线 `unconfirmed`。状态：`active`。

### E-005 · 实现与确定性验证

- 2026-10-31，`observed`；本分支未提交 overlay，代码在 `tools/src/tools/pokemon/`，测试在 `tools/tests/pokemon/`。
- `scripts/pokemon/run.ps1 test`：26 passed，包括原生图片进入模型请求、system trigger 续玩、停止、持久预算、回滚分支、旧历史保留、互斥锁、过期观察和未知动作不重放。
- `scripts/check/run.ps1 --stage backend --lf` 实际运行全量（失败缓存已由专项复验清空）：4362 passed、5 skipped，退出码 0；lint、856 个源文件 mypy 通过。第一次全量曾有测试读取未指定 UTF-8 的失败，修复后通过。最后的推理档位参数及取消分类另经 26 项专项复验，不把运行中编辑当成冻结版本证据。
- Skill validator 通过；guidance audit 0 errors / warnings。个人包安装在 `agent.paths.skills_dir()/pokemon-firered`，未改 builtin manifest 或安装包。
- 限制：没有正式桌面端发起长跑验收。长跑使用完整源码环境的实验入口；桌面个人 Skill 使用现有命令/看图工具。没有真实模型请求。

### E-006 · 真实 mGBA 接口

- mGBA 0.10.5 Windows x64 官方制品，原创 64KiB 测试 ROM，`observed`。
- `verify-emulator` 通过：按 A 改变可见像素、松开恢复、240×160 PNG、快照存读，以及 600 帧长按中途取消后松键。
- 一轮样本：observe 约 38–65ms，4 帧 act 约 114–116ms，checkpoint 约 64ms，restore 约 62ms。只计宿主/模拟器时长，不含模型和进程冷启动，不能外推通关速度。
- 证据：受管 `runtime-regression/.../pokemon/instances/d51324e32a60446d8769bcb4b3f3aca3/emulator-check.json`；测试 ROM SHA-256 `ae133c3bdb2cdbdcaf8f3c3744ceaaa0dbabc73288ef4a656f41e4d15f165823`。
- 官方 Windows 版没有脚本 CLI 参数，实际通过 Tools → Scripting 载入 Lua；`launch` 明确返回操作说明，不宣称自动启动。控制接通后无需 UI 点击按键。
- 外置模拟器缓存对象 `e868a72afbf61459ab035230` 已 pin，避免被构建清理回收。未构建安装包。

### E-007 · 中文候选与启动

- 原始线索：[SP 发布帖](https://www.pokeuniv.com/bbs/forum.php?action=printable&mod=viewthread&tid=3373)、[7 月 23 日更新帖](https://pmcenter.cn/forum.php?mod=viewthread&tid=45552)。原文提醒汉化版本之间不能混用存档，真实保存容量为 128KB；第二地址访问 403，搜索缓存可见更新说明。
- 下载：[NES Ninja 对应条目](https://nesninja.com/game/gba/pokemon-fire-red-version?rom=Pokemon+-+Fire+Red+%28J%29+%28V1.0%29+%5BT%2BChS20120723-3_Pocketstars+SP%5D.gba)。首次直连 403，附带公开来源页 Referer 的普通下载成功；未登录或绕过验证。
- 大小 16,777,216 字节；SHA-256 `d705980b7b185ef000c83b20ca7633ea13b748db1600e3439f00450b10b7e7e0`；CRC32 `3589923d`；头部 `POKEMON FIRE` / `BPRE`，头部校验正确。镜像名字标记 J，但实际头部为美版代码，不以文件名认定版本。
- 已看到中文标题和控制说明；`game.sav` 为 131,072 字节；真实快照保存/恢复成功。文件大小不证明游戏内保存或名人堂后读档正确。
- 私有证据：`agent.paths.user_data_dir()/pokemon/instances/b7b5c43c972b4d7795b3c8d29fe20c3e/compatibility-smoke.json`。这是 coding agent 的准备/兼容性 smoke，不是 AF 自主游玩；未把路线/决策写入 Skill。
- ROM 只在本机用户数据目录，未进入仓库/Skill/安装包。镜像授权及逐字节原始来源尚未核实，不作为公开分发资产。

### E-010 · 历史图片淘汰与实际请求前缀

- `confirmed`，2026-10-31。仅分析 E-009 修正段既有证据，没有新增模型调用。私有报告 `image-prefix-analysis-20261031.json` 位于同一游戏实例目录。
- 报告 SHA-256：`461e08a4122b4e673e272dcc9dc8c122a51226a1ada63afeb3767b70393f9b3f`。
- 将实际 `rollout_commit.final_wire_witness` 的逐项 SHA-256 与 latency 中 request fingerprint 对齐，逐个检查 49 次 rebase 的首个不同项。按旧图片淘汰投影重建 function_call_output，使用相同 canonical JSON 和 `agent-friend:provider-transcript-item:v2` digest 域计算，49/49 精确匹配。
- 结论限于本段首个前缀差异确由旧图片替换占位文字产生；不是缓存费用、全部请求变化或提速幅度的证明。仅比较 prepared canonical history 会漏掉投影变化，不能代替实际 wire witness。
- 新实现将单次工具预算与历史预算分开：单次至多 8 图/32MiB，历史高水位 10 图/32MiB，达到预算后集中降至 4 图/16MiB（保留最新完整结果组）。确定性测试证明一段内追加不改写旧前缀；超限仍会重组，不宣称永久缓存命中。

### E-011 · 动作带图、稳定采样与有界对白

- `implemented / deterministic-verified / runtime-observed`，2026-10-31；base 仍为 `89162a4499ca1c6137714692f58c653fb565dd9b` 加本分支未提交 overlay。未执行新真实模型试玩或真实 C27-06 eval，性能收益和模型工具选择仍待验证。
- 通用命令显式 `output_images` 复用当前轮 read_image scope，仅用于 buffered；成功 JSON 输出声明相对 image_paths。执行后图片失败明确保留 executed=true，不重放动作。fake exec-server 测试覆盖从命令完成到图片快照的实际工具链；C27-06 固定失败后查看已有图片而非重复执行的行为契约，C27-04 保持相邻 control。
- 游戏 harness 只使用截图，不读取 RAM。act 有界等待稳定画面并返回近期尝试；dialogue 逐次轻按 B、释放、截图，遇额外窗口、未知布局、重复页或预算即交还 AF。单次最多 7 次推进、8 图、600 帧、20 秒，受全局试玩截止时间约束。
- B 的选择依据为 [FireRed 文本实现](https://github.com/pret/pokefirered/blob/master/src/text.c) 中 A/B 均可结束文本等待，以及 [场景交互实现](https://github.com/pret/pokefirered/blob/master/src/field_control_avatar.c) 中 A 触发人物交互。原版源码不能证明中文候选所有场景一致，因而遇选择和未知布局停止，不能无条件连按。
- 真实无 LLM smoke：`scripts/pokemon/run.ps1 verify-visual`，私有报告 `visual-smoke-32deaad6a62d4939a4f724f3f285cbc9.json`，SHA-256 `0183b8d9862b7cb53325553390eb3ae6e5893c375c9185d042ed473783fe2a6e`。2 次 B、208 帧、3 张观察，取得博士对白和获得妙蛙种子的页面，重复页停止；finally 恢复快照、paused=true，前后像素完全相同。该样本早于最终相邻重复页去重与超时返回部分结果的细化，后两者另有确定性测试，不能倒改为同一冻结版本运行证据。
- 全量后端 4379 passed、5 skipped，2 项评测数量断言失败；修正数量后，全部 agent_eval、命令/图片与 Pokémon 专项合计 336 passed，lint 与 mypy 865 文件通过。Skill validator/audit 通过。未修改前端/Rust、未运行其检查，没有构建安装包。
- 个人包已通过旧清单校验、备份和保留实例绑定的 update 更新；最后备份 `1b732ec0fe784d3c99d1a619b5c9b393`。依赖使用既有受管 Python/Pillow，没有安装 OCR 引擎。当前固定白色对白框启发式只用于保守停止和分页；不证明完整菜单识别，OCR 仍为后续可选方向。
- AF 原始图片仍本地保留；本次无 computer-use，无新模型请求，无游戏进度贡献或通关声明。游戏保持第二轮结束点暂停。
- 最终边界复验：`scripts/check/run.ps1 --stage backend agent/tests/test_command_image_output.py llm_providers/tests/test_multimodal_adapter.py`，8 passed，lint 和 mypy 865 文件通过；证明完整工具 schema 拒绝 streaming 带图，以及 8 图转换为实际 Responses input_image、9 图拒绝。测试使用 fake 资产与显式模型能力，不访问 provider；首个新增测试因 fixture 缺少规范化模型能力而失败，修正 fixture 后通过。
- 最新 Tauri dev：owner PID 41864、desktop PID 4784、bridge PID 26248；同 worktree/feature 分支，bridge 于北京时间 12:10:45 启动，晚于最后生产源码修改 12:08:07。healthz=ok、runs=[]。doctor 确认 personal_external/builtin=false/instance_bound=true；已安装 actions.py 与 SKILL.md 分别与源码 SHA-256 完全一致。保留 dev 供用户查看，停止入口为 `scripts/dev/run.ps1 stop`。

### E-012 · 动作带图优化后 30 分钟复测

- `runtime-observed`，2026-10-31。用户单独确认“开始，最多 30 分钟”；沿用原 session、模型 gpt-6.1-sol / effort:medium、游戏现场和 session_auto，没有改审批模式。通过桌面同一 `/ag-ui/run?enqueue=true` 接口发起，没有使用源码 player、computer-use 或由 Coding Agent 选择游戏动作。
- 历史 provenance：同 worktree/feature 分支、原 base 加 overlay；代码与个人包冻结在 `visual-pilot-code-20261031.json`，结束后与冻结 hash 对账无漂移，随后才更新报告。dev owner 41864 / desktop 4784 / bridge 26248，runtime `7c7712c20a5846eab3f6f74a1e855b81`，源码早于启动。保持默认工具循环配置，外部 watcher 布防 1770 秒，另留 30 秒余量；脚本共享绝对 deadline。
- run `8f1263d1-e7b3-4e04-8327-135180c7d7c5`，北京时间 12:44:40.793 至 13:13:25.400，1724.607 秒（28 分 44.607 秒）。观察器整个窗口 1736.828 秒。正常 end_turn；AF 在 finish 返回 51 秒余量时保存暂停，未触发强制取消。
- 69 次主对话请求，另 1 次 context_compaction 摘要请求；用 run hash 对齐 provider call observation，合计 70 次实际 HTTP 请求、无重试。68 次工具调用且 0 错误：67 exec_command、1 request_filesystem_access；命令为 status 1、begin 1、act 58、dialogue 6、finish 1。没有 view_image。
- 66 个工具结果直接包含图片，共 76 张，最多单次 4 张。6 批 dialogue 合计推进 12 次、返回 16 张图；停止原因 repeated_page 2、choice_or_overlay 2、unstable 1、dialogue_closed_or_unknown 1。存在普通研究所对白被保守判为额外窗口的样本，AF 改用 A 继续；不是所有 6 批都完整走完对白，不宣称菜单识别全覆盖。
- 游戏进度：昵称 BUD；与 BOB 首战失败并接受结果，不回档；离开研究所进入北上道路；击败 2 级小拉达，成功逃离波波。最后可见战斗状态为 5 级、16/19 HP；最终保存画面在道路草丛旁，未进入下一座城。小拉达战败图 `8d72723a6cca4013a82ecc76a10bd7ec.png`、最终图 `762d34a69cd74ee2a8d5d7c54c81a4f1.png` 已人工看图核实。
- 保存 checkpoint `762d34a69cd74ee2a8d5d7c54c81a4f1`，SHA-256 `1a113b9c917a8bcf6d961b21df74c951dc258138ea5e4f4eb8261de6d8583a40` 与实际文件匹配。pending=null、paused 标记存在、watcher 正常退出、budget 文件清除，无后台模型续玩。仍只有模拟器快照，未验证 AF 上下文配对恢复。
- 主模型生成合计 902.179 秒，中位 12.376 秒；工具耗时合计 721.769 秒，其中命令 launch 184.173 秒、execution 520.839 秒（嵌套指标，不能全部相加）。58 次 act 中 settled=false 30 次、true 28 次；战斗/道路上的动画、识别范围和模型选择的长 WAIT 均可能影响等待，本轮不把 false 统一归因成识别器错误。
- 以两轮相同的 `tool_batch_start` 边界重算，连续动作批次间隔中位数从上一修正段 26.886 秒降为本轮 24.625 秒（本轮包含 act/dialogue）。旧 E-009 的 27.055 秒来自游戏动作 started_at，口径不同，保留旧值而不混用。两轮游戏场景、历史长度和压缩不同，不是受控 A/B；不能宣称稳定提速比例或据此外推通关时间。
- 主请求 prefix：strict_extension 58、rebase 11；前段为 53/49（共 102 请求）。主请求累计 input 6,050,552、cached 5,505,920、output 14,714，输入 token 加权缓存比例 91.00%，前段 82.77%。计入摘要后总 input 6,149,267、output 16,853，cached 不变；这些为累计请求用量，不是唯一上下文或账单。
- 自动摘要 1 次，用时 68.666 秒，input 98,715、output 2,139、cached=0；本轮整段时间包含它。沿用历史触发压缩后继续使用相同 Skill、权限和观察编号成功，但一次连续压缩成功不等于进程重启恢复验收。
- 私有材料仍位于 E-008 同实例：`visual-pilot-20261031.json`、request、code、events、latency、model-observations、summary 同名前缀文件。events 1459 条，journal_truncated=false；`visual-pilot-evidence-manifest-20261031.json` 对 1544 个证据文件及新操作截图/存档生成 hash。完整输出、图片与 ROM 均未进入 Git。
- 判断：直接带图及一次多图对白在真实 AF 中 `confirmed`，本轮无需额外看图往返、0 工具错误；前缀稳定性与缓存比例改善为观测事实。实际动作速度仅小幅改善，地图辨认、长等待和对白识别仍有优化空间。下轮新真实运行需单独授权，本次没有追加运行或修改候选代码。

### E-013 · 就绪判断、对白覆盖、移动反馈与 OCR 样本验证

- 用户明确授权按等待、对白/OCR、移动反馈的顺序实施，并在修改后另跑最长 30 分钟的个人 Skill。只修改外置 Pokémon harness、Skill、操作脚本和专项测试，没有新增产品依赖、安装包或后台 OCR 服务。
- 就绪判断按文字/战斗菜单区域和地图粗色块分别处理；连续两次 12 帧采样满足条件提前返回。新增无按键 wait，总 600 帧/20 秒边界不变，显式 WAIT 帧数不偷偷缩短。含方向键的 act 返回已有前图和当前后图，提供粗略变化/低歧义滚屏估计，绝不当作玩家坐标、碰撞或可通行证据。内部探索色块不进入模型上下文。
- 离线 `readiness-replay-v2-20261031.json` 使用上一轮 58 批 act 的已留存逐样本截图：旧版 ready 28 批，候选在已有采样序列内 ready 55 批，可提前结束的采样共少 4368 帧。不是实时模拟器 A/B，也不证明所有提前返回都满足游戏语义；证明菜单动画/局部背景动画不再必然耗尽等待。
- `ocr-check` 使用 [rapidocr-onnxruntime 1.4.4](https://pypi.org/project/rapidocr-onnxruntime/1.4.4/) 的独立 uv 环境，CPU、2 intra/1 inter 线程、禁用方向分类；只读本地截图，不上传、不调用 LLM。6 张便利样本覆盖普通对白、教学、招式、战斗结果及无文本地图，分别原尺寸与 nearest 3 倍放大，未作代表性准确率实验。
- 私有 `ocr-samples-20261031.json`、`ocr-check-20261031.json` 保留样本、裁剪、预期、原始识别文本、置信度、依赖版本、模型 hash。初始化 0.328 秒，12 次识别中位 1.097 秒。能识别不少中文，但 ACE/BOB、0/O、招式字形有错误；3 倍放大没有一致改善。人工初始转录中两项在复查源图后纠正，`ocr-evaluation-20261031.json` 显式保留纠正及原报告 hash，不能把标注错误计作 OCR 错误。
- 决定：这版 OCR 不进入游戏控制或个人包；原图仍由 AF 阅读。后续可扩大样本验证辅助转写，但本轮不把字词置信度作为自动按键许可。
- 真模拟器无 LLM smoke：`visual-smoke-a7530450f42f41dfaa88fec34cc4b16f.json`，SHA-256 `d138694050217eea3aab55b713201ece690e9d76af7d15a7e3f590ef111115bd`；对白在地图停止、向右 8 帧返回前后图、wait 在就绪停止。finally 恢复模拟器快照和探索记录，paused=true，前后 PNG 像素相同。较早 smoke 发现探索记录未随快照恢复，已修复并从上一轮精确工具结果恢复记录后复验，未将测试动作留给 AF。
- 首次候选检查 48 passed、lint、mypy 866 文件通过，Skill validator/audit 通过。个人包备份 `2156161319b24a769e6c7d2599108168` 后更新；安装内容逐文件与源码一致。部署仅更新个人包，当前 dev 的 bridge/通用工具代码未改变，无需以旧进程证明新加载模块；每轮 Skill 读取取得新的包快照。

### E-014 · 就绪与移动优化后的第四轮实测及窄选项修复

- `runtime-observed`，2026-10-31。沿用原 session、gpt-6.1-sol / effort:medium、session_auto、mGBA 实例和 E-012 保存点。通过真实桌面同一 `/ag-ui/run?enqueue=true` 接口，未使用源码 player 或 computer-use。没有模型代玩、攻略/RAM、倍速、常驻优化或更换推理档位。
- run `51235ac6-ecbb-4524-8875-1897d86f8984`，北京时间 13:35:11.954 至 14:03:36.454，1704.500 秒（28 分 24.500 秒）；观察器完整窗口 1719.188 秒。正常 end_turn，finish 返回剩余 65 秒；paused=true、pending=null、budget 已清除，watcher 退出，没有后台续玩。
- 冻结候选 `readiness-pilot-code-20261031.json` 对应原 base + overlay 与个人包；结束后、修复前核对所有文件无漂移。Tauri/dev/runtime 身份沿用 E-012，修改范围在每次命令新加载的个人包。随后修复窄选项的版本是 post-pilot candidate，不能冒充本轮运行版本。
- 87 主对话请求 + 1 摘要，合计 88 HTTP 请求，无重试。86 工具结果：skill_read 1、request_filesystem_access 1、exec_command 84；status/begin/finish 各 1、act 61、wait 9、dialogue 11。协议层 0 工具错误，**不等于行为零失败**，治疗选项漏识别见下条。
- 明确失败样本：护士的窄是/否窗口截图 `fff384b46ee946bb929655f067aebaf2.png` 被候选误认普通对白，自动 B 取消了第一次治疗。根因是为剔除书架增加的宽度门槛也排除了窄选择框，属于 harness 图像分类到自动按键的错误。AF 从原图发现取消，重新用 A 确认治疗并打开队伍核实 19/19 HP，未回档。一次恢复成功不抹去该失败。
- 结束后已修复门槛，保留高度/面积/填充率约束以排除书架；新增窄是/否工具停止测试。`narrow-choice-recheck-20261031.json` 对同一真实失败图、书架对白、战斗菜单、战斗文字的分类正确；49 passed、lint、mypy 866 文件通过。修复后个人包备份 `43e569df90894b819fdd0e5194baf48e` 并更新；没有再启动真实模型复验，不宣称全部选择窗口已覆盖。
- 游戏进度：击败 3 级小拉达，获 24 经验；进入城镇并在中心治疗；拿到大木的包裹，返程遇敌后逃跑；最终位于真新镇北入口，包裹未交付。领取图 `c6c077eb4b3546a59e3dc2e403442fe5.png` 与保存图 `8fc21c7e9c1b481e85d87113dad944dc.png` 已看图核实。中途命令 expect 曾把民居称为商店，进度以实际截图和领取对白为准，不把预期当成完成事实。
- checkpoint `8fc21c7e9c1b481e85d87113dad944dc`，实际文件 SHA-256 `f3a0a1ced8731ac83cb13b59f0900af5d3f14794c292ad45f1b56f1034087418` 匹配。仅为模拟器快照，不包含已验证的 AF 上下文配对恢复。
- 83 个结果带图，共 152 张，最多单次 6 张；没有 view_image。11 批 dialogue 合计 24 次推进、31 图，停止 repeated_page 4、choice_or_overlay 1、dialogue_closed_or_unknown 5、frame_budget 1。商店一次返回 6 图，AF 读出了领取/送件任务；同时包含上述漏选项样本，不能只报成功数量。
- act 就绪 60/61，上轮 28/58；act 工具耗时中位 6.000 秒。act 额外采样等待 3348 帧，上轮 6660 帧；显式松键/WAIT 合计 440 帧，上轮 11284 帧。新增 wait 9 次合计 408 帧，部分攻击后仍有额外等待往返，不能只看单次 act 变短。
- 相同 tool_batch_start 边界、只取 act/dialogue 的连续批次间隔中位 **19.171 秒**，上轮 **24.625 秒**。若把本轮新增 wait 也计作批次则为 18.637 秒，后者不用于直接提速比较。两轮起点、场景和历史不同，仍非受控 A/B。
- 主模型生成合计 1060.880 秒，中位 11.880 秒；工具耗时合计 532.280 秒，命令 launch 229.095 秒、execution 294.497 秒为嵌套分项。摘要 1 次 77.752 秒，input 115443/output 2409，无缓存；全部计入总运行窗口。
- 主请求 input 7,904,517/cached 7,275,008/output 15,594，加权缓存比例 92.04%；包括摘要的总 input 8,019,960/output 18,003。prefix strict_extension 67/rebase 20。前后图增加了图片数量，重组次数没有继续下降；本轮调用更多，不宣称更省费用。
- 收尾 runtime 快照显示同一 dev 在本轮末段有其它会话活动；未读取其正文或干预，该会话不进入本轮统计。共享机器/provider 并发对耗时的影响未量化，进一步限制因果提速结论。
- 私有原件：`readiness-pilot-20261031.json` 及 request/code/events/latency/model-observations/summary 同前缀文件；事件 991 条、truncated=false。`readiness-pilot-evidence-manifest-20261031.json` 保存 1082 个原始证据/截图/存档 hash。完整对话、截图、ROM 没有进入 Git。需求仍 IN_PROGRESS，未通关，未提交/合并或打包。

### E-015 · 决策边界、条件双图与压缩估算诊断

- 用户授权按顺序修改战斗等待、对白边界和双图；第四项只查原因，不直接改阈值。新候选在个人包实现，不改安装包；继续使用既有真实截图，不增加 OCR 依赖。
- 战斗稳定文字不再直接当成决策点，等待过程中不按键；不同稳定文字页与最终边界一起返回。菜单、选择框与确认箭头交还 AF，保留帧/时间/图片上限。普通对白采样时优先拦截额外框；小型深色四边框增加保守保护。
- 双图按需：正常画面变化只回末图，变化小/近期重复或显式 `--compare` 才附前图；所有本地原件保留。新增确定性用例覆盖连续稳定战斗文字后菜单、确认/永不结束文字、不按键、窄框及单图/显式双图。
- `decision-offline-analysis-20261031.json` 保留源码 SHA、4 张既有真实截图校验及无网络反事实。护士窄选择框、书架普通对白、战斗文字、战斗菜单均符合预期；真实无 LLM smoke `visual-smoke-d09448f2183c49d0a7b49af0526d636d.json` 已恢复测试前快照及探索记录并暂停。
- 压缩问题的最早边界位于 Context 候选估算：`Conversation._estimate_context_input` 对已有 usage anchor 先使用不含像素的 `Message.to_openai()`，随后对所有保留图片另加每图 16384 的预算；旧图片仍含在 anchor 总量中，文本形态改变也损失组件复用。摘要入口只比较总估算与阈值，未区分这类上界。
- 同一份合成候选、同一已测量 anchor=146393、10 张历史图片：完整渲染估算 146393，未渲染分支 310303，其中固定图片预留 163840。`code-supported + reproduction-supported / confirmed`：重复预留机制成立。上轮真实记录为321369触发320000，上一请求真实146393；绑定源码未变，机制为 `strongly supported` 解释，但未完整重建触发候选，不能把全部差额都归为图片或声称得到了其真实 token。
- 最小修复方向是统一候选与发送路径的内容身份/计量，只为未测量图片保守预留，并保留无上传的候选选择；需另行分析通用附件、provider transport、历史删图与未知 reasoning 的上下游契约。本次没有改此核心逻辑或阈值。用户新选 Astra high / 800000 窗口不构成该缺陷已修复。
- 准备测试中一次 fake responder 因 Windows 原子发布短暂 PermissionError 提前退出；已在原2秒 deadline 内重试该读取，未放宽产品超时。失败与随后验证分开记录。

### E-016 · Astra high 第五轮个人 Skill 真实试玩

- 用户明确授权改后继续原存档、最多30分钟，并切换 Astra high；实际会话与 provider 请求均为 `gpt-6-astra / effort:high`，目录窗口800000。没有新建外部玩家、改安装包或使用 computer-use。
- run `cb90fb51-a168-4122-9fee-1842b042545a`，2026-10-31 14:57:41.704–15:26:28.542（Asia/Shanghai），1726.837秒。89次主模型请求、0摘要、0重试，88次工具：skill_read1、目录图片授权1、status1、begin1、act67、dialogue14、wait2、finish1。0协议错误不等于0识别问题。
- 进度由截图核对：交付大木包裹、获得图鉴及5球；第二球成功收服2级雌性波波并命名SKY；低血量遇敌后逃跑，返回常磐市治疗，两只伙伴核验BUD Lv5 19/19、SKY Lv2 13/13；最终停在市北老人南边，尚未交谈、没有通关。
- 战斗两次攻击在同次结果中带回结算文字并停到下一行动菜单；护士窄“是/否”框和收服后昵称选择框都被拦截，由AF确认。47次含方向操作只附前图1次；85个带图结果共137图、127个不同图像hash，单次最多6图。14次dialogue共37次B，停止原因为unstable8、重复页1、关闭/未知3、帧上限1、选择1。
- 同口径act/dialogue批次起始间隔中位18.4275秒（第四轮19.171）；含wait为18.210秒，不混用。主模型耗时973.598秒、中位10.350；工具总705.450秒，命令启动258.321、执行438.629秒，存在嵌套不可相加为互斥分解。不同模型、窗口、剧情和战斗数量，不是A/B，不能宣称稳定提速幅度。
- 累计input15,543,083，cached14,802,048（95.232%），output16,323，末次输入230,970。71次严格前缀扩展、17次重组、1次无可比基线。800000窗口下本轮没有摘要，不代表E-015的估算问题已解决；输入累加明显大于上轮，未做价格/成本结论。
- 已保留两类识别失败：① 红色等待箭头的灰/暗阴影未掩蔽、首行箭头不在旧确认检测范围，导致普通对白和战斗确认耗尽等待；② 捕捉失败动画的白色背景被当成额外窗口，AF多调用一次wait。AF均看图恢复，无误选治疗。两者在本轮结束后才修复，不能把后补版本写成已跑过的候选。
- 运行身份为既有Tauri dev `7c7712c20a5846eab3f6f74a1e855b81`，当前feature worktree、base89162a4+overlay；候选63个源码/个人包文件由 `decision-pilot-code-20261031.json` 冻结，结束前确认无漂移。已实际读取personal Skill版本及package版本。
- 日志出现独立观测问题：watchdog长期开启agent_bridge.log，在10MiB轮转时出现Windows WinError32，calls停在46；watchdog退出释放后轮转恢复。通过tauri.log.dev及轮转镜像恢复并去重，对齐最终89请求和88工具批次，未把46误报为全轮调用数。截止与暂停基于API/时间，未依赖这个计数。根因为 `code/runtime-supported, strongly supported`；该日志观察器未在本次顺手改造。
- AF在余62秒时finish，快照 `45297900efc5464098e1198e09646961`，SHA256 `f4b058095d1ad369f2e3ce0d5255ad468baa51737d0d9f4c247e255af2d4b7b6`。最终正常end_turn，paused存在、pending=null、budget移除、watchdog退出，无后台续玩；保留用户dev运行态。仍只证明模拟器快照，非AF上下文配对恢复。
- 私有材料均在同实例：`decision-pilot-{request,code,events,summary,end-check,evidence-manifest}-20261031.json`、`decision-pilot-20261031.json`、`decision-pilot-{latency,model-observations,log-prefix}-20261031.log`，以及arrow/flash-observation、操作回执、原图和存档。977个事件未截断；原始私有内容不入Git。

### E-017 · 第五轮后识别收口（未追加付费运行）

- 对E-016暴露的识别边界作局部修正：按当前皮肤9/7/5/3/1像素三角形和颜色识别箭头，包含灰/暗阴影并支持两行；不再把所有红字都掩去。战斗额外白区必须有四边框证据，避免白色闪光误停；普通对白的保守白框保护保留。
- `decision-post-pilot-recheck-20261031.json` 记录真实旧帧与源码SHA：同一“出发吧”两帧归一后相同、首行战斗/收服确认识别成功、白闪回到battle_text、护士仍choice_or_overlay、书架仍普通对白。新增箭头移动/阴影/真实字变化与白闪测试；57 passed，lint全绿，mypy866文件通过。
- 更新个人包且保留绑定与备份；没有改变最终游戏存档或再次付费试玩。此版本只有确定性及真实已录帧验证，不声称完整真实运行已验收。通用上下文估算与阈值仍未改。

### E-018 · 上下文/回包/观察器修复与第六轮准备

- 用户授权修复后再从个人Skill最多试玩30分钟。沿用Astra high、800000窗口及原存档，不扩大为后台长跑；本次没有额外模型玩家或真实LLM调试调用。
- 日志只读句柄支持Windows共享删除，轮转时按文件身份续读，保留未完成UTF-8行；在句柄仍打开时执行轮转的本机测试通过。最终调用数按call_index去重对齐API，重试仍保留原始HTTP记录。
- 预算估算使用明确的本地图像渲染器，与发送前的工具图片transport投影一致；复用/新增图片、恢复anchor、Chat及Responses路径、资产丢失/路由不支持由确定性用例覆盖。自定义renderer不默认拥有纯本地权限。没有改阈值。
- 成功回包精简仅为模型视图，正文、stderr、业务语义/进程状态、恢复标识、截断及失败信息保留；完整事件仍可recover。旧图片淘汰提示保留，避免投影后失去“图片当前不可见”的事实。
- `context-pilot-offline-projection-20261031.json` 对第五轮原始回包作字符级回放；这是文本体积证据，不是token费用或真实提速实验。命令新增read_access/start_rpc计时，等待本轮实际数据解释启动开销。
- 实现参考：Codex官方[history.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/context_manager/history.rs)（2026-10-31读取，使用已测量用量边界及增量估算的原则，非复制其协议）；Windows共享删除按[CreateFileW官方定义](https://github.com/MicrosoftDocs/sdk-api/blob/docs/sdk-api-src/content/fileapi/nf-fileapi-createfilew.md)。本项目保留既有组件级usage anchor、恢复和权限契约。
- 打包影响为standard：静态Python逻辑与测试，没有运行时依赖、资源收集或安装包变更；本次不构建安装包。真实无LLM模拟器smoke `visual-smoke-d91a4f706e9d43738eeeff6784493b28.json` 已恢复游戏与探索历史并暂停。
- 验证：后端全量4417 passed、5 skipped（第三方异步清理及zip重复样本警告保留）；末尾补充的删图提示兼容与观察器重试计数分别经43项、2项专项复验通过。lint及mypy871文件通过。受管Python及office/pdf本地smoke通过，无LLM。回包回放244059→171761字符，约减少29.6%，不外推费用。

- 中文版：取得可核实来源的补丁与匹配基础资源，完成校验及开场、对话、战斗、保存/读取验证后再锁定正式基线。
- 控制：基础操作已实测；继续验证进程中断、未完成恢复事务及长跑中的完整恢复链路。
- AF：验证游戏图像在实际 provider 请求中可见，续玩来源、Skill 和权限边界能贯穿上下文压缩与进程重启。
### E-019 · 上下文修订后第六轮个人 Skill 实测

- 类型：`runtime-observed`，2026-10-31；补充E-017/E-018，保留所有既有失败。用户明确授权“修完就去继续按半小时跑”，本次授权已使用，不覆盖下一次新付费运行。
- 身份：原会话`1fd96658-3377-4c16-aa63-1b7c3be3335e`，run `1bc32a03-a0ad-4fa1-b0e8-17e42de2534f`；Astra/high、窗口800000、原中文ROM及存档。当前feature worktree，Tauri runtime `0c76b8711cac448da8f8303730cd1dcf`，bridge PID20064在15:59:04启动，晚于核心改动。`context-pilot-code-20261031.json`冻结71份源码/个人包文件，结束核对无漂移；基线仍是E-018记录的未提交overlay，不冒充clean commit。
- 16:06:33.085至16:35:49.637（北京时间），1756.551秒。watchdog布防1770秒，按总时限取消成功；不是AF主动finish。未使用源码player、RAM、攻略、CUA，也未代选游戏动作。
- 进度：完成老人捕捉教学并获教学电视机；击败野生小拉达，BUD升到Lv6；进入常磐森林并与捕虫少年开战。BUD遭要害攻击降至4/21HP；AF自行查背包、恢复历史图像并换上SKY。末图SKY为Lv2、6/13HP、中毒，敌方独角虫Lv6；战斗未结束，未回档。
- 67次模型请求：66次完成、1次由总预算取消，0摘要、0重试；66次工具调用、0协议错误。53 act、5 dialogue、3 wait、status/begin/skill_read/目录图片授权/历史结果恢复各1。没有AF finish，外部收尾不混入自主工具计数。
- 实际63个带图结果、120张图片、113个独立hash，单次最多8张。5批对白推进19次B，停止于image_budget/battle_menu/battle_confirmation各1、dialogue_closed_or_unknown两次，没有旧的对白unstable。普通战斗返回10次battle_menu、5次battle_confirmation；最后一次含中毒的长战斗停在592帧、unstable，未擅自提高600帧阈值或重放。34次方向操作未附前图；这不证明双图策略对所有阻挡场景都已足够。
- 正常完成请求中位10.323秒；act/dialogue批次间隔中位17.954秒（第五轮18.4275）。不同游戏片段，且最后长停顿不进入后续动作间隔，不能据此宣称整体提速。工具总耗时499.673秒；模型总耗时1212.140秒，其中完成请求763.444秒、被取消请求448.696秒。计时字段存在包含关系，不将各层耗时直接相加。
- 启动分段63份样本：read_access合计167.721秒、中位2.671秒；start_rpc合计23.593秒、中位0.359秒。结合`execution_environment/windows_read_access.py`可确认权限准备是主要可控启动成本：当前每次对受管环境根同步执行继承RX的icacls授权。本次只增加分段观测，没有跳过授权、加权限缓存或守护进程。
- 上下文：67次估算均fallback=none；稳定续接复用已测组件，只对新增图预留，未出现旧的每轮固定叠加10张图预算。第一次更换投影后为usage_anchor_partial、估算465431，实发191112；后续仍有新增图片保守估算和分段删图重组，不能宣称估算精确。本轮没有触发摘要，也不能代替压缩后恢复的验收。
- 66次已完成请求合计input14855948、cached14145408、output11506，输入加权缓存95.217%；末次已完成input257404。首个实发input191112，相较前轮末次230970下降，但不是固定输入费用A/B。完成请求strict_extension50/rebase16；被取消请求的usage缺失，累计量不包括其可能产生的计费。
- 响应停顿：call35耗53.249秒，stream_open48.219秒，缓存99.53%、无重试。call67在4.802秒打开流、7.916秒出现tool_call_delta，此后未产出完整工具调用，448.696秒后由watchdog取消；清理日志status=closed。该调用配置response/idle各300秒，但没有因这些配置提前退出。已确认停在模型响应处理边界；是否有持续低层chunk、provider连接行为或本地超时缺口，现有日志不能区分，不归因为模型能力或截图工具。
- 观察器记录67条互不重复请求，log_observation_errors为空，按时取消且退出；本轮日志未达到轮转阈值，轮转安全由Windows共享句柄专项验证，不把它写成这次自然轮转实测。取消分支watchdog未写observed_model_calls/calls_match_final，最终run API的model_calls/total_tool_calls均为0；`conversation_done`明确67/66，事件流含66对请求/结果，与日志一致。这个取消统计缺口保留，不以0或缺字段替代真实数量。
- 16:36:09由Coding Agent调用个人脚本finish，仅执行零帧checkpoint并保持暂停；未续玩、未新增LLM。存档`0ce18e4e52374d2cb15deb3055ad1733.ss0`，SHA-256 `b4173107e889e299f42408a3b7c169a618da77aeac236643f740a021d0f5e71b`。pending=null、paused=true、desktop-budget已移除，watchdog退出。快照仅覆盖模拟器，不等于AF上下文配对恢复。
- 私有证据仍在原实例根：`context-pilot-{code,request,events,snapshot,summary,end-check,evidence-manifest}-20261031.json`、watchdog `context-pilot-20261031.json`、latency/model-observations/context-estimates日志、supervisor-finish回执及对应operations/receipts/screens/saves。原始对话/图片/ROM不入Git。完整性清单覆盖2250份文件，同时保存一次性分析脚本，461个事件完整无截断；清单SHA-256 `066059c91a30fbddeca993c17cb3c01b145ac12f26d3fa38a8dcde396796748e`。
- 结论：本次授权修复及有界复测已执行；边界与估算机制有正向证据，但长响应停顿消耗约四分之一本轮时间，完整自主通关仍未实现。下一步优先核查流式无完整输出时的超时语义及取消计数，再设计不削弱ACL有效性的启动优化；未自动实施新修复或另开付费运行。

### E-020 · 五轮有界循环与专用工具修订

- 授权：2026-10-31用户明确要求5次新的试玩，每次最多30分钟，从现有状态连续推进。仅Pokemon专用工具、个人Skill及配套验证可自主修复；AF模型请求、上下文、通用命令/权限、运行调度等核心改动必须暂停并给用户确认方案。无回档刷结果、源码player替代、CUA、直播或观众交互。
- 归档：同一私有实例根的`loop-five-20261031.json`为循环账本；各轮`loop-NN-{code,request,events,snapshot,summary,end-check,evidence-manifest}-20261031.json`记录独立候选和完整证据，不覆盖旧轮次。

| 循环轮次 | Run | 当前结果 |
|---|---|---|
| 1 | `1fdc6358-00f9-435d-9306-9427a3d7a3ad` | 正常完成，28分40.753秒，94模型/93工具；局部修复及63项测试通过 |
| 2 | `31b2bf64-0756-4b35-8374-b616a9f38ef3` | 正常完成，28分37.902秒，84模型/83工具；观察边界修复及69项测试通过 |
| 3 | `10e1d95f-af95-4f01-98e7-29d3826932c7` | 正常完成，28分43.593秒，82模型/81工具；局部修复及76项测试通过 |
| 4 | `a6cf18ca-4693-423c-82ff-9f21cb77ae04` | 29分23.856秒；AF先保存暂停，随后旧结果回看期间被watchdog取消；82主请求+3摘要/81工具，修复及78项测试通过 |
| 5 | `37278d95-0a3d-4392-b32e-e593cfd1c6ad` | 正常完成，28分46.185秒，89模型/88工具；局部修复及79项测试通过 |

- 第1轮：17:28:05.931至17:56:46.684，Astra/high、800000窗口、原会话/中文ROM。71份冻结文件无漂移。94模型请求全完成、0摘要/重试，93工具调用0错误；75 act、11 wait、2 dialogue，其余为Skill读取、状态、图片授权、begin和finish。观察器94次与API/事件完全对齐，log_observation_errors为空；没有复现E-019的长响应停顿，不据此宣布该问题解决。
- 第1轮游戏进度：赢得捕虫少年战，BUD升到8级并学会寄生种子，SKY曾倒下；随后回常磐市恢复全队，BUD25/25、SKY13/13，购买4瓶药水和3份解毒药，剩1572元。保存于商店菜单，未穿过森林、未通关。AF主动finish，余59秒，快照`d092048493664ec2ae0aad8a5b747e62.ss0`，SHA-256 `e2de0c46dcb0d6ddf05f641356a9b627c74d937c2e3302cdb41e82a83d9ed2f2`；paused、pending=null、预算删除与watchdog退出均确认。
- 第1轮性能：模型合计1061.818秒，中位10.8655秒；工具594.393秒。act/dialogue批次间隔中位18.014秒，包含wait为17.377秒。input29022389/cached28742144/output16536，输入加权缓存99.034%，末次input354646；strict_extension77/rebase17。不同游戏场景、非A/B，计时有包含关系，不能直接把差异归因于单项改动。
- 第1轮边界：护士选择框正确停止，随后治疗完成；90个带图结果130张、单次最多5图，36方向动作仅1次显式对照返回双图。9次战斗相关frame_budget/unstable集中在592–600帧，包含中毒/寄生结算以及无箭头的训练家结束文字；购物选择框出现但文字尚未完整，产生数次额外短wait。后续wait的36帧不等于真实缺少36帧，因为模拟器在模型思考期间继续运行。
- 第1轮后修复：只改`tools/pokemon/runtime/actions.py`、对应测试和个人Skill说明。输入按键及普通对白/世界观察仍保留600帧；识别为蓝底战斗文字后，只有无按键等待可使用总1200帧，20秒/8图/试玩截止保持不变，菜单、确认、选择仍由AF处理。新增回归覆盖长战斗三类停止边界、时限及禁止借等待扩展按键；没有把购物页或无箭头通知自动按A。
- 验证：`scripts/check/run.ps1 --stage backend tools/tests/pokemon -q`通过（lint、mypy871文件、63 tests）；Skill validator UTF-8模式通过、guidance audit零问题。个人包备份`62dfff87f82d4c1485ef40523ff0dead`后更新；`visual-smoke-f27acab734fa4b0a8f6ee900c6a21d82.json`确认选择框0次B、检查后恢复并暂停。`loop-01-post-fix-20261031.json`保存修复文件hash、验证和备份，第二轮冻结记录绑定新候选；第一轮结果不冒充已验证新等待策略。
- 第2轮：18:02:54.658至18:31:32.560，84模型/83工具、0错误/重试/摘要，watchdog对账完全匹配。71份冻结条目中仅evidence-index/progress两份运行记录滚动追加，runtime_source_drift为空，未改运行代码。击败另一名捕虫少年及两只绿毛虫，奖励96元，BUD升到9级；保存前队伍核实21/27HP（AF先前报24/27，后自行纠正），SKY13/13HP，无伙伴倒下、无药品消耗或捕获，野生铁甲蛹已逃离。停在森林西侧草丛，主菜单打开，尚未找到出口。
- 第2轮finish余64秒，快照`b0991e045e69476c8f06db5d87a2fbf2.ss0`，SHA-256 `c0fe4136f5a10844e8bd7a19ce70681e18dc8676297c0a8bc8453d82bdaf7265`；paused、pending=null、预算移除及watchdog退出确认。80个带图结果124张、单次最多5图；44方向动作仅1次双图。
- 第2轮等待改动：5次在612/832/832/664/772帧抵达菜单或确认边界，超过旧600帧限而不额外wait，属于已验证的局部机制改善。仍有3次macro_time_limit：两次较长寄生结算及一次无箭头训练家战后台词；不把扩展帧限写成已经解决所有等待。动作/对白间隔中位19.159秒，模型中位12.0195秒；不同场景非A/B，整体间隔没有因此更短。已完成input33932469/cached33624576/output15367，缓存99.093%，末次input446002，69 extension/15 rebase。
- 第2轮后修复：入场插画不再作为普通稳定画面就绪；选择框仅等待底部说明文字稳定，一旦本批见过选择，随后窗口消失也停止自动B；无箭头且有字形的蓝底文字约8秒不变时返回text_unchanged、decision_ready=false，不按A。源文件仍仅Pokemon actions/vision、测试及个人Skill；没有改AF核心。三个停止边界均保留20秒、帧、图片与本轮预算。
- 证据与验证：`loop-02-text-stability-20261031.json`显示本轮自动结算采样同文字最长5.771秒，战后无箭头台词17.861秒；上一轮对照样本自动结算最长5.524秒、台词11.786秒，见`loop-02-offline-boundaries-20261031.json`。这是采样证据，不证明其它长动画一定低于8秒，因此只提前交还不确定画面。真实森林入场图由other改为transition，后续地图仍other；69项专项、lint/mypy871、Skill validator/audit通过。个人包备份`24a39895baf64bfbbc929a58a84afac0`，无模型smoke `visual-smoke-42b4a029bf1a4d1a99103d6ecc3e94e5.json`恢复完成并暂停。修复hash与检查在`loop-02-post-fix-20261031.json`，后补修复不计入第2轮已跑候选。
- 方案影响：仅更新专用工具等待/观察契约和Skill；没有改AF核心、上下文阈值、权限缓存或provider超时。进一步优化取决于后续证据，不能为凑循环次数制造改动。

- 第3轮：18:41:45.430至19:10:29.022，82模型/81工具、0错误/重试/摘要；watchdog对账一致，71份冻结文件无漂移。穿过常磐森林到尼比市，捕获5级雄性独角虫（1球），赢得出口捕虫少年战及108元，BUD升10级学会藤鞭。中心治疗后BUD29/29、SKY13/13、独角虫20/20；保存于市区北侧，未入道馆/获徽章/通关。AF主动finish余56秒，快照`81cfe57b7239442bb370aee9003dc2b6.ss0`，SHA-256 `435c168e377359051198b85ef3d0580253641290fb2265fd2d60a55bf2e60818`，paused、pending=null、预算删除均核实。
- 第3轮性能：模型1109.652秒、中位13.1275秒；工具543.236秒，act/dialogue间隔中位19.699秒。input40528524/cached40200320/output14658，缓存99.190%，末次input534262；67 extension/15 rebase。78带图结果119图、最大5图。仍非A/B；上下文更大、游戏场景不同，不推算通关速度。
- 第3轮验证/新问题：长战斗628–832帧到菜单/确认，命名与护士选择说明稳定后交还AF；战后台词以text_unchanged提前返回且AF自行确认。仍有一次20秒长回合。两张灰框路牌被误判other，dialogue零按键后还需手动B；367张已回传截图的灰框配色筛查只命中这两张。多个战斗文字确认逐次A造成模型往返。后续白色NPC对白是否由A重新触发尚未证实，不据此作因果结论。
- 第3轮后修复：专用recognizer增加当前灰框路牌配色（保留白底面积及双边框条件）；新增显式confirm-text，只对已识别蓝底确认箭头单帧A并松开，逐页保留图；菜单/选项/普通对白/未知/重复/无箭头停滞和原预算均停止，绝不代选。普通dialogue仍B。76项专项、lint、mypy871及Skill validator/audit通过；真实旧路牌两图现为dialogue，关闭后地图为other。个人包备份`68f32812e09e420e856287feaa789ec2`，smoke `visual-smoke-2eedeae75b6a4c608dc507704faf80eb.json`零B后恢复并暂停。单帧A效果与批量收益待第4轮，不能用fake测试声称已实测。

- 第4轮：19:20:03.866至19:49:27.722，82主请求（81完成、末次取消）+3摘要，81工具、0工具错误/网络重试；71冻结文件无漂移。击败道馆训练家和小刚，原图`03607094ed044a06b654e3161dc1383e.png`确认取得灰色徽章；BUD升13级，战后33/35HP，随后护士治疗完成，保存于尼比中心主菜单。AF finish余52秒，快照`209b1b62576448ec89d3fd6195bccd89.ss0`，SHA-256 `edee575ed8f2ee8a5c79ad950ff84a3f623a8f6a917b0e7826387e1556744edc`；paused、pending=null、预算删除均核实。finish后3次recover_tool_result回看旧图，报告生成前被原截止取消；不是保存失败，也没有外部补操作。
- 第4轮工具：16次confirm-text执行25次A，7次repeated_page、2次菜单、2次选择、2次其它边界、2次text_unchanged、1次帧上限。长战后文字确有单次推进4页/3页；但单帧A漏推进降低收益。私有`loop-04-confirmation-probe-20261031.json`记录奖金页6个采样归一化全图一致，中间1帧A后仍未变，后续AF常规8帧A退出；经验页另有相同文字、经验条变化。只确认脉冲可靠性和等待范围不足，不声称已定位ROM内部输入轮询。一次wait漏output_images由AF用view_image恢复，没有重放游戏动作。
- 第4轮上下文：压缩19:30:15.583至19:35:01.759，共286.176秒，3段摘要正常完成，sequential_full、无fallback，估算659748→185034，随后主玩法恢复；阈值640000/窗口800000未改。保存后的cancel API计数仍0，与conversation_done的82/81不一致，按日志/事件恢复真实计数；watchdog记录82次、无观测错误。这两项属于AF通用层，仅观察，不混入游戏修复。
- 第4轮性能：主模型888.042秒（含末次取消2.359秒，不含摘要），中位10.185秒；工具542.303秒；含确认/wait的动作间隔中位17.392秒。累计input20703790/cached19737088/output22510包含3段摘要，取消请求缺usage；缓存95.331%，主请求末次input98084。61 extension/20 rebase。77带图结果168图、单次最大8图，含3次恢复旧结果；不同场景及本轮压缩，不作为单项A/B提速结论。
- 第4轮后修复：confirm-text对确认箭头要求去除箭头闪动后的全图连续60帧稳定，再使用既有tap的8帧A+24帧释放；所有停止和预算不变，不扩大自动选择权限。Skill要求finish后直接简短报告，未核实细节留待下一轮，不继续翻查旧结果。78专项、lint、mypy871、Skill validator/audit通过；个人包备份`bc066e3c1a71476f9efd39ba71e1ce19`，`visual-smoke-4baa5f2283e946ed88fb8a2c522d4124.json`零按键检查后恢复暂停。真实确认可靠性待第5轮。

- 第5轮：19:53:22.923至20:22:09.109，89模型/88工具、0错误/摘要/重试；watchdog/API/conversation_done对账一致，71冻结文件无漂移。取得跑步鞋、进入3号公路，击败使用两只9级波波的训练家并获144元；BUD14级、SKY3级、独角虫5级，无倒下或新增捕获。用了两瓶药水、补买四瓶。返回尼比中心护士治疗选项时保存，尚未确认治疗，最终HP没有重新核实。AF finish余56秒后直接报告，快照`d3901f1b821f41b5bf8ad488b2905b94.ss0`，SHA-256 `20a5ee1cc72f4b76c40ea94786ad05658f6337b5ef92332221d1095b17fcdac3`；paused、pending=null、预算删除均确认。
- 第5轮确认工具：6次confirm-text、11次A；1次战斗菜单、3次其它边界、1次选项、1次无箭头text_unchanged，0 repeated_page。两段经验结算分别一次推进3/4次确认、保留4/5张图，奖金页一次退出；没有越过换人或升级信息页自动选择。相较第4轮16次中7次重复页，支持修复方向，但场景不同且样本少，不是A/B或可靠率保证。普通dialogue有1次重复页，属于领取跑步鞋过程，不混入战斗确认指标。
- 第5轮性能：主模型999.331秒、中位9.744秒，工具679.054秒；所有游戏批次间隔中位17.193秒。input13371739/cached13144960/output15995，缓存98.304%，末次input194819；69 extension/20 rebase。85带图结果170张、单次最多6图，31方向动作1次双图。第70次模型响应77.920秒后成功完成，无重试；不把该延迟归因于游戏工具，也未修改provider超时。
- 第5轮后修复：两张队伍HP回复提示被底部紫白配色误标battle_menu；668张已回传截图的排除条件只命中这两张。识别器增加当前队伍背景排除，保留other交还AF；实际行动菜单/招式菜单保持battle_menu。柜台交互中连续移动/转向/A未触发，AF自行改LEFT8/WAIT24/A8后成功；Skill补充先核对朝向、释放等待再交互，不原样重复A。79专项、lint、mypy871、Skill validator/audit通过；个人包备份`dc795dbc2b884360b9727d78e322e5ba`，smoke `visual-smoke-2d8001105eaf46629a3ae623205593a3.json`选择框0次B，恢复原状态并暂停。最后两项仅离线/无模型验证，未追加第六轮付费运行。
- 五轮收口：累计8652.289秒（2小时24分12秒），每轮均小于30分钟；431主模型请求+3摘要请求、426工具调用、0工具错误。四轮正常报告，第4轮AF已保存后被watchdog取消；全部游戏快照由AF执行finish，无外部补游戏操作。所有核心冻结条目24份与循环前一致；仅修改Pokemon工具/个人Skill及配套测试文档。原有Tauri dev保留（当前worktree/branch，owner6880、bridge20064）；游戏控制器paused、无pending和有效试玩预算。未提交、合并、打包或内置Skill。完整通关仍未完成。

## 未决问题与下一项证据

- 本次5轮授权已用完，不覆盖新的真实模型运行。通用AF层的压缩耗时、取消计数投影缺口和权限准备成本仅保留证据；若选择修复，须先给用户确认方案，未通过游戏工具绕过。长期上下文/存档配对恢复、游戏内存读与完整通关仍待验收。


## Artifact 与敏感性

- 已有本地代码、自动化报告、截图、模拟器快照和十一轮真实桌面模型试玩；没有完整通关记录。
- 中文候选仅在仓库外；未复制上游 GPT 玩家代码进入产品，未创建外部 fork。
- 后续运行原始材料按用户数据与日志既有路径管理，索引保留校验和及最小证据；ROM、凭据和完整私人对话不进入 Git。

## 对账记录

| 日期 | 结果 |
|---|---|
| 2026-10-31 | 需求已确认；证据均为代码或公开资料，所有运行时结论保留为未验证；设计与实现尚未开始落盘 |
| 2026-10-31 | 方案确认后实现个人 Skill 与实验入口；确定性与真实模拟器检查通过，中文候选已取得并初步运行。真实 AF 试玩等待本次 API 预算授权 |

### E-008 · 真实桌面个人 Skill 有界试玩

- 用户授权先启动当前 feature 分支 dev，再直接从 AF 个人 Skill 试玩；上限 20 模型调用或 600 秒。
- 当前 worktree / `feature/168-autonomous-pokemon-firered`，Tauri owner PID 2740，bridge runtime `b356964a49e14607894f1264fc0cd2e0`。依赖缺失通过 `scripts/frontend/install.ps1` 补齐；随后标准桌面启动成功。源码与个人包均在本次进程启动前就绪。
- 通过真实 AF 窗口新建会话、设置 medium 并发送请求；界面显示加载 personal `pokemon-firered`。没有使用源码 player 代替桌面。原生 CUA 截图显示最终报告，UIA 对运行正文返回空，未据此宣称 DOM 验收通过。
- 会话 `1fd96658-3377-4c16-aa63-1b7c3be3335e`；run `a9918cf4-1683-4180-afd8-747ff807483e`。2026-10-31 09:56:29.846 至 10:02:26.604（北京时间），356.758 秒；16 个模型调用和 16 个 transport request，无重试；15 次工具调用，5 次错误。
- 按默认逐次审批开始；前几次由 Coding Agent 仅允许当次请求。后续运行记录显示 `user_enable_session_auto`，界面变为本会话无需授权；Coding Agent 没有操作该模式切换，不把这段耗时假定为始终逐次审批。
- 成功工具结果：2 observe，1 act（START 1 帧、松开等待 100 帧），3 view_image 返回图片，1 checkpoint。最后截图仍为中文标题页，未确认开始新游戏、命名、选角、队伍或战斗。
- 失败步骤：实例目录写权限、命令 JSON 转义、暂停状态、受管命令中的 shell 控制符、checkpoint 缺少 observation_id；AF 后续自行纠正。单帧 START 未推进原因未确认。
- 时间证据：模型生成合计 175.920 秒、中位 9.984 秒；命令授权等待 95.389 秒、命令启动 16.721 秒、命令执行 7.608 秒。命令授权不包含独立图片权限等待，不能把这些数简单当作完整互斥分解。observe 65.7/70.1ms，101 帧动作 1.739 秒，checkpoint 87.7ms。
- usage 累计 input 625316（其中 cached 580864），output 3018；是逐请求累计量，不是唯一上下文大小或费用估算。
- 最终正常 `end_turn`，16 次调用时主动收口；宿主提前设置 16 continuations/540 秒工具循环上限，外部 watchdog 570 秒或第 18 次 transport request 取消并 pause，为 20/600 总限保留余量。本轮没有触及硬停止阈值，不能把 watchdog 强制取消标成实测通过。
- checkpoint `a84c49c36a7242c79cbbfd457e22cfae`，SHA-256 `7dbd341e11c97c98fe76aa7231cb71c02a5c677a0497dfe71bd552630f4de504`。只证明模拟器快照；没有生成 AF 上下文配对恢复清单。paused 标记存在，watchdog 已退出。
- 私有证据在 `agent.paths.user_data_dir()/pokemon/instances/b7b5c43c972b4d7795b3c8d29fe20c3e/`：`desktop-pilot-20261031.json`、`desktop-pilot-events-20261031.json`、`desktop-pilot-latency-20261031.log`、`desktop-pilot-summary-20261031.json` 和对应 screenshots/saves。原始对话、截图、ROM 均未进入 Git。
- 结论：个人 Skill 能接通实际桌面游戏，但当前接口和调用路径效率未达标；不计为有效通关推进。保留 dev / mGBA 供用户查看，无后台模型续玩。

### E-009 · 调用契约优化与 30 分钟桌面复测

- `runtime-observed`，2026-10-31；补充 E-008，保留前轮结果。用户明确授权优化并直接复测，将本次上限改为 30 分钟，继续 gpt-6.1-sol / medium、原会话和中文游戏实例。没有启动源码 player，未修改安装包。
- 候选改动：cmd 简单参数 --tap/--step、status 返回暂停和目录信息、begin 合并恢复/观察、finish 合并 checkpoint/pause；个人包 update 校验旧清单、备份、保留实例绑定；watch-desktop 只监控指定会话的新 desktop_text run，并向脚本提供剩余时限。35 项专项、lint、mypy 858 文件通过；真实 mGBA begin/finish 无按键 smoke 通过。
- 运行身份：同一 feature worktree，base `89162a4499ca1c6137714692f58c653fb565dd9b` + 未提交 overlay；Tauri PID 37276、dev owner 32732、bridge `1c5849630c3245328988fc9eadc70dc0`，端口 1420/18800。新进程晚于运行时代码更新，个人包版本由本轮 skill_read 确认。原始与修正候选的文件 hash 分别在 `optimized-pilot-code-20261031.json`、`optimized-pilot-corrected-code-20261031.json`；试玩后只改 --help 文案，不倒改候选身份。
- **失败首段保留**：run `2a16a609-db1c-4a1c-bc25-ce6e6f03da7d`，10:20:11.869 至 10:21:36.706，84.837 秒、7 transport requests、7 工具结果、1 错误。Coding Agent 把普通 exec_command 的 argv 能力误写进 Skill 绑定指引，忽略 allOf 的 cmd 必填/argv 禁止条件；AF 被拒绝后自行改 cmd。Coding Agent 暂停并修正指引，不把这次失败归因于 AF 游戏能力。该段只执行一批 WAIT 540 帧，没有游戏按键。旧 run API 已过期，工具原始记录从本会话 JSONL 按 meta.run_id 恢复，保留 record_uuid/record_ts，未伪装为实时事件序号。
- **修正段**：run `882fd7fe-3d52-468b-b567-c01c770b9ad7`，10:25:42.075 至 10:49:02.008，1399.932 秒。102 个 transport requests / 102 次模型调用 / 102 个工具结果；其中 skill_read 1、exec_command 51、request_filesystem_access 1、view_image 49。1 次错误是模型混用互斥 --tap/--step，收到明确错误后改为统一 --step 并继续；没有重复出现权限目录、暂停、JSON 转义、shell 控制符或 checkpoint 编号错误。
- 观察到的进度：女主角 ACE、劲敌 BOB；从标题和开场进入房间，经过室内找路、出门、博士带入研究所，选择妙蛙种子并停在博士的选择后对话。47 次成功 act（含 5 次纯等待）、9198 个请求帧；首次战斗、游戏内保存/读取及通关未覆盖。房间定位和重复确认博士对话明显消耗动作；单次纯视觉样本不足以认定稳定模型能力上限。
- 修正段性能：连续 act 起始时间间隔中位 27.055 秒；模型生成合计 1090.037 秒（占 run 时间约 77.9%）、中位 10.342 秒/次、最大 18.851 秒。动作执行合计 155.907 秒；命令启动 94.497 秒、命令执行 183.623 秒、命令审批等待 1.016 秒。指标有嵌套/重叠，不能相加当作互斥时间分解；请求帧不等于整个运行期间游戏时钟。
- 修正段累计 input 9,426,595（cached 7,802,496）、output 14,878；失败首段 input 317,202（cached 306,432）、output 879。是日志中完成请求的累加值，不是唯一上下文或费用。首段被取消请求可能缺少完成 usage，不据此声称全部计费量已知。
- 总窗口从第一观察器布防到结束约 29 分 2 秒，从首次 AF 请求到结束约 28 分 50 秒，含修复停顿；两段合计 109 次真实请求。首观察器预留 30 秒、目标截止 10:49:30；恢复时按余量重新布防，启动开销使 watcher 内部 deadline 晚约 8 秒，但本次实际 10:49:02 已结束，未触及目标或 30 分钟上限。不能把未触发的硬取消路径标为本轮实测成功。
- AF 在 finish 结果返回约 60 秒余量时完成 checkpoint 并暂停，再看保存图和总结，正常 end_turn；checkpoint `5e23018a745b4cba863d1f6835472c2d`，SHA-256 `0b289a4b094fa8b661b562743966c3a42222b29414a47c646028f230f92e42b9`。paused 标记存在、pending=null、watchdog 退出且 budget 文件清除；没有 Coding Agent 补做游戏操作或保存。仅为模拟器快照，未验证 AF 上下文配对恢复。
- 用户中途要求不用 computer-use；其后只用本地 REST、日志、截图文件完成监控和收尾，未用 UI 自动化。游戏操作始终来自 AF 的个人 Skill。会话沿用已存在的 session_auto，Coding Agent 没有切换审批模式。
- 私有原始材料仍在 E-008 同实例：两个 `desktop-pilot-optimized*.json`；`optimized-pilot-{false-start,corrected}-20261031-{events.json,latency.log,summary.json}`；上述候选 hash；对应 operations/screens/saves。`optimized-pilot-evidence-manifest-20261031.json` 校验 113 个文件。完整原始工具输入输出仅在本机，不进入 Git。
- 判断：`confirmed` 接入、批量动作、正常保存暂停和本次进展；`directional` 调用契约改善。不是受控 A/B：两轮时长、审批状态、历史上下文和游戏起点不同；不能宣称固定提速倍数或外推通关时长。下一步优先验证动作与图片同次返回，减少转场及重复对话往返，再进入战斗和长跑恢复。需求保持 IN_PROGRESS。
