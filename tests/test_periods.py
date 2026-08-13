"""Calendar arithmetic. Off-by-one here silently corrupts every number above."""

import unittest
from datetime import date, timedelta

from veritas.periods import (
    Period,
    daily_periods,
    last_complete_day,
    partial_week,
    prior_weeks,
    same_weekday_baseline,
    week_of,
    week_start,
    week_in_review,
)


class WeekStartTests(unittest.TestCase):
    def test_sunday_is_its_own_week_start(self):
        self.assertEqual(week_start(date(2026, 8, 2)), date(2026, 8, 2))

    def test_monday_belongs_to_the_preceding_sunday(self):
        self.assertEqual(week_start(date(2026, 8, 3)), date(2026, 8, 2))

    def test_saturday_belongs_to_the_preceding_sunday(self):
        self.assertEqual(week_start(date(2026, 8, 8)), date(2026, 8, 2))

    def test_every_weekday_maps_into_a_seven_day_window(self):
        for offset in range(7):
            day = date(2026, 8, 2) + timedelta(days=offset)
            self.assertEqual(week_start(day), date(2026, 8, 2), day)

    def test_week_of_spans_sunday_to_saturday(self):
        week = week_of(date(2026, 8, 5))
        self.assertEqual((week.start, week.end), (date(2026, 8, 2), date(2026, 8, 8)))
        self.assertEqual(week.n_days, 7)


class ReportingWindowTests(unittest.TestCase):
    def test_the_current_day_is_never_reported(self):
        self.assertEqual(last_complete_day(date(2026, 8, 13)), date(2026, 8, 12))

    def test_week_in_review_is_the_last_finished_week(self):
        week = week_in_review(date(2026, 8, 13))  # a Thursday
        self.assertEqual((week.start, week.end), (date(2026, 8, 2), date(2026, 8, 8)))

    def test_a_sunday_run_reports_the_week_that_just_ended(self):
        # as_of Sunday -> last complete day is Saturday -> that week is complete.
        week = week_in_review(date(2026, 8, 9))
        self.assertEqual((week.start, week.end), (date(2026, 8, 2), date(2026, 8, 8)))

    def test_a_sunday_run_has_no_partial_week(self):
        self.assertIsNone(partial_week(date(2026, 8, 9)))

    def test_a_monday_run_has_a_one_day_partial_week(self):
        partial = partial_week(date(2026, 8, 10))
        self.assertEqual((partial.start, partial.end), (date(2026, 8, 9), date(2026, 8, 9)))

    def test_partial_week_stops_at_the_last_complete_day(self):
        partial = partial_week(date(2026, 8, 13))
        self.assertEqual((partial.start, partial.end), (date(2026, 8, 9), date(2026, 8, 12)))
        self.assertEqual(partial.n_days, 4)

    def test_partial_baselines_are_the_same_elapsed_slice(self):
        partial = partial_week(date(2026, 8, 13))
        baselines = prior_weeks(partial, 4)
        self.assertEqual(len(baselines), 4)
        for baseline in baselines:
            self.assertEqual(baseline.n_days, partial.n_days)
        self.assertEqual(baselines[0].start, date(2026, 8, 2))
        self.assertEqual(baselines[0].end, date(2026, 8, 5))
        self.assertEqual(baselines[3].start, date(2026, 7, 12))


class BaselineTests(unittest.TestCase):
    def test_prior_weeks_are_most_recent_first(self):
        week = week_of(date(2026, 8, 5))
        baselines = prior_weeks(week, 4)
        self.assertEqual([b.end for b in baselines][0], date(2026, 8, 1))
        self.assertEqual([b.end for b in baselines][-1], date(2026, 7, 11))

    def test_same_weekday_baseline_keeps_the_weekday(self):
        wednesday = Period("daily", date(2026, 8, 5), date(2026, 8, 5))
        for baseline in same_weekday_baseline(wednesday, 8):
            self.assertEqual(baseline.start.weekday(), wednesday.start.weekday())
        self.assertEqual(same_weekday_baseline(wednesday, 8)[-1].start, date(2026, 6, 10))

    def test_daily_periods_cover_the_week(self):
        days = daily_periods(week_of(date(2026, 8, 5)))
        self.assertEqual(len(days), 7)
        self.assertEqual(days[0].start, date(2026, 8, 2))
        self.assertEqual(days[-1].start, date(2026, 8, 8))


class PeriodTests(unittest.TestCase):
    def test_a_backwards_period_is_rejected(self):
        with self.assertRaises(ValueError):
            Period("daily", date(2026, 8, 5), date(2026, 8, 1))

    def test_label_is_stable(self):
        self.assertEqual(week_of(date(2026, 8, 5)).label, "complete_week:2026-08-08")


if __name__ == "__main__":
    unittest.main()
