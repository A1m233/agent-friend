# Exploration · Pet Live2D Debugger

> 过程笔记，非决策、非需求。记录“用开发期工具手动触发 Live2D 动作 / 表情 / 项目事件，从而给桌宠动作做人工标注”的讨论快照。后续真正落地时，应新建立项需求，并以 `docs/requirements/<id>-pet-live2d-debugger/` 为 single source of truth。

## 1. 背景

Hiyori 模型的 `model3.json` 只暴露 motion group 与文件编号，例如 `Idle[4] = Hiyori_m06`、`TapBody[0] = Hiyori_m04`。目前没有找到官方文档逐条说明 `Hiyori_m01` 到 `Hiyori_m10` 的语义。

仅靠解析 `.motion3.json` 参数曲线不够可靠；更适合的办法是提供一个开发期调试入口，让人直接播放每个动作，观察后给动作建立人工认知。

## 2. 当前触发点

- 桌宠普通待机已经有 `IdleLoop` 轮播池，避免把点击动作 `Hiyori_m06` 混入普通 idle 随机动作。
- 点击桌宠仍需要使用体感明显的 `Idle[4] / Hiyori_m06`，并叠加现有 `TapReactionSource` 的脸红 / 嘴型反馈。
- 未来若继续调整动作，缺少“快速试 motion”的工具会导致每次都要改配置、重启、观察，效率很低。

## 3. 候选形态

在桌宠 ActionBar 的 dev-only 分页里增加一个“Live2D 调试器”按钮，点击后打开独立 dev-only 窗口。

窗口建议保持窄而直观：

- Motion 区：选择 group、motion index、priority，并播放指定 motion。
- 快捷区：一键触发现有“点击反馈”、回到 idle、播放 idle 随机动作。
- 参数源区：后续可扩展为触发 tap 表情、拖拽反馈、gaze 反馈等项目级事件。
- 日志区：记录最近触发过的命令，便于人工对照动作。

## 4. MVP 边界

第一版只需要解决“看清每个动作是什么”：

- actionbar dev-only 入口；
- dev-only 独立窗口；
- 从 pet 窗口里的 Live2D sprite 获取 motion 列表，或由 pet 窗口执行“查询 motions”命令后回传；
- 调试窗口发命令，pet 窗口执行 `startMotion({ group, no, priority })`；
- 支持“一键点击反馈”，等价于真实点击桌宠；
- release build 不暴露入口。

明确不做：

- 不自动识别动作语义；
- 不解析 motion 曲线并生成解释；
- 不保存动作标注；
- 不做生产可见入口；
- 不做完整参数编辑器。

## 5. 待立项时重点确认

- 调试窗口与 pet 窗口之间走 Tauri event、前端 BroadcastChannel，还是现有 invoke + event 组合。
- motion 列表的来源：直接 `sprite.getMotions()` 回传，还是从 `model3.json` 静态读取并补文件名。
- priority 默认值是否固定为 `Force`，以保证调试时立即覆盖当前 idle。
- 是否需要把“只触发 TapReactionSource、不播 motion”也作为首版按钮，方便区分项目叠加表情和模型 motion 本身。
