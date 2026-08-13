"""Source loading and aggregation into canonical records.

This layer stands in for "run each metric's query". It knows the shape of the
source and the calendar, and nothing about business meaning: which columns to sum
comes from the registry, so adding a metric never touches this file.

It is deliberately **fail-closed**. Malformed rows, a period the source does not
cover, or data that is behind the reporting window stop the metric (and the run,
if the metric is required) rather than being smoothed over. A guessed number is
worse than a missing one.
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .periods import (
    Period,
    daily_periods,
    partial_week,
    prior_weeks,
    same_weekday_baseline,
    week_in_review,
)
from .records import OVERALL, RESERVED_PREFIX, Record, build_record, materialize_grid
from .registry import Metric, Registry

DIMENSIONS = ("country", "channel")
MEASURES = ("sessions", "orders", "gmv")
DATE_COLUMN = "date"

#: Dimension values reach an LLM prompt and then a rendered document, so they are
#: constrained to a conservative vocabulary at the door. Anything outside it is a
#: hard failure rather than a silent strip: a sanitizer that quietly drops
#: characters hides whatever upstream defect put them there.
DIMENSION_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:&+/-]{0,63}")

STATUS_OK = "ok"
STATUS_INCOMPLETE = "incomplete"
STATUS_STALE = "stale"

DEFAULT_BASELINE_WEEKS = 4
#: Same-weekday observations behind a daily robust score. Eight rather than four:
#: a MAD computed on four points is unstable enough to make ordinary days look
#: extreme, which is exactly the false-positive the robust score exists to avoid.
DEFAULT_DAILY_BASELINE_DAYS = 8
DEFAULT_FRESHNESS_SLACK_DAYS = 1


class SourceError(ValueError):
    """The input data is unusable. Never recovered from, never guessed around."""


@dataclass(frozen=True)
class SourceRow:
    day: date
    dims: dict[str, str]
    measures: dict[str, float]


@dataclass(frozen=True)
class SourceData:
    path: Path
    sha256: str
    rows: tuple[SourceRow, ...]
    min_date: date
    max_date: date

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class ReportPlan:
    """Every period the run will compute, and how they relate."""

    as_of: date
    week: Period
    week_baselines: tuple[Period, ...]
    days: tuple[Period, ...]
    day_baselines: dict[Period, tuple[Period, ...]]
    partial: Period | None
    partial_baselines: tuple[Period, ...]

    @property
    def prior_week(self) -> Period:
        return self.week_baselines[0]

    def all_periods(self) -> list[Period]:
        periods: list[Period] = [self.week, *self.week_baselines, *self.days]
        for baselines in self.day_baselines.values():
            periods.extend(baselines)
        if self.partial is not None:
            periods.append(self.partial)
            periods.extend(self.partial_baselines)
        return sorted(set(periods))

    def coverage_span(self) -> tuple[date, date]:
        periods = self.all_periods()
        return min(p.start for p in periods), max(p.end for p in periods)


@dataclass(frozen=True)
class MetricStatus:
    metric_id: str
    status: str
    detail: str = ""
    required: bool = False

    @property
    def usable(self) -> bool:
        return self.status == STATUS_OK


@dataclass
class ComputeResult:
    records: list[Record] = field(default_factory=list)
    statuses: list[MetricStatus] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        """True when a required metric could not be computed cleanly."""
        return any(s.required and not s.usable for s in self.statuses)

    def usable_metric_ids(self) -> set[str]:
        return {s.metric_id for s in self.statuses if s.usable}


def _parse_measure(raw: str, column: str, line: int) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SourceError(f"line {line}: column {column!r} is not numeric") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise SourceError(f"line {line}: column {column!r} is not finite")
    if value < 0:
        raise SourceError(f"line {line}: column {column!r} is negative")
    return value


def load_source(path: Path) -> SourceData:
    """Read and validate the source file. Any defect raises :class:`SourceError`."""
    if not path.exists():
        raise SourceError(f"source file not found: {path}")

    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()

    rows: list[SourceRow] = []
    seen: set[tuple[date, tuple[str, ...]]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        expected = {DATE_COLUMN, *DIMENSIONS, *MEASURES}
        actual = set(reader.fieldnames or ())
        if actual != expected:
            raise SourceError(
                f"unexpected columns: got {sorted(actual)}, expected {sorted(expected)}"
            )
        for line, raw in enumerate(reader, start=2):
            try:
                day = date.fromisoformat((raw[DATE_COLUMN] or "").strip())
            except ValueError:
                raise SourceError(f"line {line}: bad date {raw[DATE_COLUMN]!r}") from None

            dims = {}
            for dim in DIMENSIONS:
                value = (raw[dim] or "").strip()
                if not value:
                    raise SourceError(f"line {line}: empty {dim!r}")
                if value.startswith(RESERVED_PREFIX):
                    raise SourceError(
                        f"line {line}: {dim!r} uses the engine-reserved "
                        f"{RESERVED_PREFIX!r} namespace"
                    )
                if not DIMENSION_VALUE.fullmatch(value):
                    # The offending value is deliberately not echoed.
                    raise SourceError(
                        f"line {line}: {dim!r} contains characters outside the "
                        "allowed label vocabulary"
                    )
                dims[dim] = value

            key = (day, tuple(dims[d] for d in DIMENSIONS))
            if key in seen:
                raise SourceError(f"line {line}: duplicate row for {key}")
            seen.add(key)

            measures = {m: _parse_measure(raw[m], m, line) for m in MEASURES}
            rows.append(SourceRow(day=day, dims=dims, measures=measures))

    if not rows:
        raise SourceError("source file has no data rows")

    return SourceData(
        path=path,
        sha256=digest,
        rows=tuple(rows),
        min_date=min(r.day for r in rows),
        max_date=max(r.day for r in rows),
    )


def build_plan(
    as_of: date,
    baseline_weeks: int = DEFAULT_BASELINE_WEEKS,
    daily_baseline_days: int = DEFAULT_DAILY_BASELINE_DAYS,
) -> ReportPlan:
    """Resolve the reporting calendar for a run."""
    week = week_in_review(as_of)
    days = tuple(daily_periods(week))
    partial = partial_week(as_of)
    return ReportPlan(
        as_of=as_of,
        week=week,
        week_baselines=tuple(prior_weeks(week, baseline_weeks)),
        days=days,
        day_baselines={d: tuple(same_weekday_baseline(d, daily_baseline_days)) for d in days},
        partial=partial,
        partial_baselines=tuple(prior_weeks(partial, baseline_weeks)) if partial else (),
    )


def _period_totals(
    rows_by_day: dict[date, list[SourceRow]], period: Period
) -> tuple[dict[tuple[str | None, str | None], dict[str, float]], int]:
    """Sum every measure for a period, per bucket, in one pass over its days."""
    totals: dict[tuple[str | None, str | None], dict[str, float]] = defaultdict(
        lambda: dict.fromkeys(MEASURES, 0.0)
    )
    row_count = 0
    for day in period.days:
        for row in rows_by_day.get(day, ()):
            row_count += 1
            buckets = [(None, None)] + [(dim, row.dims[dim]) for dim in DIMENSIONS]
            for bucket in buckets:
                target = totals[bucket]
                for measure, value in row.measures.items():
                    target[measure] += value
    return totals, row_count


def compute(
    source: SourceData,
    registry: Registry,
    plan: ReportPlan,
    freshness_slack_days: int = DEFAULT_FRESHNESS_SLACK_DAYS,
) -> ComputeResult:
    """Aggregate the source into canonical records for every metric in the plan."""
    result = ComputeResult()
    periods = plan.all_periods()

    rows_by_day: dict[date, list[SourceRow]] = defaultdict(list)
    for row in source.rows:
        rows_by_day[row.day].append(row)

    # Freshness: the source must reach the last day the report will speak about.
    required_through = plan.as_of - timedelta(days=1 + freshness_slack_days)
    stale = source.max_date < required_through
    stale_detail = (
        f"source ends {source.max_date.isoformat()}, "
        f"needs data through {required_through.isoformat()}"
    )

    # Coverage: the delivered day grid is checked against the expected one. A day
    # inside a reported period with no rows at all is missing data, not a quiet
    # day -- this source is a daily aggregate feed, so a day with no trade would
    # still deliver rows. Checking days rather than periods matters: a source
    # that stops one day early leaves every period non-empty while silently
    # truncating the partial week against baselines that are not truncated.
    totals_by_period: dict[Period, dict[tuple[str | None, str | None], dict[str, float]]] = {}
    for period in periods:
        totals_by_period[period], _ = _period_totals(rows_by_day, period)

    expected_days = {day for period in periods for day in period.days}
    missing_days = sorted(day for day in expected_days if not rows_by_day.get(day))

    for metric in registry.metrics:
        if stale:
            result.statuses.append(
                MetricStatus(metric.id, STATUS_STALE, stale_detail, metric.required)
            )
            continue
        if missing_days:
            detail = (
                f"{len(missing_days)} day(s) missing from the reported window, "
                f"starting {missing_days[0].isoformat()}"
            )
            result.statuses.append(
                MetricStatus(metric.id, STATUS_INCOMPLETE, detail, metric.required)
            )
            continue

        result.records.extend(_records_for_metric(metric, plan, totals_by_period))
        result.statuses.append(MetricStatus(metric.id, STATUS_OK, "", metric.required))

    return result


def _records_for_metric(
    metric: Metric,
    plan: ReportPlan,
    totals_by_period: dict[Period, dict[tuple[str | None, str | None], dict[str, float]]],
) -> list[Record]:
    periods = plan.all_periods()
    records: list[Record] = []

    for period in periods:
        totals = totals_by_period[period]
        records.append(build_record(metric, period, None, None, totals[(None, None)]))

    for cut_dim in metric.cuts:
        cut_records: list[Record] = []
        for period in periods:
            for (dim, value), sums in totals_by_period[period].items():
                if dim == cut_dim:
                    cut_records.append(build_record(metric, period, dim, value, sums))
        records.extend(materialize_grid(metric, cut_records, periods, cut_dim))

    return records


__all__ = [
    "ComputeResult",
    "MetricStatus",
    "ReportPlan",
    "SourceData",
    "SourceError",
    "SourceRow",
    "OVERALL",
    "STATUS_INCOMPLETE",
    "STATUS_OK",
    "STATUS_STALE",
    "build_plan",
    "compute",
    "load_source",
]
