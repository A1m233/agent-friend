# 公开 Guide 发布与续跑

## 固定顺序

1. 让已审阅的产品变化形成稳定产品提交 `P`。
2. 重新确认 `P` 与此前审阅的 dirty overlay 等价。
3. 修改公开 guide，把 `source.json` 锚定到 `P`，更新内容摘要。
4. 运行系统 Skill validator和项目 `verify`。
5. 在 `manual` 模式经单独 Git 授权、在 `merge-main-and-push` 模式使用工作流授权，于公开仓库形成提交 `G`。
6. 按当前模式的 push 授权推送 `G`，确认 origin 的 branch 或 tag 精确指向 `G`。
7. 检查固定 Guide repository 的稳定 releases，确定未被使用的下一单调 sequence；不得复用、覆盖或降低已经发布的 sequence。
8. 为 `G` 选择明确的产品兼容范围：与新产品能力绑定的知识以下界锁定到首个支持版本；只修正文案且旧宿主行为相同的发布可以保留更宽范围。上界取下一个已知不兼容边界，证据不足时停止，不用宽泛范围猜测。
9. 运行 `publish-release --release-sequence N --min-product-version X --max-product-version-exclusive Y`。命令先构建 deterministic ZIP/manifest，再创建 draft、上传 assets、publish，并复核 `immutable=true` 和两个 asset digest。
10. 确认 release tag、source commit、manifest 与 `G` 一致后，返回产品交付流程。日常 Guide 发布不再同步 builtin，也不产生后续 builtin pin 产品提交。

公开工作区、未提交内容或只存在于本地的 commit 不能作为 release 来源。桌面离线 builtin 只在
`package-baseline` 模式从已经发布的兼容 immutable release 刷新。

## 两仓预检

- 分别运行 `git status`，列出全部修改、删除和未跟踪路径。
- 发现无法确认属于本次流程的路径时暂停；不得自行纳入或排除。
- 公开仓库必须 clean 且 `HEAD == G` 才能构建或发布 release。
- 构建前确认来源目录没有 symlink、junction 或越界路径。
- 仓库必须已经启用 GitHub release immutability；首个发布前由有 admin 权限的维护者完成该一次性设置。

## 部分完成续跑

| 状态 | 继续动作 |
| --- | --- |
| 公开内容有未提交修改 | 重新验证并确认提交范围 |
| `G` 已提交但 origin 不可见 | 按当前模式授权推送并确认；不先 sync |
| `G` 已推送但尚无 release | 复核 sequence/兼容范围后从 `G` 构建并发布 |
| draft 已创建且 assets 完整 | 核对本地摘要与 draft assets 后 publish；不另建同 sequence release |
| release 已发布且 immutable/digest/commit 一致 | 不重复发布，直接返回产品交付流程 |
| release 已发布但不是 immutable 或摘要不符 | 停止；不得覆盖 assets，修复设置后使用更高新 sequence 发布 |
| 公开 HEAD 在 `G` 后还有修改 | 先确认要发布新提交还是恢复干净范围 |
| 任一仓库有来源不明 dirty 文件 | 暂停对应 Git 操作并列精确路径 |

若 `G` 已推送而后续失败，不删除、不 amend、不 force-push `G`。已经发布的 release 不删除、不改 tag
或 assets；问题版本通过更高 sequence 的新 immutable release 撤回或回退。
