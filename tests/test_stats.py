"""Every statistic, against values worked out by hand.

Expected numbers are derived in the comments rather than copied from a run, so a
change in behaviour fails the test instead of being blessed by it.
"""

import math
import unittest

from veritas.stats import (
    MAD_TO_SIGMA,
    compare_to_baseline,
    contribution_share,
    delta,
    mean,
    median,
    median_absolute_deviation,
    pct_change,
    rate_adequacy_flags,
    robust_scale_floor,
    robust_score,
    sample_sd,
    two_proportion_z,
)


class ElementaryTests(unittest.TestCase):
    def test_delta(self):
        self.assertEqual(delta(120.0, 100.0), 20.0)
        self.assertIsNone(delta(None, 100.0))
        self.assertIsNone(delta(120.0, None))

    def test_pct_change(self):
        self.assertAlmostEqual(pct_change(110.0, 100.0), 10.0)
        self.assertAlmostEqual(pct_change(90.0, 100.0), -10.0)

    def test_pct_change_uses_the_magnitude_of_the_base(self):
        # -50 -> -40 is an improvement of 10 on a base of magnitude 50: +20%.
        self.assertAlmostEqual(pct_change(-40.0, -50.0), 20.0)

    def test_growth_from_zero_is_not_a_percentage(self):
        self.assertIsNone(pct_change(50.0, 0.0))

    def test_mean_and_sd(self):
        # [10,12,14,16]: mean 13; deviations -3,-1,1,3; sum sq 20; /3 = 6.6667.
        self.assertAlmostEqual(mean([10, 12, 14, 16]), 13.0)
        self.assertAlmostEqual(sample_sd([10, 12, 14, 16]), math.sqrt(20 / 3))

    def test_sd_needs_two_points(self):
        self.assertIsNone(sample_sd([10]))
        self.assertEqual(sample_sd([10, 10]), 0.0)

    def test_median_of_even_and_odd_samples(self):
        self.assertEqual(median([3, 1, 2]), 2.0)
        self.assertEqual(median([1, 2, 3, 4]), 2.5)
        self.assertIsNone(median([]))

    def test_median_absolute_deviation(self):
        # median 2.5; |deviations| = 1.5, 0.5, 0.5, 1.5; their median is 1.0.
        self.assertEqual(median_absolute_deviation([1, 2, 3, 4]), 1.0)

    def test_none_values_are_skipped_not_treated_as_zero(self):
        self.assertAlmostEqual(mean([10, None, 20]), 15.0)
        self.assertEqual(median([10, None, 20]), 15.0)


class BaselineComparisonTests(unittest.TestCase):
    def test_screening_z_against_a_hand_computed_baseline(self):
        # baseline mean 13, sample sd sqrt(20/3) = 2.581989; (20-13)/2.581989.
        comparison = compare_to_baseline(20.0, [10, 12, 14, 16])
        self.assertAlmostEqual(comparison.baseline_mean, 13.0)
        self.assertAlmostEqual(comparison.baseline_sd, 2.5819889, places=6)
        self.assertAlmostEqual(comparison.screening_z, 7 / math.sqrt(20 / 3), places=9)
        self.assertAlmostEqual(comparison.delta, 7.0)
        self.assertAlmostEqual(comparison.pct, 7 / 13 * 100)
        self.assertEqual(comparison.baseline_n, 4)
        self.assertEqual(comparison.flags, ())

    def test_a_flat_baseline_yields_no_z(self):
        comparison = compare_to_baseline(20.0, [10, 10, 10, 10])
        self.assertIsNone(comparison.screening_z)
        self.assertIn("degenerate_baseline_scale", comparison.flags)

    def test_a_short_baseline_is_flagged_but_still_compared(self):
        comparison = compare_to_baseline(20.0, [10, 14])
        self.assertIn("thin_baseline", comparison.flags)
        self.assertIsNotNone(comparison.screening_z)

    def test_an_empty_baseline_is_flagged_and_unscored(self):
        comparison = compare_to_baseline(20.0, [None, None])
        self.assertIn("no_baseline", comparison.flags)
        self.assertIsNone(comparison.screening_z)
        self.assertIsNone(comparison.baseline_mean)
        self.assertEqual(comparison.baseline_n, 0)

    def test_a_missing_current_value_is_flagged(self):
        comparison = compare_to_baseline(None, [10, 12, 14, 16])
        self.assertIn("no_current_value", comparison.flags)
        self.assertIsNone(comparison.screening_z)


