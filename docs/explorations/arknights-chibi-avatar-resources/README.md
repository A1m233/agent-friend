# Exploration · Arknights Chibi Avatar Resources

> 过程笔记，非决策、非需求。记录“把明日方舟宿舍 / 基建小人形象接入桌宠系统”的资源调研、生态判断、接入影响评估与后续 spike 方向。若后续进入实现，应新建立项需求或 spike 文档，以对应文档作为 single source of truth。

## 0. 结论快照

这条线值得继续探索，而且用户最初的直觉基本成立：社区里已经有人把明日方舟小人做成桌宠，并且做得相当成熟。关键修正是：

- **明日方舟宿舍 / 基建小人主要不是 Live2D，而是 Spine 骨骼动画。**
- 现有前人项目最值得参考的是 [ArkPets](https://github.com/isHarryh/Ark-Pets) / [Ark-Models](https://github.com/isHarryh/Ark-Models)，不是普通 Live2D 模型库。
- 官方可直接分发的 Arknights 小人资源没有找到；社区模型通常明确标注素材版权归鹰角，适合个人本地研究，不适合打包进产品或仓库。
- 对当前 Live2D 路线不是推翻，而是新增一个未来可能的 **Spine avatar backend**。
- 正在做的 033 Live2D 调试窗口应继续按 **Live2D 专用调试器** 收口，不要现在改成通用 Avatar / Spine 调试器。

一句话：**Arknights 小人路线有戏，但它应该是“Spine 接入探索”，不是“替换 Live2D”。**

## 1. 背景

当前桌宠形象已经走通 Live2D：

- `frontend/src/pet/live2dConfig.ts` 固定配置内置 Hiyori 模型。
- `frontend/src/pet/usePetLive2D.ts` 把 `Live2DSprite` 塞进 `usePixiAvatarSlot` 暴露的 PIXI `avatar-slot`。
- 018 / 024 已经围绕 Live2D 做了状态机、lip-sync、视线跟随、点击反馈、拖拽反馈。
- 033 正在把“试 motion / 看反馈”的工作流沉淀成 dev-only Live2D 调试窗口。

本次探索来自一个产品直觉：明日方舟宿舍里的 Q 版小人，很适合做成 agent-friend 的桌宠形象。它看起来像 Live2D，但社区和资源线索显示，这类小人实质上多是 Spine。

## 2. 资源与社区项目

### 2.1 ArkPets

[ArkPets / 明日方舟桌宠](https://github.com/isHarryh/Ark-Pets)

配套 B 站视频：[【ArkPets】重磅更新 如何把动态立绘种在任务栏？](https://www.bilibili.com/video/BV1dv421C7QF)

这是最接近目标形态的前人项目。README 中描述它支持：

- 干员基建小人，包括时装。
- 敌人战斗小人。
- 干员动态立绘。
- 走路、坐下、躺下、戳戳、基建特殊动作等桌宠行为。
- 社区维护模型库下载。

它说明“明日方舟小人作为桌宠”不是空想，已经有成熟路径可参考。

### 2.2 Ark-Models

[Ark-Models](https://github.com/isHarryh/Ark-Models)

这是 ArkPets 相关的模型资源仓库，定位为 “Arknights Spine Models (Excerpt) / 明日方舟 Spine 动画小人模型（节选）”。目录结构大致对应：

| 目录 | 内容 |
| --- | --- |
| `models` | 干员基建小人，含时装 |
| `models_enemies` | 敌人战斗小人 |
| `models_illust` | 干员动态立绘，含时装 |

重要技术点：

- 格式是 Spine 模型，通常是 `.atlas` + `.skel` + `.png`。
- README 标注需要 **Spine Runtime v3.8**。
- 资源来自解包 / 提取流程，而不是鹰角公开发布的开发者素材包。

重要授权点：

- README 明确提示素材版权属于鹰角网络及关联方。
- 不应商用，也不应直接打包分发到我们的应用或仓库。

### 2.3 ArkPets-Web / ArkPets-Chrome

[ArkPets-Web](https://github.com/fuyufjh/ArkPets-Web)

这个项目把 ArkPets 思路移到 Web 侧，README 提到实现基于 Spine-ts / WebGL。它对我们很有参考价值，因为 agent-friend 的 pet 窗本身就是 Tauri + Web 前端，底层已经有 PIXI canvas。

[ArkPets-Chrome](https://github.com/fuyufjh/ArkPets-Chrome)

这是浏览器插件方向，把 Arknights 小人显示在网页上。它进一步证明“Web 环境里跑 Arknights Spine 小人”是可行方向。

### 2.4 B 站教程与提取工具线索

社区里有不少面向明日方舟 Q 版小人的提取和查看教程，例如：

- [【教程】明日方舟的Q版角色提取方法](https://www.bilibili.com/video/BV1x7411P7U9)
- “不需要下载 Spine 导出明日方舟小人透明底 gif 动图”的相关教程，指向过 SpineViewer 一类工具。
- “游戏各资源提取教程之 立绘与 Q 版 / Q 版小人动画”等教程。

这说明社区实践不是零散个案，而是围绕 Spine 模型提取、查看、导出形成了一条相对稳定的民间工具链。

### 2.5 Fan Live2D 资源

也能搜到粉丝制作的明日方舟 Live2D 模型，例如：

[博士~我才不是阿米驴呢！[阿米娅免费live2d]](https://www.bilibili.com/video/BV1bCQWYpESX)

这类资源说明“明日方舟角色 Live2D 化”存在，但它不是宿舍小人的原始技术路线，且授权限制通常很窄：不能转卖、不能搬运、不能直播盈利，版权仍归鹰角。它可以作为“用户本地导入 Live2D 模型”的灵感，但不适合作为内置资源。

### 2.6 Aceship / PRTS 线索

[Aceship Arknight Images](https://github.com/Aceship/Arknight-Images) 和相关 viewer 生态里也能看到 Arknights Spine 资源和浏览工具的痕迹。这类项目适合作为格式和社区实践参考，不应作为可直接分发授权来源。

[PRTS 关于页面](https://prts.wiki/w/PRTS:%E5%85%B3%E4%BA%8E) 也明确区分 wiki 内容授权与游戏图片、动画、音频、文本原文版权。对我们而言，保守策略应当是：不把游戏原始素材视为可自由分发资产。

## 3. Spine 与 Live2D 的关系

观感上，Spine 和 Live2D 都是“2D 角色动起来”。工程上它们是两套不同 runtime。

| 维度 | Live2D / Cubism | Spine |
| --- | --- | --- |
| 常见用途 | 立绘、VTuber、半身角色、表情和口型 | 游戏 2D 骨骼动画、Q 版角色、战斗小人 |
| 典型资源 | `.model3.json` / `.moc3` / `.motion3.json` / textures | `.skel` 或 `.json` / `.atlas` / `.png` |
| 控制方式 | 参数 ID，例如 `ParamMouthOpenY` / `ParamAngleX` | animation track、bone、slot、attachment |
| 我们当前状态 | 已接入 Hiyori | 尚未接入 |
| Arknights 小人匹配度 | 粉丝重建可行，但不是宿舍小人主路线 | 高，社区主路线 |

因此，接入 Arknights 宿舍小人不应理解为“找一个 `.model3.json` 替换 Hiyori”，而应理解为“新增 Spine 模型加载和播放能力”。

## 4. 对现有系统的影响

### 4.1 好消息：外层底座可以复用

现有 pet window 的形象承载层已经是 PIXI `avatar-slot`：

- `usePixiAvatarSlot` 创建 PIXI stage 和 slot。
- `usePetLive2D` 只是把 Live2D sprite 作为 slot child。
- 拖拽、ActionBar 锚点、bubble 跟随、透明区 hit-test 都围绕 slot / rendered pixels 工作。

这意味着 Spine 不一定要推翻桌宠窗口，只要它能作为 PIXI display object 或等价渲染对象进入同一个 slot，就可以复用很多外围能力。

大概率可复用：

- pet 透明整屏窗口。
- slot world position 数据流。
- 拖拽位置更新。
- ActionBar / bubble 跟随。
- DOM hit 优先与 alpha hit-test 大框架。
- `petStateStore` 的状态投影。

### 4.2 真正需要改的地方

当前 Live2D 能力散落在几个专用点：

- `usePetLive2D` 只加载 `.model3.json`。
- 状态机 motion 派发直接调用 `sprite.startMotion(...)`。
- `MouthDriver` 写 Live2D 参数 `ParamMouthOpenY`。
- `GazeSource` / `TapReactionSource` / `DragReactionSource` 写 Live2D 参数。
- 交互调度的类型是 `Live2DSprite`。

如果要正式支持 Spine，比较稳的方向是抽一层 avatar runtime，而不是把 Spine 分支硬塞进 Live2D 代码：

```ts
interface PetAvatarRuntime {
  kind: "live2d" | "spine";
  ready: boolean;
  play(action: "idle" | "thinking" | "speaking" | "error" | "tap"): void;
  destroy(): void;
  setMouthOpen?(value: number): void;
  setGaze?(x: number, y: number): void;
}
```

Live2D adapter 包住 `Live2DSprite`；Spine adapter 负责 Spine skeleton / atlas / animation state。状态机和 UI 层只面对 `PetAvatarRuntime`。

### 4.3 风险点

最大风险不是业务架构，而是 runtime 兼容：

- Ark-Models 标注 Spine Runtime v3.8。
- 我们当前前端是 `pixi.js` v8。
- 官方现代 Pixi Spine runtime 未必直接兼容 Spine 3.8 的 `.skel`。

因此第一步不能直接立完整需求，应该先做最小 spike：在 pet 窗或独立 playground 里加载一个本地用户自备的 Arknights Spine 模型，确认能否显示、播放 idle、切动画，并观察 bounds / alpha hit-test 是否还能稳定工作。

## 5. 对 033 Live2D 调试窗口的影响

结论：**033 不应被这次 Spine 探索打断或扩大范围。**

033 的目标是 dev-only Live2D motion / feedback 调试窗口。它调的是当前 Hiyori `Live2DSprite`，核心能力包括：

- 读取 `model3.json` motion catalog。
- 发送 `startMotion({ group, no, priority })`。
- 触发当前项目级 Live2D 点击反馈。
- 辅助人工观察 Hiyori motion 语义。

这些能力对 Live2D 是正确抽象，但对 Spine 不通用。Spine 会有不同的 catalog、priority、animation track 和参数控制模型。若现在把 033 改成通用 Avatar 调试器，会带来两个坏处：

- 当前 feature 范围从“调 Live2D motion”膨胀成“多 backend 调试平台”。
- Spine runtime 兼容还没验证，会把未知风险拖进 033 收口路径。

更好的边界是：

- 033 继续叫 Live2D 调试器，按 Live2D 专用工具完成。
- Spine 探索另开 spike，先验证渲染和动画播放。
- 等 Spine 跑通后，再决定是否新增 `SpineDebugger`，或在更高层做 `AvatarDebugger`。

## 6. 建议后续路线

### 6.1 短期：完成 033，不混入 Spine

把当前 Live2D 调试窗口先收口，继续服务 Hiyori / Live2D motion 观察。它对调现有点击反馈、idle 候选动作仍然有直接价值。

### 6.2 中期：开 Spine runtime spike

建议另开轻量 spike，目标非常窄：

- 不提交 Arknights 素材。
- 使用用户本地自备模型路径，或临时放在 gitignored 目录。
- 尝试加载 `.skel` / `.atlas` / `.png`。
- 播放 idle / walk / tap 之类的基础 animation。
- 把 Spine display object 放进现有 `avatar-slot` 或一个等价 playground。
- 验证 bounds、drag、ActionBar 锚点、透明区 hit-test 的基础表现。

需要重点验证的技术路径：

- `@esotericsoftware/spine-pixi-v8` 是否能处理 Spine 3.8 资源。
- 如果不能，是否需要 `spine-ts` 旧版 WebGL runtime。
- 是否能把 `.skel` 转换成兼容 JSON。
- 若必须独立 WebGL canvas，是否仍能和当前 slot / alpha hit-test 机制协同。

### 6.3 长期：Avatar backend 多态化

如果 Spine spike 成功，再考虑把当前 Live2D 专用形象层抽成多 backend：

- `Live2DAvatarRuntime`
- `SpineAvatarRuntime`
- 统一 action：idle / thinking / speaking / error / tap / drag。
- 能力探测：mouth / gaze / parameter feedback 可选。
- 资源导入策略：内置安全样例 + 用户本地导入，不内置 Arknights 资产。

### 6.4 产品化资源路线

有三种清晰路线：

| 路线 | 适合阶段 | 风险 |
| --- | --- | --- |
| 用户本地导入 Ark-Models 等社区资源 | 个人研究 / 本地 demo | 授权灰区，不打包不分发 |
| 支持 Spine backend，但不提供 Arknights 资源 | 开源工具 / 技术能力 | 用户导入行为仍需免责声明 |
| 约稿原创 Q 版 Spine / Live2D 角色 | 产品化 | 成本更高，但合规最稳 |

## 7. 开放问题

- Spine 3.8 与 Pixi v8 的最小可行 runtime 是哪一个？
- 如果使用旧版 spine-ts，是否会和现有 Pixi v8 renderer 冲突？
- Spine display object 的 bounds 是否足够稳定，能不能承接当前 ActionBar / bubble anchor？
- alpha hit-test 读 framebuffer 时，Spine 渲染结果是否能被现有路径采样？
- Arknights 资源只允许“用户本地导入”是否足够符合项目未来的发布边界？
- 是否需要为用户导入模型做显式版权提示和 gitignore 目录约定？
- 如果最终约稿原创形象，应选择 Spine 还是 Live2D？这取决于目标表现：Q 版动作密集更偏 Spine，半身情绪表达和口型更偏 Live2D。

## 8. 当前推荐判断

目前最稳的判断是：

1. **Live2D 继续作为当前主线。** Hiyori / 033 调试器 / 018 / 024 都不需要因为这次探索返工。
2. **Arknights 小人单独作为 Spine backend 探索。** 不把它塞进 Live2D 调试窗口。
3. **资源上走本地导入，不内置。** 官方授权不明确，社区资源版权归鹰角，不适合分发。
4. **先 spike runtime，再谈产品化。** 兼容性跑通之前，不要提前设计完整导入 UX 或模型市场。

这条线如果跑通，价值会很高：它能把桌宠形象从“Live2D 半身角色”扩到“游戏级 Q 版小人”，但工程边界要保持清楚。
