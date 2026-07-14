"""020 兼容入口；实现已迁移到 ``memory.search.time_parser``。"""

from memory.search.time_parser import Bias, parse_time_expression

__all__ = ["Bias", "parse_time_expression"]
