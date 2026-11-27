"""Content-free cost accounting for scoped reads (journal I/O is logged separately)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter

from ..file_observation import _access_caller
from ..store_types import SourceVersion

logger = logging.getLogger(__name__)


@dataclass
class ReadCost:
    mode: str
    version: SourceVersion | None = None
    rows: int = 0
    body_chars: int = 0
    sql_steps_lower_bound: int = 0
    requested_ids: int = 0
    requested_limit: int = 0


@contextmanager
def observe_read(session_id: str, mode: str) -> Iterator[ReadCost]:
    cost = ReadCost(mode)
    started = perf_counter()
    outcome = "success"
    caller = _access_caller()
    try:
        yield cost
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        with suppress(Exception):
            logger.debug(
                "session_read mode=%s caller=%s session_key=%s version=%s rows=%d body_chars=%d sql_steps_lower_bound=%d requested_ids=%d requested_limit=%d elapsed_ms=%.3f outcome=%s",
                cost.mode,
                caller,
                sha256(session_id.encode()).hexdigest()[:16],
                cost.version,
                cost.rows,
                cost.body_chars,
                cost.sql_steps_lower_bound,
                cost.requested_ids,
                cost.requested_limit,
                (perf_counter() - started) * 1000,
                outcome,
            )
