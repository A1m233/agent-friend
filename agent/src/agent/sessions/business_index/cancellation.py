"""Keep cancellation distinguishable from a disposable cache's I/O failures."""

from ..query import CancelCheck


def sticky_cancellation(check: CancelCheck) -> CancelCheck:
    if check is None:
        return None
    failures: list[BaseException] = []

    def checked() -> None:
        if failures:
            raise failures[0]
        try:
            check()
        except BaseException as exc:
            failures.append(exc)
            raise

    return checked
