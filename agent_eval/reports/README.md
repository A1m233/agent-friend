# agent_eval / reports

默认 `./scripts/agent-eval/run.sh` 会把结果写到这里。这里的 JSON 是一次正式运行证据，适合随代码入 git，便于之后回看失败 case 的 query、tool calls、final text 和运行条件。

注意：

- `working_tree_dirty=true` 时，`git_commit` 不能完整代表当时实际代码状态。
- 小规模运行（例如 `--limit-cases 1`）可以放这里，但不能当作全量质量指标。
- 不要手动编辑 JSON；需要修正时重新运行生成新文件。

如果一次运行要作为后续对比锚点，请加 `--baseline` 写到 `../baselines/`。
