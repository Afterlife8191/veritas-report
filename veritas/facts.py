"""The facts file: every published number, with an id and its derivation.

A *fact* is one computed number plus the computation that produced it. Facts are
the only currency the writing layer is allowed to spend: it may select facts,
order them and narrate them, but every number it prints has to be one of these,
referenced by id. That is what makes the report checkable by a machine instead of
by a careful reader.

Two artifacts come out of here:

``FactBook.facts``
    The complete, provenance-carrying set. The audit trail resolves against it.

``FactBook.pack()``
    A bounded, compact projection handed to the writer -- overall figures plus a
    shortlist of cuts. Bounded because feeding every metric x cut x period is
    both wasteful and worse for judgement; a projection rather than a filter
    because the pack states its own coverage, so what was left out stays visible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from .compute import ComputeResult, ReportPlan
from .numeric import format_value, normalize_digits
from .periods import COMPLETE_WEEK, DAILY, PARTIAL_WEEK, Period
from .records import PROV_ZERO_FILL, Record
from .registry import Metric, Registry
from .stats import (
    compare_to_baseline,
    contribution_share,
    delta,
    pct_change,
    rate_adequacy_flags,
    robust_score,
    two_proportion_z,
)

SCHEMA_VERSION = "1.0"

#: Units used by derived statistics, which need not match the metric's own unit.
UNIT_SCORE = "score"
UNIT_PERCENT = "percent"
#: The difference between two rates is in percentage points, not percent.
UNIT_POINTS = "pp"
UNIT_COUNT = "count"

PCT_PRECISION = 1
SCORE_PRECISION = 2

#: A day is offered to the writer as an anomaly candidate at or beyond this
#: score. It bounds the pack; it is not a significance claim and not a gate --
#: every daily fact stays in the full facts file either way.
ANOMALY_SCORE_THRESHOLD = 2.5

#: Cuts shown per metric per cut dimension, across all selection channels.
SHORTLIST_PER_DIMENSION = 4


@dataclass(frozen=True)
class Provenance:
    """How a fact was produced."""

    computation: str
    formula: str
    inputs: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Fact:
    id: str
    metric_id: str
    label: str
    statistic_key: str
    cut_dim: str | None
    cut_value: str | None
    period_type: str
    period_start: str
    period_end: str
    value: float | None
    unit: str
    display_precision: int
    display: str
    provenance: Provenance
    flags: tuple[str, ...] = ()

    @property
    def tolerance(self) -> float:
        return 0.5 * (10.0**-self.display_precision)

    @property
    def digits(self) -> str:
        """The published figure, reduced to sign and digits.

        Prose is bound against this rather than against ``value``: the report
        prints ``display``, so quoting a raw float, or a value that merely falls
        inside the tolerance band, would put a number on the page that the
        pipeline never published.
        """
        return normalize_digits(self.display)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["provenance"] = asdict(self.provenance)
        payload["flags"] = list(self.flags)
        payload["provenance"]["inputs"] = list(self.provenance.inputs)
        return payload

    def to_pack_dict(self) -> dict:
        """The writer sees values and labels, not derivations."""
        entry = {
            "id": self.id,
            "label": self.label,
            "value": self.value,
            "display": self.display,
            "unit": self.unit,
        }
        if self.flags:
            entry["flags"] = list(self.flags)
        return entry


@dataclass(frozen=True)
class ShortlistItem:
    metric_id: str
    cut_dim: str | None
    cut_value: str | None
    channels: tuple[str, ...]
    rank: int
    fact_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "metric_id": self.metric_id,
            "cut": "overall" if self.cut_dim is None else f"{self.cut_dim}={self.cut_value}",
            "selected_by": list(self.channels),
            "rank": self.rank,
            "fact_ids": list(self.fact_ids),
        }


@dataclass
class FactBook:
    run_id: str
    as_of: str
    generated_at: str
    week_start: str
    week_end: str
    partial_week: dict | None
    facts: dict[str, Fact] = field(default_factory=dict)
    shortlist: list[ShortlistItem] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    incomplete: bool = False

    def add(self, fact: Fact) -> Fact:
        if fact.id in self.facts:
            raise ValueError(f"duplicate fact id: {fact.id}")
        self.facts[fact.id] = fact
        return fact

    def get(self, fact_id: str) -> Fact | None:
        return self.facts.get(fact_id)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "as_of": self.as_of,
            "generated_at": self.generated_at,
            "week_in_review": {"start": self.week_start, "end": self.week_end},
            "partial_week": self.partial_week,
            "incomplete": self.incomplete,
            "coverage": self.coverage,
            "notes": self.notes,
            "shortlist": [item.to_dict() for item in self.shortlist],
            "facts": {fid: fact.to_dict() for fid, fact in sorted(self.facts.items())},
        }

    def pack(self) -> dict:
        """The bounded input handed to the writer."""
        pack_ids = self._pack_fact_ids()
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "week_in_review": {"start": self.week_start, "end": self.week_end},
            "partial_week": self.partial_week,
            "incomplete": self.incomplete,
            "coverage": self.coverage,
            "notes": self.notes,
            "shortlist": [item.to_dict() for item in self.shortlist],
            "facts": [self.facts[fid].to_pack_dict() for fid in pack_ids],
        }

    def pack_fact_ids(self) -> set[str]:
        return set(self._pack_fact_ids())

    def cut_labels(self, metric_id: str) -> set[str]:
        """Every segment label that exists for a metric, e.g. ``channel=email``.

        Drawn from the pack rather than from every computed fact: the writer can
        only honestly write about a segment it was shown, and the rendered report
        prints this string, so it must not become free text.
        """
        return {
            cut_label(fact.cut_dim, fact.cut_value)
            for fact_id in self._pack_fact_ids()
            for fact in (self.facts[fact_id],)
            if fact.metric_id == metric_id
        }

    def _pack_fact_ids(self) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for item in self.shortlist:
            for fid in item.fact_ids:
                if fid not in seen and fid in self.facts:
                    seen.add(fid)
                    ordered.append(fid)
        return ordered


def cut_label(cut_dim: str | None, cut_value: str | None) -> str:
    return "overall" if cut_dim is None else f"{cut_dim}={cut_value}"


def make_fact_id(metric_id: str, cut_dim, cut_value, period: Period, statistic_key: str) -> str:
    return f"{metric_id}/{cut_label(cut_dim, cut_value)}/{period.label}/{statistic_key}"


def _period_phrase(period: Period) -> str:
    if period.period_type == COMPLETE_WEEK:
        return f"week ending {period.end.isoformat()}"
    if period.period_type == PARTIAL_WEEK:
        return f"partial week {period.start.isoformat()}..{period.end.isoformat()}"
    return f"day {period.end.isoformat()}"


STATISTIC_LABELS = {
    "value": "value",
    "n_obs": "observations behind the value",
    "numerator": "numerator",
    "denominator": "denominator",
    "wow_delta": "change vs prior week (absolute)",
    "wow_pct": "change vs prior week (%)",
    "baseline_mean": "mean of the trailing 4-week baseline",
    "vs_baseline_delta": "change vs trailing baseline",
    "vs_baseline_pct": "change vs trailing baseline (%)",
    "screening_z": "screening z vs trailing baseline (ranking heuristic)",
    "rate_z": "two-proportion screening z vs prior week",
    "contribution_share_pct": "share of the overall week-over-week change (%)",
    "robust_score": "robust score vs same-weekday baseline",
    "weekday_median": "median of the same-weekday baseline",
    "robust_scale": "scale used by the robust score (MAD, floored)",
    "slice_baseline_mean": "mean of the same elapsed slice in prior weeks",
    "vs_slice_pct": "change vs the same elapsed slice of prior weeks (%)",
}


class _Builder:
    """Accumulates facts for one run."""

    def __init__(self, book: FactBook, registry: Registry) -> None:
        self.book = book
        self.registry = registry

    def emit(
        self,
        metric: Metric,
        record_key: tuple[str | None, str | None],
        period: Period,
        statistic_key: str,
        value: float | None,
        provenance: Provenance,
        unit: str | None = None,
        precision: int | None = None,
        flags: tuple[str, ...] = (),
    ) -> Fact:
        cut_dim, cut_value = record_key
        unit = unit if unit is not None else metric.unit
        precision = precision if precision is not None else metric.display_precision
        fact = Fact(
            id=make_fact_id(metric.id, cut_dim, cut_value, period, statistic_key),
            metric_id=metric.id,
            label=(
                f"{metric.title} | {cut_label(cut_dim, cut_value)} | "
                f"{_period_phrase(period)} | {STATISTIC_LABELS.get(statistic_key, statistic_key)}"
            ),
            statistic_key=statistic_key,
            cut_dim=cut_dim,
            cut_value=cut_value,
            period_type=period.period_type,
            period_start=period.start.isoformat(),
            period_end=period.end.isoformat(),
            value=value,
            unit=unit,
            display_precision=precision,
            display=format_value(value, unit, precision),
            provenance=provenance,
            flags=tuple(flags),
        )
        return self.book.add(fact)


def _series_records(result: ComputeResult) -> dict[tuple[str, str | None, str | None], dict[Period, Record]]:
    index: dict[tuple[str, str | None, str | None], dict[Period, Record]] = {}
    for record in result.records:
        index.setdefault(record.series_key, {})[record.period] = record
    return index


def _value_of(records: dict[Period, Record], period: Period) -> float | None:
    record = records.get(period)
    return None if record is None else record.value


def build_factbook(
    result: ComputeResult,
    registry: Registry,
    plan: ReportPlan,
    source_sha256: str,
    run_id: str,
) -> FactBook:
    """Turn canonical records plus statistics into the typed facts file."""
    book = FactBook(
        run_id=run_id,
        as_of=plan.as_of.isoformat(),
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        week_start=plan.week.start.isoformat(),
        week_end=plan.week.end.isoformat(),
        partial_week=(
            None
            if plan.partial is None
            else {"start": plan.partial.start.isoformat(), "end": plan.partial.end.isoformat()}
        ),
        incomplete=result.incomplete,
    )
    book.coverage = [
        {
            "metric_id": status.metric_id,
            "status": status.status,
            "required": status.required,
            "detail": status.detail,
        }
        for status in sorted(result.statuses, key=lambda s: s.metric_id)
    ]
    book.notes = [
        "screening_z and robust_score are ranking heuristics computed on short "
        "baselines, not calibrated significance tests.",
        f"source data sha256: {source_sha256}",
    ]

    builder = _Builder(book, registry)
    series = _series_records(result)
    usable = result.usable_metric_ids()

    for metric in registry.metrics:
        if metric.id not in usable:
            continue
        overall_key = (metric.id, None, None)
        overall_records = series.get(overall_key, {})
        overall_delta = _weekly_facts(builder, metric, (None, None), overall_records, plan, None)
        _daily_facts(builder, metric, (None, None), overall_records, plan)
        _partial_facts(builder, metric, (None, None), overall_records, plan)

        for cut_dim in metric.cuts:
            cut_values = sorted(
                key[2] for key in series if key[0] == metric.id and key[1] == cut_dim
            )
            for cut_value in cut_values:
                records = series[(metric.id, cut_dim, cut_value)]
                _weekly_facts(builder, metric, (cut_dim, cut_value), records, plan, overall_delta)
                _daily_facts(builder, metric, (cut_dim, cut_value), records, plan)
                _partial_facts(builder, metric, (cut_dim, cut_value), records, plan)

    book.shortlist = _build_shortlist(book, registry, plan, series, usable)
    return book


def _weekly_facts(
    builder: _Builder,
    metric: Metric,
    key: tuple[str | None, str | None],
    records: dict[Period, Record],
    plan: ReportPlan,
    overall_delta: float | None,
) -> float | None:
    """Emit the weekly comparison facts for one series; return its WoW delta."""
    week = plan.week
    current = records.get(week)
    if current is None:
        return None

    source_note = "aggregated from the source rows inside the period"
    value_fact = builder.emit(
        metric,
        key,
        week,
        "value",
        current.value,
        Provenance(
            computation="aggregate",
            formula=_aggregate_formula(metric),
            inputs=(f"source_rows:{week.start.isoformat()}..{week.end.isoformat()}",),
            note=source_note,
        ),
        flags=current.flags,
    )
    builder.emit(
        metric,
        key,
        week,
        "n_obs",
        current.n_obs,
        Provenance(
            computation="aggregate",
            formula=f"SUM({metric.n_obs_field}) over the period",
            inputs=(f"source_rows:{week.start.isoformat()}..{week.end.isoformat()}",),
        ),
        unit=UNIT_COUNT,
        precision=0,
    )

    baseline_periods = list(plan.week_baselines)
    prior = records.get(baseline_periods[0]) if baseline_periods else None
    prior_fact_id = None
    for baseline_period in baseline_periods:
        baseline_record = records.get(baseline_period)
        if baseline_record is None:
            continue
        fact = builder.emit(
            metric,
            key,
            baseline_period,
            "value",
            baseline_record.value,
            Provenance(
                computation="aggregate",
                formula=_aggregate_formula(metric),
                inputs=(
                    f"source_rows:{baseline_period.start.isoformat()}"
                    f"..{baseline_period.end.isoformat()}",
                ),
                note=source_note,
            ),
            flags=baseline_record.flags,
        )
        if prior_fact_id is None:
            prior_fact_id = fact.id

    prior_value = prior.value if prior else None
    wow_delta = delta(current.value, prior_value)
    builder.emit(
        metric,
        key,
        week,
        "wow_delta",
        wow_delta,
        Provenance(
            computation="wow_delta",
            formula="current - prior_week",
            inputs=tuple(x for x in (value_fact.id, prior_fact_id) if x),
        ),
        unit=UNIT_POINTS if metric.unit == UNIT_PERCENT else None,
    )
    builder.emit(
        metric,
        key,
        week,
        "wow_pct",
        pct_change(current.value, prior_value),
        Provenance(
            computation="wow_pct",
            formula="100 * (current - prior_week) / |prior_week|",
            inputs=tuple(x for x in (value_fact.id, prior_fact_id) if x),
        ),
        unit=UNIT_PERCENT,
        precision=PCT_PRECISION,
    )

    baseline_values = [_value_of(records, p) for p in baseline_periods]
    baseline_ids = tuple(
        make_fact_id(metric.id, key[0], key[1], p, "value")
        for p in baseline_periods
        if records.get(p) is not None
    )
    comparison = compare_to_baseline(current.value, baseline_values)
    builder.emit(
        metric,
        key,
        week,
        "baseline_mean",
        comparison.baseline_mean,
        Provenance(
            computation="baseline_mean",
            formula=f"mean of the {len(baseline_ids)} prior complete weeks",
            inputs=baseline_ids,
        ),
        flags=comparison.flags,
    )
    builder.emit(
        metric,
        key,
        week,
        "vs_baseline_pct",
        comparison.pct,
        Provenance(
            computation="vs_baseline_pct",
            formula="100 * (current - baseline_mean) / |baseline_mean|",
            inputs=(value_fact.id,) + baseline_ids,
        ),
        unit=UNIT_PERCENT,
        precision=PCT_PRECISION,
        flags=comparison.flags,
    )
    builder.emit(
        metric,
        key,
        week,
        "screening_z",
        comparison.screening_z,
        Provenance(
            computation="screening_z",
            formula="(current - baseline_mean) / baseline_sd  [sample sd, n-1]",
            inputs=(value_fact.id,) + baseline_ids,
            note="ranking heuristic on a 4-observation baseline, not a p-value",
        ),
        unit=UNIT_SCORE,
        precision=SCORE_PRECISION,
        flags=comparison.flags,
    )

    if metric.is_ratio:
        _rate_facts(builder, metric, key, current, prior, week, value_fact.id)

    if key[0] is not None and metric.is_additive:
        builder.emit(
            metric,
            key,
            week,
            "contribution_share_pct",
            contribution_share(wow_delta, overall_delta),
            Provenance(
                computation="contribution_share_pct",
                formula="100 * cut_wow_delta / overall_wow_delta",
                inputs=(
                    make_fact_id(metric.id, key[0], key[1], week, "wow_delta"),
                    make_fact_id(metric.id, None, None, week, "wow_delta"),
                ),
                note="additive statistics only; means and percentiles do not decompose",
            ),
            unit=UNIT_PERCENT,
            precision=PCT_PRECISION,
        )

    return wow_delta


def _rate_facts(
    builder: _Builder,
    metric: Metric,
    key: tuple[str | None, str | None],
    current: Record,
    prior: Record | None,
    week: Period,
    value_fact_id: str,
) -> None:
    # Adequacy is a k-of-n question, so it applies to rates only. A mean's
    # "numerator" is not a count of successes -- GMV is not a subset of orders --
    # and running the rule on it produces nonsense flags.
    adequacy = (
        rate_adequacy_flags(current.numerator, current.denominator or 0.0)
        if metric.statistic == "rate"
        else ()
    )
    for statistic_key, value in (
        ("numerator", current.numerator),
        ("denominator", current.denominator),
    ):
        builder.emit(
            metric,
            key,
            week,
            statistic_key,
            value,
            Provenance(
                computation="aggregate",
                formula=f"SUM({metric.numerator_field if statistic_key == 'numerator' else metric.denominator_field})",
                inputs=(f"source_rows:{week.start.isoformat()}..{week.end.isoformat()}",),
            ),
            unit=UNIT_COUNT,
            precision=0,
            flags=adequacy,
        )
    if metric.statistic != "rate":
        return
    z = (
        two_proportion_z(
            current.numerator, current.denominator or 0.0, prior.numerator, prior.denominator or 0.0
        )
        if prior is not None
        else None
    )
    builder.emit(
        metric,
        key,
        week,
        "rate_z",
        z,
        Provenance(
            computation="two_proportion_z",
            formula="(p1 - p2) / sqrt(p_pooled (1 - p_pooled) (1/n1 + 1/n2))",
            inputs=(value_fact_id,),
            note="screening score: repeated visitors make observations non-independent",
        ),
        unit=UNIT_SCORE,
        precision=SCORE_PRECISION,
        flags=adequacy,
    )


def _daily_facts(
    builder: _Builder,
    metric: Metric,
    key: tuple[str | None, str | None],
    records: dict[Period, Record],
    plan: ReportPlan,
) -> None:
    for day in plan.days:
        current = records.get(day)
        if current is None:
            continue
        baseline_periods = plan.day_baselines[day]
        baseline_values = [_value_of(records, p) for p in baseline_periods]
        baseline_ids = tuple(
            make_fact_id(metric.id, key[0], key[1], p, "value")
            for p in baseline_periods
            if records.get(p) is not None
        )
        value_fact = builder.emit(
            metric,
            key,
            day,
            "value",
            current.value,
            Provenance(
                computation="aggregate",
                formula=_aggregate_formula(metric),
                inputs=(f"source_rows:{day.end.isoformat()}",),
            ),
            flags=current.flags,
        )
        for baseline_period in baseline_periods:
            baseline_record = records.get(baseline_period)
            if baseline_record is None:
                continue
            fact_id = make_fact_id(metric.id, key[0], key[1], baseline_period, "value")
            if fact_id in builder.book.facts:
                continue
            builder.emit(
                metric,
                key,
                baseline_period,
                "value",
                baseline_record.value,
                Provenance(
                    computation="aggregate",
                    formula=_aggregate_formula(metric),
                    inputs=(f"source_rows:{baseline_period.end.isoformat()}",),
                ),
                flags=baseline_record.flags,
            )

        score = robust_score(current.value, baseline_values, metric.metric_type)
        builder.emit(
            metric,
            key,
            day,
            "weekday_median",
            score.baseline_median,
            Provenance(
                computation="weekday_median",
                formula=f"median of the {score.baseline_n} prior same-weekday values",
                inputs=baseline_ids,
            ),
            flags=score.flags,
        )
        builder.emit(
            metric,
            key,
            day,
            "robust_scale",
            score.scale,
            Provenance(
                computation="robust_scale",
                formula="max(MAD, floor); floor = sqrt(max(median,1)) for counts, "
                "0.01*|median| otherwise",
                inputs=baseline_ids,
                note="the floor may only push the scale up, never down",
            ),
            flags=score.flags,
        )
        builder.emit(
            metric,
            key,
            day,
            "robust_score",
            score.score,
            Provenance(
                computation="robust_score",
                formula="(value - weekday_median) / robust_scale",
                inputs=(value_fact.id,) + baseline_ids,
                note="same-weekday baseline; unscored when the baseline is all zero",
            ),
            unit=UNIT_SCORE,
            precision=SCORE_PRECISION,
            flags=score.flags,
        )


def _partial_facts(
    builder: _Builder,
    metric: Metric,
    key: tuple[str | None, str | None],
    records: dict[Period, Record],
    plan: ReportPlan,
) -> None:
    if plan.partial is None:
        return
    current = records.get(plan.partial)
    if current is None:
        return
    slice_values = [_value_of(records, p) for p in plan.partial_baselines]
    slice_ids = tuple(
        make_fact_id(metric.id, key[0], key[1], p, "value")
        for p in plan.partial_baselines
        if records.get(p) is not None
    )
    for slice_period in plan.partial_baselines:
        slice_record = records.get(slice_period)
        if slice_record is None:
            continue
        builder.emit(
            metric,
            key,
            slice_period,
            "value",
            slice_record.value,
            Provenance(
                computation="aggregate",
                formula=_aggregate_formula(metric),
                inputs=(
                    f"source_rows:{slice_period.start.isoformat()}"
                    f"..{slice_period.end.isoformat()}",
                ),
                note="same elapsed slice of an earlier week",
            ),
            flags=slice_record.flags,
        )
    value_fact = builder.emit(
        metric,
        key,
        plan.partial,
        "value",
        current.value,
        Provenance(
            computation="aggregate",
            formula=_aggregate_formula(metric),
            inputs=(
                f"source_rows:{plan.partial.start.isoformat()}..{plan.partial.end.isoformat()}",
            ),
            note="in-progress week, truncated at the last complete day",
        ),
        flags=current.flags,
    )
    comparison = compare_to_baseline(current.value, slice_values)
    builder.emit(
        metric,
        key,
        plan.partial,
        "slice_baseline_mean",
        comparison.baseline_mean,
        Provenance(
            computation="slice_baseline_mean",
            formula="mean of the same elapsed slice across prior weeks",
            inputs=slice_ids,
            note="never compared against a full week",
        ),
        flags=comparison.flags,
    )
    builder.emit(
        metric,
        key,
        plan.partial,
        "vs_slice_pct",
        comparison.pct,
        Provenance(
            computation="vs_slice_pct",
            formula="100 * (current - slice_baseline_mean) / |slice_baseline_mean|",
            inputs=(value_fact.id,) + slice_ids,
        ),
        unit=UNIT_PERCENT,
        precision=PCT_PRECISION,
        flags=comparison.flags,
    )


def _aggregate_formula(metric: Metric) -> str:
    if metric.statistic == "rate":
        return (
            f"100 * SUM({metric.numerator_field}) / SUM({metric.denominator_field}) "
            "over the period"
        )
    if metric.statistic == "mean":
        return f"SUM({metric.numerator_field}) / SUM({metric.denominator_field}) over the period"
    return f"SUM({metric.numerator_field}) over the period"


def _abs_or_zero(value: float | None) -> float:
    return abs(value) if value is not None else 0.0


def _build_shortlist(
    book: FactBook,
    registry: Registry,
    plan: ReportPlan,
    series: dict[tuple[str, str | None, str | None], dict[Period, Record]],
    usable: set[str],
) -> list[ShortlistItem]:
    """Select what the writer gets to see.

    Multiple channels, deliberately: ranking by one statistic would be a gate in
    disguise, and a brand-new cut has no z-score at all yet may be the story.
    """
    items: list[ShortlistItem] = []
    rank = 0

    for metric in registry.metrics:
        if metric.id not in usable:
            continue
        rank += 1
        items.append(
            ShortlistItem(
                metric_id=metric.id,
                cut_dim=None,
                cut_value=None,
                channels=("overall",),
                rank=rank,
                fact_ids=_series_pack_fact_ids(book, metric, None, None, plan),
            )
        )

        for cut_dim in metric.cuts:
            candidates = [
                key for key in series if key[0] == metric.id and key[1] == cut_dim
            ]
            channels: dict[tuple[str | None, str | None], set[str]] = {}

            def score_of(key, statistic_key: str) -> float:
                fact = book.get(make_fact_id(metric.id, key[1], key[2], plan.week, statistic_key))
                return _abs_or_zero(fact.value) if fact else 0.0

            by_z = sorted(candidates, key=lambda k: score_of(k, "screening_z"), reverse=True)
            for key in by_z[:2]:
                if score_of(key, "screening_z"):
                    channels.setdefault((key[1], key[2]), set()).add("screening_z")

            impact_key = "contribution_share_pct" if metric.is_additive else "wow_pct"
            by_impact = sorted(candidates, key=lambda k: score_of(k, impact_key), reverse=True)
            for key in by_impact[:2]:
                if score_of(key, impact_key):
                    channels.setdefault((key[1], key[2]), set()).add(impact_key)

            for key in candidates:
                records = series[key]
                current = records.get(plan.week)
                baselines = [records.get(p) for p in plan.week_baselines]
                if current is not None and current.provenance == PROV_ZERO_FILL:
                    channels.setdefault((key[1], key[2]), set()).add("disappeared")
                elif baselines and all(
                    b is not None and b.provenance == PROV_ZERO_FILL for b in baselines
                ):
                    channels.setdefault((key[1], key[2]), set()).add("newly_appeared")

            def selection_order(entry) -> tuple:
                (dim, value), reasons = entry
                z_fact = book.get(make_fact_id(metric.id, dim, value, plan.week, "screening_z"))
                return (-len(reasons), -_abs_or_zero(z_fact.value if z_fact else None), value or "")

            ordered = sorted(channels.items(), key=selection_order)
            for (dim, value), reasons in ordered[:SHORTLIST_PER_DIMENSION]:
                rank += 1
                items.append(
                    ShortlistItem(
                        metric_id=metric.id,
                        cut_dim=dim,
                        cut_value=value,
                        channels=tuple(sorted(reasons)),
                        rank=rank,
                        fact_ids=_series_pack_fact_ids(book, metric, dim, value, plan),
                    )
                )

    return items


WEEKLY_PACK_KEYS = (
    "value",
    "n_obs",
    "wow_delta",
    "wow_pct",
    "baseline_mean",
    "vs_baseline_pct",
    "screening_z",
    "numerator",
    "denominator",
    "rate_z",
    "contribution_share_pct",
)
PARTIAL_PACK_KEYS = ("value", "slice_baseline_mean", "vs_slice_pct")
DAILY_PACK_KEYS = ("value", "weekday_median", "robust_scale", "robust_score")


def _series_pack_fact_ids(
    book: FactBook,
    metric: Metric,
    cut_dim: str | None,
    cut_value: str | None,
    plan: ReportPlan,
) -> tuple[str, ...]:
    """Which of a series' facts travel into the writer's pack."""
    ids: list[str] = []

    def take(period: Period, keys) -> None:
        for statistic_key in keys:
            fact_id = make_fact_id(metric.id, cut_dim, cut_value, period, statistic_key)
            if fact_id in book.facts:
                ids.append(fact_id)

    take(plan.week, WEEKLY_PACK_KEYS)
    if plan.week_baselines:
        take(plan.week_baselines[0], ("value",))
    if plan.partial is not None:
        take(plan.partial, PARTIAL_PACK_KEYS)

    for day in plan.days:
        score = book.get(make_fact_id(metric.id, cut_dim, cut_value, day, "robust_score"))
        is_candidate = score is not None and (
            (score.value is not None and abs(score.value) >= ANOMALY_SCORE_THRESHOLD)
            or "new_event" in score.flags
        )
        if is_candidate:
            take(day, DAILY_PACK_KEYS)

    return tuple(dict.fromkeys(ids))


__all__ = [
    "ANOMALY_SCORE_THRESHOLD",
    "Fact",
    "FactBook",
    "Provenance",
    "ShortlistItem",
    "build_factbook",
    "cut_label",
    "make_fact_id",
]
