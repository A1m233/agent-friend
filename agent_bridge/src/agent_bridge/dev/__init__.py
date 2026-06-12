"""014 · dev / 测试期工具子包（**不入** wheel 打包产物）。

子模块：

- :mod:`agent_bridge.dev.fire_source` — dev 端点 ``POST /dev/fire-source``
  立即触发指定 EventSource（仅 ``settings.dev_mode=True`` 时挂载）
- :mod:`agent_bridge.dev.push_subscribe` — 命令行 CLI，订阅 ``/push/subscribe``
  美化打印 envelope（M14.7 落地）

详见 docs/requirements/014-engine-main-loop-and-bridge-push/design.md §6 + §9。
"""
