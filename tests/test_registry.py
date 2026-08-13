"""The registry is the plug-in surface, so it validates itself strictly."""

import tempfile
import unittest
from pathlib import Path

from tests.support import REGISTRY_PATH
from veritas.registry import RegistryError, load_registry

MINIMAL = """
schema_version = 2

[[metric]]
id = "gmv"
title = "GMV"
metric_type = "continuous"
statistic = "sum"
numerator_field = "gmv"
unit = "usd"
display_precision = 0
direction = "up_is_good"
required = true
cuts = ["channel"]
"""


class ShippedRegistryTests(unittest.TestCase):
    def test_the_shipped_registry_loads(self):
        registry = load_registry(REGISTRY_PATH)
        self.assertIn("gmv", [m.id for m in registry.metrics])
        self.assertEqual(len(registry.sha256), 64)

    def test_required_metrics_are_declared(self):
        registry = load_registry(REGISTRY_PATH)
        self.assertIn("gmv", registry.required_ids)
        self.assertNotIn("sessions", registry.required_ids)

    def test_additivity_follows_the_statistic(self):
        registry = load_registry(REGISTRY_PATH)
        self.assertTrue(registry.get("gmv").is_additive)
        self.assertFalse(registry.get("aov").is_additive)
        self.assertFalse(registry.get("conversion_rate").is_additive)

    def test_tolerance_is_half_a_unit_of_the_last_shown_decimal(self):
        registry = load_registry(REGISTRY_PATH)
        self.assertAlmostEqual(registry.get("gmv").tolerance, 0.5)
        self.assertAlmostEqual(registry.get("conversion_rate").tolerance, 0.005)

    def test_an_unknown_metric_is_an_error_not_a_none(self):
        with self.assertRaises(RegistryError):
            load_registry(REGISTRY_PATH).get("revenue")


class MalformedRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _load(self, body: str):
        path = self.tmp / "registry.toml"
        path.write_text(body, encoding="utf-8")
        return load_registry(path)

    def test_the_minimal_registry_loads(self):
        self.assertEqual(len(self._load(MINIMAL).metrics), 1)

    def test_a_future_schema_version_is_refused(self):
        with self.assertRaisesRegex(RegistryError, "schema_version"):
            self._load(MINIMAL.replace("schema_version = 2", "schema_version = 99"))

    def test_an_empty_registry_is_refused(self):
        with self.assertRaisesRegex(RegistryError, "no metrics"):
            self._load("schema_version = 2\n")

    def test_a_missing_field_is_refused(self):
        with self.assertRaisesRegex(RegistryError, "missing required field"):
            self._load(MINIMAL.replace('unit = "usd"\n', ""))

    def test_an_unknown_statistic_is_refused(self):
        with self.assertRaisesRegex(RegistryError, "statistic"):
            self._load(MINIMAL.replace('statistic = "sum"', 'statistic = "p90"'))

    def test_a_ratio_without_a_denominator_is_refused(self):
        with self.assertRaisesRegex(RegistryError, "denominator_field"):
            self._load(MINIMAL.replace('statistic = "sum"', 'statistic = "rate"'))

    def test_a_sum_with_a_denominator_is_refused(self):
        with self.assertRaisesRegex(RegistryError, "denominator_field"):
            self._load(MINIMAL + '\ndenominator_field = "sessions"\n')

    def test_duplicate_metric_ids_are_refused(self):
        with self.assertRaisesRegex(RegistryError, "duplicate metric id"):
            self._load(MINIMAL + MINIMAL.split("schema_version = 2", 1)[1])

    def test_a_negative_precision_is_refused(self):
        with self.assertRaisesRegex(RegistryError, "display_precision"):
            self._load(MINIMAL.replace("display_precision = 0", "display_precision = -1"))


if __name__ == "__main__":
    unittest.main()
