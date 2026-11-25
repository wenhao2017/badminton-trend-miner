"""A request-scoped limit for optional polling, including retries and key rotation."""
from contextlib import contextmanager
from contextvars import ContextVar


class PollBudgetExceeded(RuntimeError):
    pass


_reservation = ContextVar("optional_poll_reservation", default=None)


@contextmanager
def optional_poll_budget(reserve):
    token = _reservation.set(reserve)
    try:
        yield
    finally:
        _reservation.reset(token)


def reserve_optional_request():
    reserve = _reservation.get()
    if reserve is not None and not reserve():
        raise PollBudgetExceeded("Optional live polling request budget exhausted or unavailable")
