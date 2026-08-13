"""Screening statistics. Pure functions, no I/O, no judgement.

This layer computes *every* comparison for *every* series and decides nothing.
Salience is somebody else's job -- these numbers exist to support that decision,
not to gate it.

Three honesty rules are baked in and stated wherever the numbers are published:

* The weekly baseline is four observations, so ``screening_z`` is a **ranking
  heuristic**, not a calibrated p-value. It is named ``screening_z`` everywhere
  for exactly that reason.
* Day-over-day comparison uses a **same-weekday** baseline with median/MAD.
  A trailing 28-day window would flag every structurally different weekday, and a
  mean/sd would let one earlier outlier mask this week's spike.
* A scale floor is applied as ``max(observed, floor)`` -- never substitution.
  Replacing a genuinely wide scale with a smaller floor would shrink the
  denominator and manufacture a spike.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Fewer baseline observations than this and the comparison is flagged as thin.
MIN_BASELINE_POINTS = 3

#: Validity conditions for the normal approximation behind the two-proportion z.
RATE_MIN_DENOMINATOR = 30
RATE_MIN_CELL = 5

#: Relative floor for the robust scale of a continuous metric. Scale-free, so it
#: reads the same in dollars as in seconds. The constant is arbitrary; reaching
#: this branch at all is itself the signal.
CONTINUOUS_SCALE_FLOOR_FRACTION = 0.01

#: Consistency constant making the MAD an estimator of the standard deviation
#: for normally distributed data (1 / 0.6745). Without it a robust score is on an
#: arbitrary scale roughly 1.5x hotter than a z, and every ordinary Tuesday looks
#: like an anomaly.
MAD_TO_SIGMA = 1.4826


def delta(current: float | None, prior: float | None) -> float | None:
    """``current - prior``, or ``None`` when either side is missing."""
    if current is None or prior is None:
        return None
    return current - prior


def pct_change(current: float | None, prior: float | None) -> float | None:
    """Percent change from ``prior`` to ``current``.

    ``None`` when either side is missing or when ``prior`` is zero: growth from
    nothing is not a percentage, and reporting one would invent a number.
    """
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior) * 100.0


def mean(values: list[float]) -> float | None:
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def sample_sd(values: list[float]) -> float | None:
    """Sample standard deviation (``n - 1``). ``None`` for fewer than 2 points."""
    usable = [v for v in values if v is not None]
    if len(usable) < 2:
        return None
    avg = sum(usable) / len(usable)
    variance = sum((v - avg) ** 2 for v in usable) / (len(usable) - 1)
    return math.sqrt(variance)


def median(values: list[float]) -> float | None:
    usable = sorted(v for v in values if v is not None)
    if not usable:
        return None
    mid = len(usable) // 2
    if len(usable) % 2:
        return float(usable[mid])
    return (usable[mid - 1] + usable[mid]) / 2.0


def median_absolute_deviation(values: list[float]) -> float | None:
    """Unscaled MAD: ``median(|x - median(x)|)``."""
    centre = median(values)
    if centre is None:
        return None
    return median([abs(v - centre) for v in values if v is not None])


@dataclass(frozen=True)
class BaselineComparison:
    """A value against its trailing baseline."""

    baseline_mean: float | None
    baseline_sd: float | None
    baseline_n: int
    delta: float | None
    pct: float | None
    screening_z: float | None
    flags: tuple[str, ...] = ()


def compare_to_baseline(current: float | None, baseline: list[float | None]) -> BaselineComparison:
    """Compare ``current`` against the mean of its trailing baseline."""
    usable = [v for v in baseline if v is not None]
    flags: tuple[str, ...] = ()
    if len(usable) < MIN_BASELINE_POINTS:
        flags += ("thin_baseline",)
    if not usable:
        return BaselineComparison(None, None, 0, None, None, None, flags + ("no_baseline",))

    avg = mean(usable)
    sd = sample_sd(usable)
    z: float | None = None
    if current is None:
        flags += ("no_current_value",)
    elif sd is None or sd == 0:
        flags += ("degenerate_baseline_scale",)
    else:
        z = (current - avg) / sd

    return BaselineComparison(
        baseline_mean=avg,
        baseline_sd=sd,
        baseline_n=len(usable),
        delta=delta(current, avg),
        pct=pct_change(current, avg),
        screening_z=z,
        flags=flags,
    )


def two_proportion_z(k1: float, n1: float, k2: float, n2: float) -> float | None:
    """Screening z for two proportions ``k1/n1`` vs ``k2/n2``.

    ``None`` when either sample is empty or the pooled proportion is degenerate.
    Repeated visitors make observations non-independent, so this is a screening
    score and not an exact test -- the pack labels it as such.
    """
    if n1 <= 0 or n2 <= 0:
        return None
    pooled = (k1 + k2) / (n1 + n2)
    if pooled in (0.0, 1.0):
        return None
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    return (k1 / n1 - k2 / n2) / se


def rate_adequacy_flags(numerator: float, denominator: float) -> tuple[str, ...]:
    """Adequacy flags for a rate, evaluated on the rate's own k and n.

    A flat ``n >= 30`` rule waves through exactly the cases this exists to catch:
    a 1% rate on n = 100 has ``np = 1`` and is wildly unstable, yet clears any
    n-only threshold.
    """
    flags: tuple[str, ...] = ()
    if denominator <= 0:
        return ("no_exposure",)
    if denominator < RATE_MIN_DENOMINATOR:
        flags += ("small_denominator",)
    if min(numerator, denominator - numerator) < RATE_MIN_CELL:
        flags += ("small_cell",)
    return flags


def contribution_share(cut_delta: float | None, overall_delta: float | None) -> float | None:
    """Share of the overall change attributable to one cut, in percent.

    Only meaningful for additive statistics; callers must not decompose a mean or
    a percentile, because cut-level values of those do not roll up.
    """
    if cut_delta is None or overall_delta is None or overall_delta == 0:
        return None
    return cut_delta / overall_delta * 100.0


@dataclass(frozen=True)
class RobustScore:
    """A day against its same-weekday history, scored on median/MAD.

    ``baseline_mad`` is the raw MAD; ``scale`` is the value actually used as the
    denominator -- the MAD rescaled to a standard-deviation equivalent, then
    raised to the floor if it fell below it.
    """

    score: float | None
    baseline_median: float | None
    baseline_mad: float | None
    scale: float | None
    baseline_n: int
    flags: tuple[str, ...] = field(default=())


def robust_scale_floor(baseline_median: float, metric_type: str) -> float:
    """Strictly positive lower bound for the scale of a robust score.

    Counts get a Poisson-flavoured ``sqrt(max(median, 1))`` -- plain
    ``sqrt(median)`` is still zero on an all-zero baseline. Continuous metrics
    have no variance law to appeal to, so they get a relative floor.
    """
    if metric_type == "count":
        return math.sqrt(max(baseline_median, 1.0))
    return abs(baseline_median) * CONTINUOUS_SCALE_FLOOR_FRACTION


def robust_score(
    current: float | None,
    baseline: list[float | None],
    metric_type: str,
) -> RobustScore:
    """Score ``current`` against its same-weekday baseline.

    Degenerate cases are routed, never fudged:

    * empty baseline -> unscored, ``no_baseline``
    * all-zero baseline with a non-zero current value -> unscored, ``new_event``
      (its novelty is the story; a z-score of it would be meaningless)
    * flat baseline (MAD = 0) -> floored scale, flagged, finite score
    """
    usable = [v for v in baseline if v is not None]
    flags: tuple[str, ...] = ()
    if len(usable) < MIN_BASELINE_POINTS:
        flags += ("thin_baseline",)
    if not usable:
        return RobustScore(None, None, None, None, 0, flags + ("no_baseline",))

    centre = median(usable)
    dispersion = median_absolute_deviation(usable)

    if centre == 0 and dispersion == 0:
        extra = ("all_zero_baseline",)
        if current:
            extra += ("new_event",)
        return RobustScore(None, centre, dispersion, None, len(usable), flags + extra)

    floor = robust_scale_floor(centre, metric_type)
    rescaled = dispersion * MAD_TO_SIGMA
    scale = max(rescaled, floor)  # a floor may only ever push the scale up
    if scale > rescaled:
        flags += ("scale_floored",)
    if scale <= 0:
        return RobustScore(None, centre, dispersion, None, len(usable), flags + ("degenerate_scale",))
    if current is None:
        return RobustScore(None, centre, dispersion, scale, len(usable), flags + ("no_current_value",))

    return RobustScore(
        score=(current - centre) / scale,
        baseline_median=centre,
        baseline_mad=dispersion,
        scale=scale,
        baseline_n=len(usable),
        flags=flags,
    )
