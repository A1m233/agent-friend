# personas/

本目录存放虚拟朋友的人设 markdown 文件。每个 `.md` 文件对应一个独立人设，加载时按 `persona_name` 选择（默认 `default`）。

## 阶段 1 约定

阶段 1 **只搭机制不填内容**——`default.md` 只写最低限度的身份信息（姓名 + 说话风格），不写：

- 行为准则
- 失忆话术禁令（明确否决，详见 design.md §4.4.4）
- 性格细节
- 复杂角色设定

人设的语义内容由用户后续想清楚后填入 markdown，**不需要改任何代码**。

## 加载方式

`MarkdownPromptBuilder` 通过 `importlib.resources` 从本目录读取，文件随 `agent` 模块打包发布。

```python
from agent.prompts import MarkdownPromptBuilder

builder = MarkdownPromptBuilder(persona_name="default")
system_prompt = builder.build()
```

## 未来扩展（M0.3+）

- `data/personas/{name}.md`（项目根 `data/`，gitignored）作为用户自定义 overlay，加载时优先于本目录
- 多人设切换（`--persona` CLI 参数）
- 动态拼接（时间感知、用户信息等）
