"""Metric registry: the declarative surface the whole pipeline reads.

The registry is the only place that knows what a metric *means*. Compute,
statistics, packing and validation stay metric-agnostic, which is what makes
"add a metric" a config change rather than an engine change.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

METRIC_TYPES = {"count", "rate", "continuous"}
STATISTICS = {"count", "sum", "rate", "mean"}
UNITS = {"usd", "percent", "count"}
DIRECTIONS = {"up_is_good", "down_is_good"}

#: Statistics whose cut-level values sum to the overall value. Only these may be
#: decomposed into contributions-to-change.
ADDITIVE_STATISTICS = {"count", "sum"}

SUPPORTED_SCHEMA_VERSION = 2


class RegistryError(ValueError):
    """Raised when the registry file is malformed. Always fatal, never patched."""


@dataclass(frozen=True)
class Metric:
    id: str
    title: str
    metric_type: str
    statistic: str
    numerator_field: str
    unit: str
    display_precision: int
    direction: str
    required: bool
    cuts: tuple[str, ...]
    denominator_field: str | None = None
    n_obs_field: str = "sessions"

    @property
    def is_ratio(self) -> bool:
        return self.denominator_field is not None

    @property
    def is_additive(self) -> bool:
        return self.statistic in ADDITIVE_STATISTICS

    @property
    def tolerance(self) -> float:
        """Half a unit of the last displayed decimal place.

        A claim is accepted when it agrees with the computed number at the
        precision the report prints. Anything coarser would let a wrong number
        through; anything finer would reject correct rounding.
        """
        return 0.5 * (10.0 ** -self.display_precision)


@dataclass(frozen=True)
class Registry:
    metrics: tuple[Metric, ...]
    path: Path
    sha256: str
    schema_version: int = SUPPORTED_SCHEMA_VERSION
    _by_id: dict[str, Metric] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {m.id: m for m in self.metrics})

    def get(self, metric_id: str) -> Metric:
        try:
            return self._by_id[metric_id]
        except KeyError:
            raise RegistryError(f"unknown metric id: {metric_id!r}") from None

    def has(self, metric_id: str) -> bool:
        return metric_id in self._by_id

    @property
    def required_ids(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.metrics if m.required)


def _require(mapping: dict, key: str, where: str):
    if key not in mapping:
        raise RegistryError(f"{where}: missing required field {key!r}")
    return mapping[key]


def _parse_metric(raw: dict, index: int) -> Metric:
    where = f"metric[{index}]"
    metric_id = _require(raw, "id", where)
    where = f"metric {metric_id!r}"

    metric_type = _require(raw, "metric_type", where)
    if metric_type not in METRIC_TYPES:
        raise RegistryError(f"{where}: metric_type {metric_type!r} not in {sorted(METRIC_TYPES)}")

    statistic = _require(raw, "statistic", where)
    if statistic not in STATISTICS:
        raise RegistryError(f"{where}: statistic {statistic!r} not in {sorted(STATISTICS)}")

    unit = _require(raw, "unit", where)
    if unit not in UNITS:
        raise RegistryError(f"{where}: unit {unit!r} not in {sorted(UNITS)}")

    direction = _require(raw, "direction", where)
    if direction not in DIRECTIONS:
        raise RegistryError(f"{where}: direction {direction!r} not in {sorted(DIRECTIONS)}")

    denominator_field = raw.get("denominator_field")
    if statistic in {"rate", "mean"} and not denominator_field:
        raise RegistryError(f"{where}: statistic {statistic!r} requires denominator_field")
    if statistic in {"count", "sum"} and denominator_field:
        raise RegistryError(f"{where}: statistic {statistic!r} must not declare denominator_field")

    cuts = tuple(raw.get("cuts", ()))
    if len(set(cuts)) != len(cuts):
        raise RegistryError(f"{where}: duplicate cut dimension")

    precision = int(_require(raw, "display_precision", where))
    if precision < 0:
        raise RegistryError(f"{where}: display_precision must be >= 0")

    return Metric(
        id=metric_id,
        title=_require(raw, "title", where),
        metric_type=metric_type,
        statistic=statistic,
        numerator_field=_require(raw, "numerator_field", where),
        denominator_field=denominator_field,
        n_obs_field=raw.get("n_obs_field", "sessions"),
        unit=unit,
        display_precision=precision,
        direction=direction,
        required=bool(raw.get("required", False)),
        cuts=cuts,
    )


def load_registry(path: Path) -> Registry:
    """Parse and validate the registry. A malformed registry stops the run."""
    raw_bytes = path.read_bytes()
    document = tomllib.loads(raw_bytes.decode("utf-8"))

    version = int(document.get("schema_version", 0))
    if version != SUPPORTED_SCHEMA_VERSION:
        raise RegistryError(
            f"registry schema_version {version} is not supported "
            f"(this build reads {SUPPORTED_SCHEMA_VERSION})"
        )

    entries = document.get("metric", [])
    if not entries:
        raise RegistryError("registry declares no metrics")

    metrics = tuple(_parse_metric(raw, i) for i, raw in enumerate(entries))
    ids = [m.id for m in metrics]
    if len(set(ids)) != len(ids):
        raise RegistryError("duplicate metric id in registry")

    return Registry(
        metrics=metrics,
        path=path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        schema_version=version,
    )
