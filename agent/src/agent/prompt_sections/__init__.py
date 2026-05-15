"""``agent.prompt_sections`` 子包：随包发布的默认 section markdown 资源。

本子包**只放数据**（``*.md`` 资源），无 Python 代码逻辑。``__init__.py``
存在仅是为了让目录成为 Python 包，从而支持 :func:`importlib.resources.files`
寻址。

加载逻辑见 :mod:`agent.system_prompt.defaults`。
"""
