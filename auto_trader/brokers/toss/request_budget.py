"""Opt-in deadline for maintenance reads; ordinary requests keep their policy."""
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic


class RequestBudgetExceeded(Exception):
    """Yield maintenance work, without treating a partial response as evidence."""


current_budget = ContextVar('toss_request_budget', default=None)


class RequestBudget:
    def __init__(self, seconds, pages):
        if seconds <= 0 or pages < 1:
            raise ValueError('A positive maintenance budget is required')
        self.started = monotonic()
        self.seconds = seconds
        self.deadline = self.started + seconds
        self.max_pages = pages
        self.pages = 0
        self.requests = 0
        self.wait_seconds = 0.0
        self.last_error = None

    def remaining(self):
        value = self.deadline - monotonic()
        if value <= 0:
            raise RequestBudgetExceeded('time_budget')
        return value

    def page(self):
        self.remaining()
        if self.pages >= self.max_pages:
            raise RequestBudgetExceeded('page_budget')
        self.pages += 1

    @contextmanager
    def activate(self):
        token = current_budget.set(self)
        try:
            yield
        finally:
            current_budget.reset(token)


def check_wait(seconds):
    budget = current_budget.get()
    if budget is not None and seconds >= budget.remaining():
        raise RequestBudgetExceeded('wait_exceeds_time_budget')


def request_wait(waiter, seconds):
    check_wait(seconds)
    started = monotonic()
    try:
        waiter.wait(seconds)
    finally:
        budget = current_budget.get()
        if budget is not None:
            budget.wait_seconds += monotonic() - started


@contextmanager
def request_lock(lock):
    budget = current_budget.get()
    if budget is None:
        lock.acquire()
    elif not lock.acquire(timeout=budget.remaining()):
        raise RequestBudgetExceeded('lock_time_budget')
    try:
        yield
    finally:
        lock.release()
