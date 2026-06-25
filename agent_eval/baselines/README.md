# agent_eval / baselines

这里存放被明确标记为 baseline 的 agent 行为契约评测结果。生成方式：

```bash
./scripts/agent-eval/run.sh --baseline --note "before prompt change"
```

baseline 与 `../reports/` 使用同一套 JSON schema，但语义更强：它是后续对比的锚点。只有值得长期对照的运行结果才放这里，避免被临时 smoke / 小规模调参结果污染。

约定：

- baseline 文件随代码入 git。
- 不要为了“清爽”批量删除旧 baseline。
- `working_tree_dirty=true` 的 baseline 要谨慎解读。
- C2 结果受 `TAVILY_API_KEY` 是否配置影响，报告里的 `run.tools` 会记录当时工具可用性。