class RateTests(unittest.TestCase):
    def test_two_proportion_z(self):
        # p1 .05, p2 .04, pooled .045; se = sqrt(.045*.955*(2/1000)) = .00927095.
        z = two_proportion_z(50, 1000, 40, 1000)
        self.assertAlmostEqual(z, 0.01 / math.sqrt(0.045 * 0.955 * 0.002), places=9)

    def test_two_proportion_z_needs_both_samples(self):
        self.assertIsNone(two_proportion_z(5, 0, 4, 100))
        self.assertIsNone(two_proportion_z(0, 100, 0, 100))  # pooled p = 0

    def test_adequacy_flags_a_small_success_cell_even_at_large_n(self):
        # 1 order in 100 sessions: n is fine, np = 1 is not.
        self.assertEqual(rate_adequacy_flags(1, 100), ("small_cell",))

    def test_adequacy_flags_a_small_denominator(self):
        self.assertEqual(rate_adequacy_flags(10, 20), ("small_denominator",))

    def test_adequacy_accepts_a_healthy_rate(self):
        self.assertEqual(rate_adequacy_flags(50, 1000), ())

    def test_no_exposure_short_circuits(self):
        self.assertEqual(rate_adequacy_flags(0, 0), ("no_exposure",))


class ContributionTests(unittest.TestCase):
    def test_contribution_share(self):
        self.assertAlmostEqual(contribution_share(-50.0, 200.0), -25.0)

    def test_no_share_of_a_zero_change(self):
        self.assertIsNone(contribution_share(-50.0, 0.0))
        self.assertIsNone(contribution_share(None, 200.0))


class RobustScoreTests(unittest.TestCase):
    def test_scale_floor_for_counts_is_poisson_flavoured(self):
        self.assertAlmostEqual(robust_scale_floor(100.0, "count"), 10.0)

    def test_count_floor_never_collapses_on_an_empty_baseline(self):
        self.assertAlmostEqual(robust_scale_floor(0.0, "count"), 1.0)

    def test_continuous_floor_is_relative(self):
        self.assertAlmostEqual(robust_scale_floor(500.0, "continuous"), 5.0)
        self.assertAlmostEqual(robust_scale_floor(-500.0, "continuous"), 5.0)

    def test_score_against_a_hand_computed_baseline(self):
        # baseline 10..24 by 2: median 17; |deviations| 7,5,3,1,1,3,5,7 -> MAD 4.
        # scale = 4 * 1.4826 = 5.9304; (30 - 17) / 5.9304 = 2.19217.
        score = robust_score(30.0, [10, 12, 14, 16, 18, 20, 22, 24], "continuous")
        self.assertAlmostEqual(score.baseline_median, 17.0)
        self.assertAlmostEqual(score.baseline_mad, 4.0)
        self.assertAlmostEqual(score.scale, 4.0 * MAD_TO_SIGMA)
        self.assertAlmostEqual(score.score, 13.0 / (4.0 * MAD_TO_SIGMA), places=9)
        self.assertEqual(score.baseline_n, 8)
        self.assertEqual(score.flags, ())

    def test_a_flat_count_baseline_is_floored_not_infinite(self):
        # median 10, MAD 0 -> floor sqrt(10) = 3.162278; (20 - 10) / 3.162278.
        score = robust_score(20.0, [10, 10, 10, 10], "count")
        self.assertAlmostEqual(score.scale, math.sqrt(10.0))
        self.assertAlmostEqual(score.score, 10.0 / math.sqrt(10.0), places=9)
        self.assertIn("scale_floored", score.flags)
        self.assertTrue(math.isfinite(score.score))

    def test_a_flat_continuous_baseline_uses_the_relative_floor(self):
        score = robust_score(110.0, [100.0] * 8, "continuous")
        self.assertAlmostEqual(score.scale, 1.0)
        self.assertAlmostEqual(score.score, 10.0)
        self.assertIn("scale_floored", score.flags)

    def test_the_floor_only_ever_raises_the_scale(self):
        # A widely dispersed baseline keeps its own scale; substituting the
        # smaller floor would shrink the denominator and manufacture a spike.
        wide = [0.0, 100.0, 0.0, 100.0, 0.0, 100.0, 0.0, 100.0]
        score = robust_score(200.0, wide, "continuous")
        self.assertGreater(score.scale, robust_scale_floor(50.0, "continuous"))
        self.assertNotIn("scale_floored", score.flags)

    def test_an_all_zero_baseline_with_activity_is_routed_not_scored(self):
        score = robust_score(42.0, [0, 0, 0, 0, 0, 0, 0, 0], "count")
        self.assertIsNone(score.score)
        self.assertIn("all_zero_baseline", score.flags)
        self.assertIn("new_event", score.flags)

    def test_an_all_zero_baseline_with_no_activity_is_not_a_new_event(self):
        score = robust_score(0.0, [0, 0, 0, 0], "count")
        self.assertIsNone(score.score)
        self.assertIn("all_zero_baseline", score.flags)
        self.assertNotIn("new_event", score.flags)

    def test_no_baseline_is_flagged_and_unscored(self):
        score = robust_score(42.0, [None, None], "count")
        self.assertIsNone(score.score)
        self.assertIn("no_baseline", score.flags)

    def test_a_short_baseline_is_flagged(self):
        score = robust_score(20.0, [10, 12], "count")
        self.assertIn("thin_baseline", score.flags)

    def test_a_missing_current_value_is_flagged(self):
        score = robust_score(None, [10, 12, 14, 16], "count")
        self.assertIsNone(score.score)
        self.assertIn("no_current_value", score.flags)


if __name__ == "__main__":
    unittest.main()
