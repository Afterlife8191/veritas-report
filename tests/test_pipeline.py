"""End to end: the demo path, the retry loop, and the two ways a run fails."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests.support import BRIEF_PATH, REGISTRY_PATH, highlight, response, write_source
from veritas.cli import main
from veritas.generate import DEFAULT_SEED, generate_rows, write_csv
from veritas.llm import MockProvider, ScriptedProvider
from veritas.pipeline import (
    EXIT_INCOMPLETE,
    EXIT_OK,
    RunConfig,
    ValidationFailure,
    run,
)

AS_OF = date(2026, 8, 13)


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "out"

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, data_path: Path, **overrides) -> RunConfig:
        return RunConfig(
            data_path=data_path,
            registry_path=REGISTRY_PATH,
            brief_path=BRIEF_PATH,
            out_dir=self.out,
            as_of=AS_OF,
            **overrides,
        )

    def real_data(self) -> Path:
        return write_csv(self.tmp / "storefront.csv", generate_rows(seed=DEFAULT_SEED))


class DemoPathTests(PipelineTestCase):
    def setUp(self):
        super().setUp()
        self.result = run(self.config(self.real_data()), provider=MockProvider())

    def test_the_run_passes_with_no_api_key(self):
        self.assertTrue(self.result.report.passed, self.result.report.feedback())
        self.assertEqual(self.result.exit_code, EXIT_OK)

    def test_it_takes_a_single_attempt(self):
        self.assertEqual(len(self.result.attempts), 1)
        self.assertTrue(self.result.attempts[0]["passed"])

    def test_it_writes_the_four_artifacts(self):
        for name in ("facts", "pack", "report", "audit"):
            self.assertTrue(self.result.written[name].exists(), name)

    def test_the_report_has_highlights_and_a_coverage_section(self):
        self.assertIn("## Highlights", self.result.markdown)
        self.assertIn("## Coverage", self.result.markdown)
        self.assertNotIn("INCOMPLETE RUN", self.result.markdown)

    def test_every_published_claim_is_bound(self):
        self.assertTrue(self.result.report.bindings)
        self.assertEqual({b.status for b in self.result.report.bindings}, {"bound"})

    def test_the_audit_traces_each_claim_back_to_a_computation(self):
        for claim in self.result.audit["claims"]:
            self.assertEqual(claim["status"], "bound")
            self.assertTrue(claim["derivation"])
            self.assertTrue(claim["derivation"][0]["formula"])

    def test_the_audit_records_the_inputs_by_hash(self):
        inputs = self.result.audit["inputs"]
        self.assertEqual(len(inputs["data_sha256"]), 64)
        self.assertEqual(len(inputs["registry_sha256"]), 64)
        self.assertGreater(inputs["rows"], 0)

    def test_the_audit_does_not_pin_the_machine_that_produced_it(self):
        # The trail is meant to be shared; identity comes from the hashes.
        for key in ("data_file", "registry_file", "brief_file"):
            self.assertFalse(
                self.result.audit["inputs"][key].startswith("/"),
                self.result.audit["inputs"][key],
            )

    def test_the_renderer_had_nothing_to_redact(self):
        self.assertEqual(self.result.audit["render"]["redactions"], 0)

    def test_the_writer_only_used_facts_it_was_shown(self):
        pack_ids = self.result.book.pack_fact_ids()
        for claim in self.result.audit["claims"]:
            self.assertIn(claim["fact_id"], pack_ids)

    def test_the_run_is_reproducible(self):
        again = run(self.config(self.real_data()), provider=MockProvider())
        self.assertEqual(again.book.run_id, self.result.book.run_id)
        self.assertEqual(again.markdown, self.result.markdown)


class RetryTests(PipelineTestCase):
    def test_a_rejected_draft_is_sent_back_with_the_violations(self):
        bad = response([highlight(narrative="GMV was $9,999,999,999 this week.")])
        good = response([])
        provider = ScriptedProvider([bad, good])
        result = run(self.config(self.real_data()), provider=provider)

        self.assertTrue(result.report.passed)
        self.assertEqual(len(result.attempts), 2)
        self.assertFalse(result.attempts[0]["passed"])
        codes = {v["code"] for v in result.attempts[0]["violations"]}
        self.assertIn("E020_UNBOUND_NUMBER", codes)
        # The second prompt carries the validator's report back to the writer.
        self.assertIn("validator_report", provider.prompts[1])
        self.assertIn("E020_UNBOUND_NUMBER", provider.prompts[1])

    def test_a_writer_that_never_complies_fails_loudly(self):
        bad = response([highlight(narrative="GMV was $9,999,999,999 this week.")])
        provider = ScriptedProvider([bad])
        with self.assertRaises(ValidationFailure) as caught:
            run(self.config(self.real_data(), max_retries=2), provider=provider)

        self.assertEqual(caught.exception.attempts, 3)
        self.assertIn("E020_UNBOUND_NUMBER", str(caught.exception))

    def test_a_failed_run_writes_an_audit_trail_but_no_report(self):
        provider = ScriptedProvider([response([highlight(narrative="GMV was $9,999,999,999.")])])
        with self.assertRaises(ValidationFailure):
            run(self.config(self.real_data(), max_retries=1), provider=provider)

        self.assertFalse((self.out / "report.md").exists())
        audit = json.loads((self.out / "audit.json").read_text())
        self.assertFalse(audit["validation"]["passed"])
        self.assertEqual(len(audit["writer"]["attempts"]), 2)

    def test_the_retry_budget_is_honoured(self):
        provider = ScriptedProvider([response([highlight(narrative="GMV was $1.")])])
        with self.assertRaises(ValidationFailure):
            run(self.config(self.real_data(), max_retries=0), provider=provider)
        self.assertEqual(len(provider.prompts), 1)


class FailClosedTests(PipelineTestCase):
    def test_an_incomplete_input_produces_a_banner_and_a_non_zero_exit(self):
        # Data stops before the reported window is covered.
        path = write_source(self.tmp / "short.csv", date(2026, 7, 1), date(2026, 8, 12))
        result = run(self.config(path), provider=MockProvider())

        self.assertTrue(result.book.incomplete)
        self.assertEqual(result.exit_code, EXIT_INCOMPLETE)
        self.assertIn("INCOMPLETE RUN", result.markdown)
        self.assertEqual(result.payload["highlights"], [])

    def test_stale_data_is_reported_as_stale_rather_than_computed(self):
        path = write_source(self.tmp / "stale.csv", date(2026, 5, 1), date(2026, 8, 1))
        result = run(self.config(path), provider=MockProvider())
        statuses = {entry["metric_id"]: entry["status"] for entry in result.book.coverage}
        self.assertEqual(set(statuses.values()), {"stale"})
        self.assertIn("| stale |", result.markdown)


class CliTests(PipelineTestCase):
    def test_the_demo_command_runs_clean(self):
        code = main(
            [
                "demo",
                "--data",
                str(self.tmp / "demo.csv"),
                "--out",
                str(self.out),
                "--registry",
                str(REGISTRY_PATH),
                "--brief",
                str(BRIEF_PATH),
            ]
        )
        self.assertEqual(code, EXIT_OK)
        self.assertTrue((self.out / "report.md").exists())

    def test_a_missing_api_key_exits_one_without_a_traceback(self):
        data = self.real_data()
        code = main(
            [
                "run",
                "--data",
                str(data),
                "--out",
                str(self.out),
                "--registry",
                str(REGISTRY_PATH),
                "--brief",
                str(BRIEF_PATH),
                "--provider",
                "anthropic",
            ]
        )
        self.assertEqual(code, 1)

    def test_a_bad_input_exits_one_without_a_traceback(self):
        broken = self.tmp / "broken.csv"
        broken.write_text("nope\n", encoding="utf-8")
        code = main(["run", "--data", str(broken), "--out", str(self.out)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
