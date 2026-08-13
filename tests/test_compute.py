"""Input handling and the fail-closed policy.

The pipeline is allowed to refuse. It is not allowed to guess.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from veritas.compute import (
    STATUS_INCOMPLETE,
    STATUS_OK,
    STATUS_STALE,
    SourceError,
    build_plan,
    compute,
    load_source,
)
from veritas.registry import load_registry
from tests.support import REGISTRY_PATH, write_source

AS_OF = date(2026, 8, 13)
# The plan reaches eight same-weekday baselines back from 2026-08-02.
COVERED_FROM = date(2026, 5, 1)
COVERED_TO = date(2026, 8, 12)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, body: str) -> Path:
        path = self.tmp / "source.csv"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_clean_file_loads(self):
        path = write_source(self.tmp / "ok.csv", date(2026, 8, 1), date(2026, 8, 3))
        source = load_source(path)
        self.assertEqual(source.row_count, 3)
        self.assertEqual(source.min_date, date(2026, 8, 1))
        self.assertEqual(source.max_date, date(2026, 8, 3))
        self.assertEqual(len(source.sha256), 64)

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(SourceError):
            load_source(self.tmp / "nope.csv")

    def test_unexpected_columns_are_refused(self):
        path = self._write("date,country,sessions\n2026-08-01,US,10\n")
        with self.assertRaisesRegex(SourceError, "unexpected columns"):
            load_source(path)

    def test_a_non_numeric_measure_is_refused(self):
        path = self._write(
            "date,country,channel,sessions,orders,gmv\n2026-08-01,US,organic,x,1,2.0\n"
        )
        with self.assertRaisesRegex(SourceError, "not numeric"):
            load_source(path)

    def test_a_negative_measure_is_refused(self):
        path = self._write(
            "date,country,channel,sessions,orders,gmv\n2026-08-01,US,organic,-5,1,2.0\n"
        )
        with self.assertRaisesRegex(SourceError, "negative"):
            load_source(path)

    def test_a_non_finite_measure_is_refused(self):
        path = self._write(
            "date,country,channel,sessions,orders,gmv\n2026-08-01,US,organic,nan,1,2.0\n"
        )
        with self.assertRaisesRegex(SourceError, "not finite"):
            load_source(path)

    def test_a_bad_date_is_refused(self):
        path = self._write(
            "date,country,channel,sessions,orders,gmv\n01/08/2026,US,organic,5,1,2.0\n"
        )
        with self.assertRaisesRegex(SourceError, "bad date"):
            load_source(path)

    def test_a_duplicate_cell_is_refused(self):
        path = self._write(
            "date,country,channel,sessions,orders,gmv\n"
            "2026-08-01,US,organic,5,1,2.0\n"
            "2026-08-01,US,organic,6,1,2.0\n"
        )
        with self.assertRaisesRegex(SourceError, "duplicate"):
            load_source(path)

    def test_an_empty_dimension_is_refused(self):
        path = self._write(
            "date,country,channel,sessions,orders,gmv\n2026-08-01,,organic,5,1,2.0\n"
        )
        with self.assertRaisesRegex(SourceError, "empty"):
            load_source(path)

    def test_the_engine_namespace_is_reserved(self):
        path = self._write(
            "date,country,channel,sessions,orders,gmv\n2026-08-01,US,__residual__,5,1,2.0\n"
        )
        with self.assertRaisesRegex(SourceError, "reserved"):
            load_source(path)

    def test_a_headers_only_file_is_refused(self):
        path = self._write("date,country,channel,sessions,orders,gmv\n")
        with self.assertRaisesRegex(SourceError, "no data rows"):
            load_source(path)


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.registry = load_registry(REGISTRY_PATH)
        self.plan = build_plan(AS_OF)

    def tearDown(self):
        self._tmp.cleanup()

    def _compute(self, start=COVERED_FROM, end=COVERED_TO):
        path = write_source(self.tmp / "source.csv", start, end)
        return compute(load_source(path), self.registry, self.plan)

    def test_full_coverage_computes_every_metric(self):
        result = self._compute()
        self.assertEqual({s.status for s in result.statuses}, {STATUS_OK})
        self.assertFalse(result.incomplete)
        self.assertTrue(result.records)

    def test_stale_data_marks_every_metric_stale_and_fails_the_run(self):
        result = self._compute(end=date(2026, 8, 5))
        self.assertEqual({s.status for s in result.statuses}, {STATUS_STALE})
        self.assertTrue(result.incomplete)
        self.assertEqual(result.records, [])
        self.assertIn("needs data through", result.statuses[0].detail)

    def test_a_period_with_no_rows_marks_the_metric_incomplete(self):
        # Data starts inside the daily baseline window: some periods are empty.
        result = self._compute(start=date(2026, 7, 1))
        self.assertEqual({s.status for s in result.statuses}, {STATUS_INCOMPLETE})
        self.assertTrue(result.incomplete)
        self.assertIn("day(s) missing", result.statuses[0].detail)

    def test_an_optional_metric_alone_does_not_fail_the_run(self):
        result = self._compute()
        statuses = {s.metric_id: s for s in result.statuses}
        self.assertTrue(statuses["sessions"].usable)
        self.assertFalse(statuses["sessions"].required)

    def test_the_plan_covers_the_periods_it_will_report(self):
        self.assertEqual(self.plan.week.end, date(2026, 8, 8))
        self.assertEqual(len(self.plan.week_baselines), 4)
        self.assertEqual(len(self.plan.days), 7)
        self.assertEqual(len(self.plan.day_baselines[self.plan.days[0]]), 8)
        self.assertIsNotNone(self.plan.partial)
        span_start, span_end = self.plan.coverage_span()
        self.assertLessEqual(span_start, date(2026, 6, 7))
        self.assertEqual(span_end, date(2026, 8, 12))

    def test_a_source_ending_one_day_early_is_caught_by_the_day_grid(self):
        # Inside the freshness slack, so not "stale" -- but the partial week
        # would be a day shorter than the baselines it is compared against, and
        # every period still has *some* rows. Only a day-level grid check sees it.
        result = self._compute(end=date(2026, 8, 11))
        self.assertEqual({s.status for s in result.statuses}, {STATUS_INCOMPLETE})
        self.assertTrue(result.incomplete)
        self.assertIn("2026-08-12", result.statuses[0].detail)


if __name__ == "__main__":
    unittest.main()
