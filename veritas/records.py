"""The canonical record -- the contract every metric reduces to.

One row per ``(metric, cut, period)``. Everything downstream reads this shape and
nothing else, which is why the statistics layer never needs to know what a metric
means.

Zero / null / missing semantics are part of the contract:

* A period with no activity is ``value = 0``, **present**. A *missing* record
  means missing coverage, never zero.
* A ratio with ``denominator == 0`` is ``value = None`` -- never ``0``, never a
  division error. No exposure is not a zero rate.
* Cuts seen in the baseline but absent now are zero-filled **type-aware**:
  additive statistics get ``0``; ratios and means get ``None``. A generic ``0``
  would fabricate a 0% conversion rate out of no traffic.
* Every zero-filled record is stamped ``provenance = "zero_fill"``. Without it a
  filled ``value = 0`` is indistinguishable from a genuine zero-activity period,
  and "this cut disappeared" stops being decidable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .periods import Period
from .registry import Metric

PROV_QUERY = "query"
PROV_ZERO_FILL = "zero_fill"

OVERALL = "overall"

#: Cut values in this namespace are reserved for the engine. Values arriving from
#: the source data may never match it.
RESERVED_PREFIX = "__"


@dataclass(frozen=True)
class Record:
    metric_id: str
    metric_type: str
    statistic: str
    grain: str  # "overall" | "cut"
    cut_dim: str | None
    cut_value: str | None
    period: Period
    value: float | None
    numerator: float
    denominator: float | None
    n_obs: float
    provenance: str = PROV_QUERY
    flags: tuple[str, ...] = ()

    @property
    def series_key(self) -> tuple[str, str | None, str | None]:
        """Identifies the time series this record belongs to."""
        return (self.metric_id, self.cut_dim, self.cut_value)

    @property
    def cut_label(self) -> str:
        if self.grain == OVERALL:
            return OVERALL
        return f"{self.cut_dim}:{self.cut_value}"

    def with_flags(self, *flags: str) -> "Record":
        return replace(self, flags=tuple(dict.fromkeys(self.flags + flags)))


def build_record(
    metric: Metric,
    period: Period,
    cut_dim: str | None,
    cut_value: str | None,
    totals: dict[str, float],
    provenance: str = PROV_QUERY,
) -> Record:
    """Turn summed source fields into one canonical record.

    ``totals`` holds raw column sums for the period and cut. The ratio/None rules
    live here so that every producer of records obeys them identically.
    """
    numerator = float(totals.get(metric.numerator_field, 0.0))
    denominator = (
        float(totals.get(metric.denominator_field, 0.0)) if metric.is_ratio else None
    )
    n_obs = float(totals.get(metric.n_obs_field, 0.0))

    flags: tuple[str, ...] = ()
    if metric.is_ratio:
        if denominator == 0:
            value = None
            flags += ("no_exposure",)
        else:
            value = numerator / denominator
            if metric.unit == "percent":
                value *= 100.0
    else:
        value = numerator

    return Record(
        metric_id=metric.id,
        metric_type=metric.metric_type,
        statistic=metric.statistic,
        grain=OVERALL if cut_dim is None else "cut",
        cut_dim=cut_dim,
        cut_value=cut_value,
        period=period,
        value=value,
        numerator=numerator,
        denominator=denominator,
        n_obs=n_obs,
        provenance=provenance,
        flags=flags,
    )


def zero_filled(metric: Metric, period: Period, cut_dim: str, cut_value: str) -> Record:
    """A type-aware filler for a ``(cut, period)`` the source never delivered."""
    record = build_record(metric, period, cut_dim, cut_value, {}, provenance=PROV_ZERO_FILL)
    if not metric.is_additive:
        record = replace(record, value=None)
    return record.with_flags("zero_filled")


def materialize_grid(
    metric: Metric,
    records: list[Record],
    periods: list[Period],
    cut_dim: str,
) -> list[Record]:
    """Complete the ``(cut universe) x (periods)`` grid for one cut dimension.

    The cut universe is the union of values observed across *all* periods, so a
    cut that vanished this week still gets a record -- with ``provenance =
    "zero_fill"``, which is what makes its disappearance a signal rather than an
    absence. Runs after the source rows are validated, never before.
    """
    present = {(r.cut_value, r.period) for r in records}
    universe = sorted({r.cut_value for r in records if r.cut_value is not None})
    filled = list(records)
    for cut_value in universe:
        for period in periods:
            if (cut_value, period) not in present:
                filled.append(zero_filled(metric, period, cut_dim, cut_value))
    return filled
