"""Calendar arithmetic for reporting periods.

Two rules drive everything here:

1. Weeks run **Sunday -> Saturday**. Python's ``date.weekday()`` is Monday-based,
   so every week boundary goes through :func:`week_start` rather than being
   open-coded.
2. The **current day is never reported**. Same-day data is still arriving, so the
   last day any period may include is ``as_of - 1``. A partial week is compared
   against the *same elapsed slice* of earlier weeks, never against a full week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DAYS_IN_WEEK = 7

COMPLETE_WEEK = "complete_week"
PARTIAL_WEEK = "partial_week"
DAILY = "daily"


@dataclass(frozen=True, order=True)
class Period:
    """A closed date interval ``[start, end]`` with a declared type."""

    period_type: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"period ends before it starts: {self.start}..{self.end}")

    @property
    def days(self) -> list[date]:
        span = (self.end - self.start).days + 1
        return [self.start + timedelta(days=i) for i in range(span)]

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        return f"{self.period_type}:{self.end.isoformat()}"

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def shifted_weeks(self, weeks: int) -> "Period":
        """The same weekday-aligned interval ``weeks`` weeks earlier/later."""
        offset = timedelta(days=DAYS_IN_WEEK * weeks)
        return Period(self.period_type, self.start + offset, self.end + offset)


def week_start(day: date) -> date:
    """The Sunday that opens ``day``'s week."""
    days_since_sunday = (day.weekday() + 1) % DAYS_IN_WEEK
    return day - timedelta(days=days_since_sunday)


def week_of(day: date) -> Period:
    """The complete Sunday..Saturday week containing ``day``."""
    start = week_start(day)
    return Period(COMPLETE_WEEK, start, start + timedelta(days=DAYS_IN_WEEK - 1))


def last_complete_day(as_of: date) -> date:
    """The most recent day whose data is settled: the day before ``as_of``."""
    return as_of - timedelta(days=1)


def week_in_review(as_of: date) -> Period:
    """The last week that finished before ``as_of``."""
    latest = last_complete_day(as_of)
    current = week_of(latest)
    if current.end == latest:
        return current
    return current.shifted_weeks(-1)


def partial_week(as_of: date) -> Period | None:
    """The in-progress week, or ``None`` when no day of it has completed.

    ``None`` is a supported state, not an error: on the day after a Saturday the
    new week has no completed days at all. Callers must handle both shapes.
    """
    latest = last_complete_day(as_of)
    current = week_of(latest)
    if current.end == latest:
        return None
    return Period(PARTIAL_WEEK, current.start, latest)


def prior_weeks(period: Period, count: int) -> list[Period]:
    """The ``count`` weekday-aligned periods immediately before ``period``.

    Most recent first. Works for complete and partial weeks alike, which is what
    makes an equal-elapsed comparison possible for the in-progress week.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    return [period.shifted_weeks(-(i + 1)) for i in range(count)]


def daily_periods(week: Period) -> list[Period]:
    """One single-day period per day of ``week``."""
    return [Period(DAILY, day, day) for day in week.days]


def same_weekday_baseline(day_period: Period, count: int) -> list[Period]:
    """The ``count`` most recent same-weekday days before ``day_period``.

    A plain trailing window would compare a Tuesday against weekend days and
    flag every structurally different weekday as an anomaly.
    """
    return [day_period.shifted_weeks(-(i + 1)) for i in range(count)]
