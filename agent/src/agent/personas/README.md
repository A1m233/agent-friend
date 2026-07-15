# personas/

本目录存放随应用发布的内置人格基线。当前产品只支持一个人格，`default.md` 是首次初始化以及重置人格时使用的出厂内容。

## 内容边界

`default.md` 描述用户可以理解和调整的稳定人格维度，包括：

- 身份与关系定位
- 性格与价值取向
- 相处和情绪回应方式
- 交流节奏与说话风格
- 用于校准上述特征的少量典型回应

项目级安全约束、工具规则、记忆一致性和通用输出格式不属于人格，不应写入这里；这些内容由 `prompt_sections/` 下对应的独立 section 负责。

## 加载方式

`EffectivePersonaStore` 通过 `importlib.resources` 读取内置基线，并在首次使用时把去除 frontmatter 后的正文写入用户数据目录。正式对话和人格编辑统一读取这份用户文件；用户删除文件或执行重置后，再从 `default.md` 恢复。

```python
from agent.personas import EffectivePersonaStore

store = EffectivePersonaStore()
persona = store.load()
```

## 用户文件

用户实际编辑的是 `agent.paths.personas_dir()` 下唯一人格对应的 `persona.md`。路径必须通过 `agent.paths` 获取，不要在调用方自行拼接平台目录。
