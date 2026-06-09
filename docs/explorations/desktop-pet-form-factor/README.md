# 桌宠形态探索 · 调研与动画协议

> 探索记录（过程笔记），非决策、非需求。
>
> 本篇承载**事实与技术**部分：同类项目调研、Codex Pets 机制拆解、桌宠动画协议的能力边界与扩展方向。
> 偏**战略判断**的部分（"我们做实体陪伴的定位对不对"）拆在同目录 `positioning.md`。

- **创建时间**：2026-06-09
- **关联**：`docs/decisions/0001-product-vision-and-roadmap`（M4 桌宠形态 / M8 IM 通道 / 形态层）

---

## 1. 同类项目调研

调研目标：参考界面与交互。按"灵魂是谁"分两类——一类背靠 coding agent（开源桌宠居多），一类是陪伴/人格（商业 app 居多）。

### 1.1 桌宠形态（开源，多数背靠 coding agent）

| 项目 | 看点 | 技术栈 | 链接 |
| --- | --- | --- | --- |
| clawd-on-desk（⭐3.8k） | 像素桌宠实时镜像 AI coding agent 状态（思考/打字/庆祝/睡觉），托盘、拖拽、主题包、custom 动画 | Electron | https://github.com/rullerzhou-afk/clawd-on-desk |
| VPet（⭐5.8k） | 老牌养成系桌宠模拟器，投喂/互动/创意工坊，交互最完整、最"拟人养成" | C# / WPF | https://github.com/LorisYounger/VPet |
| TinyRoommate | 交互叙事最佳：桌面走动/打哈欠，定时冒 speech bubble（"你从 9 点就在写代码了，歇会？"），冷落会变沮丧 | Tauri + Rust | https://github.com/ryannli/tiny-roommate |
| DeskPet（像素螃蟹） | 中文项目，透明置顶窗 + 前台 app 检测自动切表情（编码/写作/聊天），逐 action 审批弹窗 | Electron + React | https://github.com/Ice-teapop/desktop-pet |
| NekoAI | 经典 Neko 进化版，8 方向跟随光标、动画对话气泡、随时间变化的 mood、右键调尺寸 | Tauri + Rust | https://github.com/nucket/NekoAI |
| Clyde | 轻量版 clawd，技术栈选型文档清晰（为什么 Tauri 不用 Electron） | Tauri + Svelte | https://github.com/QingJ01/Clyde |

**反复出现的"桌宠标准交互"（可直接借鉴）**：

- speech bubble 气泡作为主对话载体（而非独立聊天窗）
- 拖拽 + 双击/连击触发反应 + 右键上下文菜单 + 系统托盘（显隐/切角色/尺寸 S/M/L/勿扰）
- 前台 app 检测自动切表情/状态（编码 vs 写作 vs 摸鱼）
- 主动 nudge（久坐提醒）+ 随时间/闲置变化的 mood
- 透明置顶无边框窗 + 跨 Space/全屏浮动

### 1.2 陪伴 + 记忆/人格（商业，多数无实体）

闭源产品，看不到代码，但**记忆/人格的产品化做法**值得参考（对应"记忆是第一护城河"）：

- **Nomi AI**：公认记忆最强。对话流之外维护**结构化笔记层**，会主动指出矛盾（"你上周说喜欢徒步，今天又说讨厌户外"）。
- **Kindroid**：把记忆工具交给用户，可**手动编辑/curate** 记忆日志与角色设定。
- **Nyther**：**四层记忆架构**（短期上下文 / 中期 episode / 长期 traits / pgvector 语义检索）+ 13 人格 + 6 mood 一键切换。
- **Replika**：有可见的 **Memory tab**，情绪连续性强。
- **KAi**：隐私优先，抽取"理解"后 24h 删原始对话，只留沉淀。

**对界面/交互的启发**：记忆不应是黑盒。可做一个**可见的"记忆面板"**（Replika 的 Memory tab / Nomi 的结构化笔记），让用户看到/编辑 AI 记住了什么——既增强信任，也直接服务"禁止出现'我不记得'话术"的体验底线。

---

## 2. Codex Pets 机制拆解

OpenAI 在 **2026-05-02** 给 Codex 上线的官方功能：桌面悬浮一只像素风桌宠。

**关键定位**：不是装饰，是 **"agent 工作状态的可视化浮层（overlay）"**。解决的痛点是——让 agent 跑长任务后切去干别的，瞄一眼桌宠就知道它在干嘛、要不要介入，不必反复切回主窗口。

### 2.1 状态 → 动画映射（其交互核心）

桌宠用 **9 种动画状态**实时反映 agent 工作阶段：

| 精灵图行 | 状态 | 含义 |
| --- | --- | --- |
| idle | 待机 | 空闲等待 |
| running / running-left / running-right | 奔跑 | 任务执行中 |
| waiting | 等待 | 等用户输入 |
| waving | 挥手 | 完成、提醒来看 |
| jumping | 跳跃 | — |
| review | 审核 | 结果可供 review |
| failed | 失败 | 出错 |

气泡提示（thought bubble）：🔴 红色小时钟 = 等你审批命令；🟢 绿色对勾 = 任务完成可给新指令。

### 2.2 交互与开启

