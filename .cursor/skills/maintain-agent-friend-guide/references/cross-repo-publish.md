# 跨仓发布与续跑

## 固定顺序

1. 让已审阅的产品变化形成稳定产品提交 `P`。
2. 重新确认 `P` 与此前审阅的 dirty overlay 等价。
3. 修改公开 guide，把 `source.json` 锚定到 `P`，更新内容摘要。
4. 运行系统 Skill validator和项目 `verify`。
5. 在 `manual` 模式经单独 Git 授权、在 `merge-main-and-push` 模式使用工作流授权，于公开仓库形成提交 `G`。
6. 按当前模式的 push 授权推送 `G`，确认 origin 的 branch 或 tag 精确指向 `G`。
7. 在产品仓库运行 `sync --guide-commit G`。
8. 验证 builtin、manifest、局部测试、完整检查和 package-sensitive frozen runtime。
9. 按当前模式的 Git 授权形成包含 builtin pin 的后续产品提交 `B`。

公开工作区、未提交内容或只存在于本地的 commit 不能作为 builtin 来源。

## 两仓预检

- 分别运行 `git status`，列出全部修改、删除和未跟踪路径。
- 发现无法确认属于本次流程的路径时暂停；不得自行纳入或排除。
- 公开仓库必须 clean 且 `HEAD == G` 才能 sync。
- `sync` 前确认来源目录没有 symlink、junction 或越界路径。

## 部分完成续跑

| 状态 | 继续动作 |
| --- | --- |
| 公开内容有未提交修改 | 重新验证并确认提交范围 |
| `G` 已提交但 origin 不可见 | 按当前模式授权推送并确认；不先 sync |
| `G` 已发布但 builtin pin 旧提交 | 直接以精确 `G` sync |
| builtin 已生成但未提交 | verify 后继续产品仓库提交范围确认 |
| manifest 已 pin `G` 且摘要一致 | 不重复复制或发布，只补验证/提交 |
| 公开 HEAD 在 `G` 后还有修改 | 先确认要发布新提交还是恢复干净范围 |
| 任一仓库有来源不明 dirty 文件 | 暂停对应 Git 操作并列精确路径 |

若 `G` 已推送而后续失败，不删除、不 amend、不 force-push `G`；修复后从 `G` 继续。
