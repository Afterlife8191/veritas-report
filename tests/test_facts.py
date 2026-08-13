"""Facts, provenance and the bounded pack, computed from the real dataset."""

import unittest
from datetime import date
from functools import lru_cache

from tests.support import REGISTRY_PATH
from veritas.compute import build_plan, compute, load_source
from veritas.facts import build_factbook, make_fact_id
from veritas.generate import DEFAULT_SEED, generate_rows, write_csv
from veritas.registry import load_registry

AS_OF = date(2026, 8, 13)
WEEK_END = "2026-08-08"


@lru_cache(maxsize=1)
def _factbook():
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    path = write_csv(tmp / "source.csv", generate_rows(seed=DEFAULT_SEED))
    registry = load_registry(REGISTRY_PATH)
    plan = build_plan(AS_OF)
    source = load_source(path)
    result = compute(source, registry, plan)
    return build_factbook(result, registry, plan, source.sha256, "test-run")


class FactIdTests(unittest.TestCase):
    def test_ids_are_metric_cut_period_statistic(self):
        self.assertEqual(
            make_fact_id("gmv", "channel", "organic", build_plan(AS_OF).week, "wow_pct"),
            f"gmv/channel=organic/complete_week:{WEEK_END}/wow_pct",
        )

    def test_overall_has_no_cut_value(self):
        self.assertTrue(
            make_fact_id("gmv", None, None, build_plan(AS_OF).week, "value").startswith(
                "gmv/overall/"
            )
        )


class FactBookTests(unittest.TestCase):
    def setUp(self):
        self.book = _factbook()

    def test_every_metric_produced_facts(self):
        metrics = {fact.metric_id for fact in self.book.facts.values()}
        self.assertEqual(metrics, {"gmv", "orders", "sessions", "conversion_rate", "aov"})

    def test_the_run_is_complete_on_the_shipped_dataset(self):
        self.assertFalse(self.book.incomplete)
        self.assertTrue(all(entry["status"] == "ok" for entry in self.book.coverage))

    def test_every_fact_carries_a_derivation(self):
        for fact in self.book.facts.values():
            self.assertTrue(fact.provenance.computation)
            self.assertTrue(fact.provenance.formula)

    def test_derived_facts_name_the_facts_they_came_from(self):
        wow = self.book.get(f"gmv/overall/complete_week:{WEEK_END}/wow_pct")
        self.assertIsNotNone(wow)
        self.assertEqual(len(wow.provenance.inputs), 2)
        for input_id in wow.provenance.inputs:
            self.assertIn(input_id, self.book.facts)

    def test_display_strings_round_trip_to_the_value(self):
        from veritas.numeric import parse_number

        for fact in list(self.book.facts.values())[:500]:
            if fact.value is None:
                self.assertEqual(fact.display, "n/a")
                continue
            self.assertAlmostEqual(parse_number(fact.display), fact.value, delta=fact.tolerance)

    def test_non_additive_metrics_are_never_decomposed(self):
        for metric_id in ("aov", "conversion_rate"):
            contributions = [
                fact
                for fact in self.book.facts.values()
                if fact.metric_id == metric_id
                and fact.statistic_key == "contribution_share_pct"
            ]
            self.assertEqual(contributions, [], metric_id)

    def test_additive_metrics_are_decomposed_at_the_cut_grain_only(self):
        contributions = [
            fact
            for fact in self.book.facts.values()
            if fact.metric_id == "gmv" and fact.statistic_key == "contribution_share_pct"
        ]
        self.assertTrue(contributions)
        self.assertTrue(all(fact.cut_dim is not None for fact in contributions))


class PackTests(unittest.TestCase):
    def setUp(self):
        self.book = _factbook()
        self.pack = self.book.pack()

    def test_the_pack_is_a_strict_subset_of_the_facts(self):
        pack_ids = {entry["id"] for entry in self.pack["facts"]}
        self.assertTrue(pack_ids)
        self.assertTrue(pack_ids < set(self.book.facts))

    def test_the_pack_hides_derivations_from_the_writer(self):
        for entry in self.pack["facts"]:
            self.assertNotIn("provenance", entry)
            self.assertIn("display", entry)

    def test_the_pack_states_its_own_coverage(self):
        self.assertEqual(len(self.pack["coverage"]), 5)
        self.assertTrue(self.pack["notes"])

    def test_every_shortlisted_fact_id_resolves(self):
        for item in self.book.shortlist:
            for fact_id in item.fact_ids:
                self.assertIn(fact_id, self.book.facts)


class ShortlistTests(unittest.TestCase):
    def setUp(self):
        self.book = _factbook()
        self.by_cut = {
            (item.metric_id, item.cut_value): item for item in self.book.shortlist
        }

    def test_the_overall_figure_is_always_shortlisted(self):
        for metric_id in ("gmv", "orders", "conversion_rate"):
            self.assertIn((metric_id, None), self.by_cut)

    def test_a_segment_that_vanished_is_selected_by_the_disappeared_channel(self):
        item = self.by_cut.get(("gmv", "display_ads"))
        self.assertIsNotNone(item, "the shut-off channel should be shortlisted")
        self.assertIn("disappeared", item.channels)

    def test_a_segment_with_no_history_is_selected_by_the_new_channel(self):
        item = self.by_cut.get(("gmv", "marketplace"))
        self.assertIsNotNone(item, "the newly launched channel should be shortlisted")
        self.assertIn("newly_appeared", item.channels)

    def test_selection_uses_more_than_one_channel(self):
        channels = {channel for item in self.book.shortlist for channel in item.channels}
        self.assertTrue({"screening_z", "disappeared", "newly_appeared"} <= channels)


if __name__ == "__main__":
    unittest.main()
