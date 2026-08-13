"""The canonical record contract: zero, null and missing are three things."""

import unittest
from datetime import date

from veritas.periods import COMPLETE_WEEK, Period
from veritas.records import (
    PROV_QUERY,
    PROV_ZERO_FILL,
    build_record,
    materialize_grid,
    zero_filled,
)
from veritas.registry import Metric

WEEK = Period(COMPLETE_WEEK, date(2026, 8, 2), date(2026, 8, 8))
PRIOR = Period(COMPLETE_WEEK, date(2026, 7, 26), date(2026, 8, 1))

GMV = Metric(
    id="gmv",
    title="GMV",
    metric_type="continuous",
    statistic="sum",
    numerator_field="gmv",
    unit="usd",
    display_precision=0,
    direction="up_is_good",
    required=True,
    cuts=("channel",),
    n_obs_field="orders",
)
CONVERSION = Metric(
    id="conversion_rate",
    title="Conversion rate",
    metric_type="rate",
    statistic="rate",
    numerator_field="orders",
    denominator_field="sessions",
    unit="percent",
    display_precision=2,
    direction="up_is_good",
    required=True,
    cuts=("channel",),
    n_obs_field="sessions",
)
AOV = Metric(
    id="aov",
    title="Average order value",
    metric_type="continuous",
    statistic="mean",
    numerator_field="gmv",
    denominator_field="orders",
    unit="usd",
    display_precision=2,
    direction="up_is_good",
    required=False,
    cuts=("channel",),
    n_obs_field="orders",
)


class RecordTests(unittest.TestCase):
    def test_a_sum_is_the_summed_field(self):
        record = build_record(GMV, WEEK, None, None, {"gmv": 1000.0, "orders": 20.0})
        self.assertEqual(record.value, 1000.0)
        self.assertEqual(record.n_obs, 20.0)
        self.assertEqual(record.grain, "overall")
        self.assertEqual(record.provenance, PROV_QUERY)

    def test_a_rate_is_scaled_to_percent(self):
        record = build_record(
            CONVERSION, WEEK, None, None, {"orders": 30.0, "sessions": 1000.0}
        )
        self.assertAlmostEqual(record.value, 3.0)
        self.assertEqual(record.numerator, 30.0)
        self.assertEqual(record.denominator, 1000.0)

    def test_a_mean_is_not_scaled(self):
        record = build_record(AOV, WEEK, None, None, {"gmv": 1000.0, "orders": 20.0})
        self.assertAlmostEqual(record.value, 50.0)

    def test_no_exposure_is_null_not_zero(self):
        record = build_record(CONVERSION, WEEK, None, None, {"orders": 0.0, "sessions": 0.0})
        self.assertIsNone(record.value)
        self.assertIn("no_exposure", record.flags)

    def test_a_genuine_zero_activity_period_is_zero_and_present(self):
        record = build_record(GMV, WEEK, None, None, {"gmv": 0.0, "orders": 0.0})
        self.assertEqual(record.value, 0.0)
        self.assertEqual(record.provenance, PROV_QUERY)


class ZeroFillTests(unittest.TestCase):
    def test_additive_metrics_fill_with_zero(self):
        record = zero_filled(GMV, WEEK, "channel", "display_ads")
        self.assertEqual(record.value, 0.0)
        self.assertEqual(record.provenance, PROV_ZERO_FILL)
        self.assertIn("zero_filled", record.flags)

    def test_rates_fill_with_null_never_a_fabricated_zero_percent(self):
        record = zero_filled(CONVERSION, WEEK, "channel", "display_ads")
        self.assertIsNone(record.value)
        self.assertEqual(record.denominator, 0.0)

    def test_means_fill_with_null(self):
        # With no orders there is no average; a 0 would read as a collapse.
        self.assertIsNone(zero_filled(AOV, WEEK, "channel", "display_ads").value)

    def test_a_filled_zero_is_distinguishable_from_a_real_zero(self):
        filled = zero_filled(GMV, WEEK, "channel", "display_ads")
        real = build_record(GMV, WEEK, "channel", "display_ads", {"gmv": 0.0})
        self.assertEqual(filled.value, real.value)
        self.assertNotEqual(filled.provenance, real.provenance)


class GridTests(unittest.TestCase):
    def test_a_cut_that_vanished_gets_a_record_for_the_current_period(self):
        records = [
            build_record(GMV, PRIOR, "channel", "display_ads", {"gmv": 500.0}),
            build_record(GMV, WEEK, "channel", "organic", {"gmv": 900.0}),
            build_record(GMV, PRIOR, "channel", "organic", {"gmv": 800.0}),
        ]
        grid = materialize_grid(GMV, records, [PRIOR, WEEK], "channel")
        current = {
            r.cut_value: r for r in grid if r.period == WEEK
        }
        self.assertEqual(set(current), {"display_ads", "organic"})
        self.assertEqual(current["display_ads"].value, 0.0)
        self.assertEqual(current["display_ads"].provenance, PROV_ZERO_FILL)
        self.assertEqual(current["organic"].provenance, PROV_QUERY)

    def test_a_cut_that_just_arrived_gets_zero_filled_history(self):
        records = [build_record(GMV, WEEK, "channel", "marketplace", {"gmv": 100.0})]
        grid = materialize_grid(GMV, records, [PRIOR, WEEK], "channel")
        prior = [r for r in grid if r.period == PRIOR]
        self.assertEqual(len(prior), 1)
        self.assertEqual(prior[0].provenance, PROV_ZERO_FILL)

    def test_the_grid_does_not_duplicate_delivered_records(self):
        records = [
            build_record(GMV, WEEK, "channel", "organic", {"gmv": 900.0}),
            build_record(GMV, PRIOR, "channel", "organic", {"gmv": 800.0}),
        ]
        grid = materialize_grid(GMV, records, [PRIOR, WEEK], "channel")
        self.assertEqual(len(grid), 2)


if __name__ == "__main__":
    unittest.main()
