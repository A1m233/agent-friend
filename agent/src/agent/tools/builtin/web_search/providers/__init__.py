"""``web_search.providers`` —— 具体 provider 实现集中归位。

每个 provider 占一个文件（如 :mod:`.tavily`）。本期唯一实现是
:class:`.tavily.TavilyProvider`；未来加 Bocha / 智谱 / SearXNG 等通过新增
同级文件接入。

provider 实现统一遵循 :class:`agent.tools.builtin.web_search.WebSearchProvider`
Protocol，对外被 :func:`agent.tools.make_default_registry` 工厂选择消费。
"""
