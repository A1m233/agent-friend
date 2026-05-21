"""``agent.tools.builtin`` —— 引擎自带的内置工具集。

每个内置工具占一个子包（如 :mod:`agent.tools.builtin.web_search`）。
对外仅通过 :func:`agent.tools.make_default_registry` 工厂消费，本子包不直接
暴露具体工具类到 ``agent.tools`` 公共 API——保持 web_search 等工具的实现
细节（如 provider 切换）对引擎层完全透明。

详见 docs/requirements/005-engine-tool-calling-and-web-search/design.md §4.7。
"""
