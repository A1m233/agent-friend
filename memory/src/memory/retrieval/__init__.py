"""记忆读路径（召回 + 打分 + 渲染）。

详见 docs/requirements/008-engine-memory/design.md §6。
"""

from memory.contracts import GateMode

from .pinned_gate import pinned_gate
from .renderer import Renderer
from .scoring import ScoreWeights, rank
from .strategy import Candidate, KeywordRetrieval, RetrievalStrategy

__all__ = [
    "Candidate",
    "GateMode",
    "KeywordRetrieval",
    "Renderer",
    "RetrievalStrategy",
    "ScoreWeights",
    "pinned_gate",
    "rank",
]
