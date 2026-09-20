"""Cooperative deadlines for CPU work running outside the event loop."""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event


class SearchStopped(Exception):
    pass


@dataclass
class SearchBudget:
    deadline: float
    cancelled: Event = field(default_factory=Event)

    def check(self) -> None:
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise SearchStopped("Search cancelled or timed out")


current_budget: ContextVar[SearchBudget | None] = ContextVar("search_budget", default=None)


def check_search() -> None:
    budget = current_budget.get()
    if budget is not None:
        budget.check()