- 窗口：透明置顶、可拖拽到任意角落
- 开启：`Settings → Appearance → Pets`；或输入框 `/pet`；或 `Cmd/Ctrl + K` 命令面板
- 内置 8 款：默认蓝色 "Codex"、鸭子 "Dewey"、"Rocky"、恶搞 "BSOD" 等

### 2.3 自定义：hatch-pet skill

用 skill + 自然语言生成桌宠：

```bash
$skill-installer hatch-pet        # 安装技能
/hatch-pet                        # 或：$hatch-pet create a new pet inspired by my recent projects
```

### 2.4 包格式（已事实标准化）

一个 pet = `~/.codex/pets/<id>/` 下两个文件：

```
pet.json          # 清单：id / displayName / description / spritesheetPath
spritesheet.webp  # 透明 WebP 精灵图：9 行(状态) × 最多 8 列(帧)，单元格 192×208
```

```json
{
  "id": "codie",
  "displayName": "Codie",
  "description": "A tiny pixel robot companion for Codex Pet.",
  "spritesheetPath": "spritesheet.webp"
}
```

### 2.5 已长出的生态

- **petdex**（`crafter-station/petdex`）：Codex/CC/Gemini CLI 通用 pet 画廊 + CLI + 桌面浮窗，`npx petdex install <name>`
- **codex-pet.com**：600+ 社区 pet，`npx codex-pet-cli add <name>`
- **OpenPet**（`X-T-E-R/OpenPet`，中文）：本地桌宠 **runtime**，把 Codex 兼容格式抽象成独立运行时，暴露 **CLI / MCP / HTTP API** 供任意 agent 控制动作/气泡/事件。其"runtime + 控制 API"架构最值得参考。

---

## 3. 桌宠动画协议：能力边界与扩展

### 3.1 当前协议怎么做到"标识自己 + 对应动作"

- **"标识自己" 靠 `pet.json`**——但只有 4 个字段，**完全不描述动画**，只管"我是谁、图在哪"。
- **"对应动作" 不靠 json，靠 spritesheet 的"位置约定"**：行号 = 状态，且映射是 **runtime 硬编码死的**；pet 作者只是"往固定槽位填素材"。连帧数都隐式（从第 0 列取连续非空帧，尾部空格忽略）。

本质是 **"约定优于配置"**：协议表达力几乎全外包给一个**写死的状态枚举**，json 退化成纯元信息标签。

### 3.2 为什么它"够用"

前提是 **Codex 的事件集是封闭、固定、不增长的**（coding agent 生命周期：idle/running/waiting/review/failed/done）。在此前提下不需要"事件→动画"映射表（行号即状态 1:1），也不需要帧率/循环/优先级（可在 runtime 对每状态写死）。

### 3.3 "多事件对应多动画"——原生搞不定

| 需求 | 当前协议 | 卡在哪 |
| --- | --- | --- |
| 新增它不认识的事件（如"被夸"→害羞） | ❌ | 行号枚举 runtime 写死，第 10 行没人认 |
| 一事件对应多候选动画（随机挑、避免重复） | ❌ | 一状态只能绑一行 |
| 同一动画被多事件复用 | ❌ | 无"事件→动画"间接层，是 1:1 位置绑定 |
| 控制循环/单次/帧率/过渡/优先级打断 | ❌ | 协议无这些字段 |
| 多张图集、按需加载 | ❌ | 单 `spritesheet.webp` |

**根因**：把"事件、动画、播放语义"三者用"行号"这一个隐式维度压扁了。

### 3.4 扩展方向（草案，非定稿）

核心：`pet.json` 从"标签"升级为"声明动画 + 声明映射"，引入间接层。

```jsonc
{
  "id": "nia",
  "displayName": "Nia",
  "sheets": ["base.webp", "emotions.webp"],     // 多图集
  "clips": {                                     // 动画片段：显式声明，不靠行号
    "idle": { "sheet": "base.webp", "row": 0, "frames": 6, "fps": 8, "loop": true },
    "shy":  { "sheet": "emotions.webp", "row": 2, "frames": 5, "fps": 12, "loop": false },
    "wave": { "sheet": "base.webp", "row": 3, "frames": 4 }
  },
  "events": {                                     // 事件→动画映射（多对多 + 候选）
    "user_praised": { "clips": ["shy", "wave"], "pick": "random", "priority": 5 },
    "task_done":    { "clips": ["wave"], "priority": 3 }
  }
}
```

升级后多出的关键能力：clips 显式声明（摆脱行号硬编码，动画数任意）；events 间接层（天然支持一事件多动画/一动画多事件）；播放语义字段（loop/fps/priority，可再加 transition/interruptible）。代价：pet 作者需写声明，不能只"画对位置"。

### 3.5 对本项目的判断

- **本质差异**：Codex 事件集封闭（agent 生命周期）；agent-friend 是"朋友"，**情绪/事件开放且会持续增长**（害羞、生气、想你了、纪念日……）。
- 因此 Codex"固定行号约定"协议大概率**从第一天就不够用**，我们需要**可声明、带间接层**的协议。呼应 `coding-design`："识别最易变维度（事件/消息类型），给易变策略留扩展点"。
- **兼容不冲突**：我们的扩展协议可**向下兼容 Codex 格式**（把 Codex 9 行约定当成一个预置 profile），既能白嫖社区数百款 spritesheet 素材，又能在上层叠"开放事件→多动画"能力。
