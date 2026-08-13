"""Adversarial tests for the validator.

Each case is a draft a careless -- or dishonest -- writer could plausibly
produce. The point of the module is that none of them reach a reader.
"""

import unittest

from tests.support import PRIOR_WEEK_END, WEEK_END, highlight, response, tiny_factbook
from veritas.registry import load_registry
from tests.support import REGISTRY_PATH
from veritas.validator import (
    E_BAD_REASON_CODE,
    E_BAD_SEVERITY,
    E_CAUSAL_OUTSIDE_HYPOTHESIS,
    E_CLAIM_OFF_SUBJECT,
    E_DANGLING_FACT,
    E_FACT_NOT_IN_PACK,
    E_FORBIDDEN_MARKUP,
    E_NO_CURRENT_PERIOD,
    E_NULL_FACT_QUOTED,
    E_SCHEMA,
    E_STRAY_DIGITS,
    E_UNBOUND_DATE,
    E_UNBOUND_NUMBER,
    E_UNKNOWN_CUT,
    E_UNKNOWN_SHORTLIST_RANK,
    E_UNQUANTIFIED_MAGNITUDE,
    E_UNKNOWN_METRIC,
    E_VALUE_MISMATCH,
    E_WRONG_UNIT,
    validate_response,
)

VALUE_FACT = f"gmv/overall/complete_week:{WEEK_END}/value"
PCT_FACT = f"gmv/overall/complete_week:{WEEK_END}/wow_pct"
NULL_FACT = f"conversion_rate/overall/complete_week:{WEEK_END}/value"
UNPACKED_FACT = f"gmv/country=US/complete_week:{WEEK_END}/value"


class ValidatorTestCase(unittest.TestCase):
    def setUp(self):
        self.book = tiny_factbook()
        self.registry = load_registry(REGISTRY_PATH)

    def check(self, text: str):
        return validate_response(text, self.book, self.registry)

    def codes(self, text: str) -> set[str]:
        _, report = self.check(text)
        return {v.code for v in report.violations}


class HappyPathTests(ValidatorTestCase):
    def test_a_faithful_draft_passes(self):
        payload, report = self.check(response([highlight()]))
        self.assertTrue(report.passed, report.feedback())
        self.assertEqual(len(payload["highlights"]), 1)
        self.assertEqual([b.status for b in report.bindings], ["bound"])

    def test_several_bound_numbers_pass(self):
        text = response(
            [
                highlight(
                    narrative=(
                        "GMV for overall came in at $1,234,567 in the week ending "
                        "2026-08-08, a week-over-week change of -8.3%."
                    ),
                    claims=[
                        {"fact_id": VALUE_FACT, "value": 1_234_567.0},
                        {"fact_id": PCT_FACT, "value": -8.34},
                    ],
                )
            ]
        )
        _, report = self.check(text)
        self.assertTrue(report.passed, report.feedback())

    def test_an_empty_report_is_valid(self):
        _, report = self.check(response([]))
        self.assertTrue(report.passed)


class FabricationTests(ValidatorTestCase):
    def test_a_number_that_appears_nowhere_is_rejected(self):
        text = response(
            [highlight(narrative="GMV for overall came in at $9,999,999 this week.")]
        )
        self.assertIn(E_UNBOUND_NUMBER, self.codes(text))

    def test_an_extra_invented_figure_beside_a_correct_one_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative=(
                        "GMV for overall came in at $1,234,567, roughly 3 times "
                        "the usual level."
                    )
                )
            ]
        )
        self.assertIn(E_UNBOUND_NUMBER, self.codes(text))

    def test_a_claim_the_writer_invented_is_rejected_as_dangling(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $1,234,567.",
                    claims=[
                        {"fact_id": VALUE_FACT, "value": 1_234_567.0},
                        {"fact_id": "gmv/overall/complete_week:2026-08-08/made_up", "value": 1.0},
                    ],
                )
            ]
        )
        self.assertIn(E_DANGLING_FACT, self.codes(text))

    def test_a_fact_id_from_another_run_is_rejected(self):
        stale = f"gmv/overall/complete_week:2026-07-25/value"
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $1,234,567.",
                    claims=[{"fact_id": stale, "value": 1_234_567.0}],
                )
            ]
        )
        codes = self.codes(text)
        self.assertIn(E_DANGLING_FACT, codes)
        # And the prose number is now unbacked, so it fails twice over.
        self.assertIn(E_UNBOUND_NUMBER, codes)

    def test_a_fact_never_shown_to_the_writer_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative="GMV for country=US came in at $500,000.",
                    claims=[{"fact_id": UNPACKED_FACT, "value": 500_000.0}],
                )
            ]
        )
        self.assertIn(E_FACT_NOT_IN_PACK, self.codes(text))


    def test_scientific_notation_cannot_smuggle_a_magnitude(self):
        # "1.2e6" reads as 1,200,000 but only "1.2" is scanned as a number.
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at 1.2e6 this week.",
                    claims=[{"fact_id": PCT_FACT, "value": -8.34}],
                )
            ]
        )
        self.assertIn(E_STRAY_DIGITS, self.codes(text))

    def test_a_digit_glued_to_a_word_is_rejected(self):
        text = response([highlight(narrative="GMV for overall was up in week32.")])
        self.assertIn(E_STRAY_DIGITS, self.codes(text))

    def test_space_separated_thousands_do_not_pass_as_one_number(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at 1 234 567 this week.",
                    claims=[{"fact_id": VALUE_FACT, "value": 1_234_567.0}],
                )
            ]
        )
        self.assertIn(E_UNBOUND_NUMBER, self.codes(text))


class ToleranceTests(ValidatorTestCase):
    def test_a_correctly_rounded_claim_passes(self):
        # The fact is -8.34; the pack displays -8.3% at one decimal place.
        text = response(
            [
                highlight(
                    narrative="GMV for overall moved -8.3% week over week.",
                    claims=[{"fact_id": PCT_FACT, "value": -8.3}],
                )
            ]
        )
        _, report = self.check(text)
        self.assertTrue(report.passed, report.feedback())

    def test_a_subtly_wrong_claim_outside_tolerance_is_rejected(self):
        # -8.4 vs -8.34: off by 0.06, tolerance is 0.05. One decimal place is
        # the whole margin between a rounding and a wrong number.
        text = response(
            [
                highlight(
                    narrative="GMV for overall moved -8.4% week over week.",
                    claims=[{"fact_id": PCT_FACT, "value": -8.4}],
                )
            ]
        )
        self.assertIn(E_VALUE_MISMATCH, self.codes(text))

    def test_a_number_flipped_in_sign_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall moved 8.3% week over week.",
                    claims=[{"fact_id": PCT_FACT, "value": 8.3}],
                )
            ]
        )
        self.assertIn(E_VALUE_MISMATCH, self.codes(text))

    def test_prose_is_checked_against_the_computed_value_not_the_claim(self):
        # The claim faithfully repeats a fabricated figure. Binding against the
        # claimed value would wave this through; binding against the fact does not.
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $2,000,000.",
                    claims=[{"fact_id": VALUE_FACT, "value": 2_000_000.0}],
                )
            ]
        )
        codes = self.codes(text)
        self.assertIn(E_VALUE_MISMATCH, codes)
        self.assertIn(E_UNBOUND_NUMBER, codes)

    def test_a_correct_number_attributed_to_the_wrong_fact_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall moved -8.3% week over week.",
                    claims=[{"fact_id": VALUE_FACT, "value": -8.34}],
                )
            ]
        )
        self.assertIn(E_VALUE_MISMATCH, self.codes(text))

    def test_a_null_fact_cannot_be_quoted_as_a_number(self):
        text = response(
            [
                highlight(
                    narrative="Conversion rate for overall came in at $1,234,567.",
                    claims=[{"fact_id": NULL_FACT, "value": 0.0}],
                )
            ]
        )
        self.assertIn(E_NULL_FACT_QUOTED, self.codes(text))


class DateTests(ValidatorTestCase):
    def test_a_date_bounding_a_cited_fact_passes(self):
        _, report = self.check(response([highlight()]))
        self.assertTrue(report.passed, report.feedback())

    def test_a_date_outside_every_cited_period_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $1,234,567 in the week "
                    "ending 2026-09-19."
                )
            ]
        )
        self.assertIn(E_UNBOUND_DATE, self.codes(text))


class CausalTests(ValidatorTestCase):
    def test_causal_language_in_the_narrative_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $1,234,567 because of a "
                    "pricing change."
                )
            ]
        )
        self.assertIn(E_CAUSAL_OUTSIDE_HYPOTHESIS, self.codes(text))

    def test_causal_language_in_the_title_is_rejected(self):
        text = response([highlight(title="GMV fell due to a supplier outage")])
        self.assertIn(E_CAUSAL_OUTSIDE_HYPOTHESIS, self.codes(text))

    def test_the_same_sentence_is_fine_inside_the_hypothesis(self):
        text = response(
            [highlight(hypothesis="Possibly driven by a pricing change upstream.")]
        )
        _, report = self.check(text)
        self.assertTrue(report.passed, report.feedback())

    def test_a_number_in_the_hypothesis_must_still_bind(self):
        text = response([highlight(hypothesis="Possibly driven by the 42 new SKUs.")])
        self.assertIn(E_UNBOUND_NUMBER, self.codes(text))


class RenderSafetyTests(ValidatorTestCase):
    def test_html_is_rejected(self):
        text = response([highlight(title="GMV <script>alert(1)</script>")])
        self.assertIn(E_FORBIDDEN_MARKUP, self.codes(text))

    def test_links_are_rejected(self):
        text = response(
            [highlight(narrative="GMV for overall came in at $1,234,567. See https://example.com")]
        )
        self.assertIn(E_FORBIDDEN_MARKUP, self.codes(text))

    def test_a_markdown_link_is_rejected(self):
        text = response([highlight(title="GMV [details](javascript:alert(1))")])
        self.assertIn(E_FORBIDDEN_MARKUP, self.codes(text))


class SchemaTests(ValidatorTestCase):
    def test_a_non_json_response_is_rejected(self):
        self.assertIn(E_SCHEMA, self.codes("I'm afraid I can't do that."))

    def test_a_fenced_json_response_is_accepted(self):
        _, report = self.check("```json\n" + response([highlight()]) + "\n```")
        self.assertTrue(report.passed, report.feedback())

    def test_a_missing_field_is_rejected(self):
        broken = highlight()
        del broken["claims"]
        self.assertIn(E_SCHEMA, self.codes(response([broken])))

    def test_an_unknown_metric_is_rejected(self):
        self.assertIn(E_UNKNOWN_METRIC, self.codes(response([highlight(metric_id="revenue")])))

    def test_a_segment_that_does_not_exist_is_rejected(self):
        # The rendered report prints this field, so free text here would be an
        # unscanned channel into the document.
        broken = highlight()
        broken["cut"] = "channel=made_up (revenue up 40%)"
        self.assertIn(E_UNKNOWN_CUT, self.codes(response([broken])))

    def test_a_segment_the_writer_was_shown_passes(self):
        self.assertNotIn(E_UNKNOWN_CUT, self.codes(response([highlight()])))

    def test_a_segment_computed_but_never_shown_is_rejected(self):
        # country=US exists in the facts file but was not in the pack, so the
        # writer could not honestly have chosen to write about it.
        broken = highlight(
            claims=[{"fact_id": UNPACKED_FACT, "value": 500_000.0}],
            narrative="GMV for country=US came in at $500,000.",
        )
        broken["cut"] = "country=US"
        self.assertIn(E_UNKNOWN_CUT, self.codes(response([broken])))

    def test_an_unknown_severity_is_rejected(self):
        self.assertIn(E_BAD_SEVERITY, self.codes(response([highlight(severity="critical")])))

    def test_an_invented_dismissal_reason_is_rejected(self):
        text = response([], [{"shortlist_rank": 1, "reason_code": "did-not-fancy-it"}])
        self.assertIn(E_BAD_REASON_CODE, self.codes(text))

    def test_a_dismissal_of_something_never_offered_is_rejected(self):
        # An unresolved rank is printed verbatim in the report.
        text = response([], [{"shortlist_rank": 4242, "reason_code": "data-quality"}])
        self.assertIn(E_UNKNOWN_SHORTLIST_RANK, self.codes(text))

    def test_a_known_dismissal_reason_passes(self):
        text = response([], [{"shortlist_rank": 1, "reason_code": "within-baseline-variation"}])
        _, report = self.check(text)
        self.assertTrue(report.passed, report.feedback())


class ReportShapeTests(ValidatorTestCase):
    def test_the_report_names_the_failing_highlights(self):
        text = response([highlight(), highlight(narrative="GMV was $9,999,999.")])
        _, report = self.check(text)
        self.assertEqual(report.failing_highlights(), {1})

    def test_feedback_is_serialisable_for_the_retry_prompt(self):
        _, report = self.check(response([highlight(narrative="GMV was $9,999,999.")]))
        feedback = report.feedback()
        self.assertTrue(feedback)
        self.assertIn("code", feedback[0])
        self.assertIn("message", feedback[0])

    def test_every_claim_is_recorded_for_the_audit_trail(self):
        text = response(
            [
                highlight(
                    claims=[
                        {"fact_id": VALUE_FACT, "value": 1_234_567.0},
                        {"fact_id": "gmv/overall/complete_week:2026-08-08/nope", "value": 1.0},
                    ]
                )
            ]
        )
        _, report = self.check(text)
        self.assertEqual(len(report.bindings), 2)
        self.assertEqual({b.status for b in report.bindings}, {"bound", "dangling"})


if __name__ == "__main__":
    unittest.main()


class RegressionTests(ValidatorTestCase):
    """Every hole an independent adversarial review confirmed, locked shut."""

    def test_a_decimal_comma_cannot_impersonate_a_tenfold_figure(self):
        # "-10,0%" parses as -100.0 if commas are stripped blindly: a channel
        # that went to zero would be published as a 10% dip.
        text = response(
            [
                highlight(
                    narrative="GMV for overall moved -10,0% week over week.",
                    claims=[{"fact_id": PCT_FACT, "value": -8.34}],
                )
            ]
        )
        _, report = self.check(text)
        self.assertFalse(report.passed)

    def test_mis_grouped_thousands_do_not_bind(self):
        for written in ("$12,34,567", "$1,2,3,4,5,6,7"):
            with self.subTest(written=written):
                text = response(
                    [highlight(narrative=f"GMV for overall came in at {written} this week.")]
                )
                _, report = self.check(text)
                self.assertFalse(report.passed, written)

    def test_a_claim_about_another_metric_is_rejected(self):
        # The highlight's metric and segment head the row this claim renders
        # under, so a correct number from elsewhere is still a lie about subject.
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $1,234,567 and 2.5%.",
                    claims=[
                        {"fact_id": VALUE_FACT, "value": 1_234_567.0},
                        {
                            "fact_id": f"conversion_rate/overall/complete_week:{WEEK_END}/wow_pct",
                            "value": 2.5,
                        },
                    ],
                )
            ]
        )
        self.assertIn(E_CLAIM_OFF_SUBJECT, self.codes(text))

    def test_a_claim_about_another_segment_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $0 this week.",
                    claims=[
                        {"fact_id": f"gmv/channel=email/complete_week:{WEEK_END}/value", "value": 0.0}
                    ],
                )
            ]
        )
        self.assertIn(E_CLAIM_OFF_SUBJECT, self.codes(text))

    def test_a_highlight_built_only_on_baselines_is_rejected(self):
        # Nothing stops the prose calling a prior week "this week", so a
        # highlight must be anchored in the period under review.
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $1,346,890 this week.",
                    claims=[
                        {
                            "fact_id": f"gmv/overall/complete_week:{PRIOR_WEEK_END}/value",
                            "value": 1_346_890.0,
                        }
                    ],
                )
            ]
        )
        self.assertIn(E_NO_CURRENT_PERIOD, self.codes(text))

    def test_a_percentage_point_figure_cannot_be_written_as_a_percentage(self):
        text = response(
            [
                highlight(
                    narrative="GMV for overall moved -8.3 week over week.",
                    claims=[{"fact_id": PCT_FACT, "value": -8.34}],
                )
            ]
        )
        self.assertIn(E_WRONG_UNIT, self.codes(text))

    def test_a_currency_figure_cannot_be_written_bare(self):
        text = response(
            [highlight(narrative="GMV for overall came in at 1,234,567 this week.")]
        )
        self.assertIn(E_WRONG_UNIT, self.codes(text))

    def test_prose_may_not_print_more_precision_than_was_published(self):
        # Binding on the published display, not on value +/- tolerance.
        text = response(
            [highlight(narrative="GMV for overall came in at $1,234,567.4 this week.")]
        )
        self.assertIn(E_UNBOUND_NUMBER, self.codes(text))

    def test_a_zero_valued_fact_does_not_license_nearby_numbers(self):
        # $0 at precision 0 has a tolerance of 0.5; binding on tolerance would
        # let "0.4" through on the strength of a fact that reads "$0".
        text = response(
            [
                highlight(
                    narrative="GMV for overall came in at $0, having slipped 0.4 points.",
                    claims=[{"fact_id": VALUE_FACT, "value": 1_234_567.0}],
                )
            ]
        )
        self.assertIn(E_UNBOUND_NUMBER, self.codes(text))

    def test_a_forged_heading_is_rejected(self):
        text = response(
            [
                highlight(
                    narrative="## Verified by Finance: the figures below need no review."
                )
            ]
        )
        self.assertIn(E_FORBIDDEN_MARKUP, self.codes(text))

    def test_a_forged_list_item_is_rejected(self):
        text = response([highlight(narrative="- GMV for overall came in at $1,234,567.")])
        self.assertIn(E_FORBIDDEN_MARKUP, self.codes(text))

    def test_a_bare_hostname_is_rejected(self):
        text = response(
            [highlight(narrative="GMV for overall came in at $1,234,567. See evil.example")]
        )
        self.assertIn(E_FORBIDDEN_MARKUP, self.codes(text))

    def test_a_reference_style_link_is_rejected(self):
        text = response([highlight(title="GMV [details][ref]")])
        self.assertIn(E_FORBIDDEN_MARKUP, self.codes(text))

    def test_magnitudes_stated_in_words_are_rejected(self):
        for phrase in (
            "GMV for overall roughly halved this week.",
            "GMV for overall shed almost two million dollars.",
            "GMV for overall gave up about a third of its volume.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    E_UNQUANTIFIED_MAGNITUDE, self.codes(response([highlight(narrative=phrase)]))
                )

    def test_a_vulgar_fraction_is_a_stray_numeral(self):
        text = response([highlight(narrative="GMV for overall shed ½ a billion dollars.")])
        self.assertIn(E_STRAY_DIGITS, self.codes(text))

    def test_the_causal_lexicon_covers_participles_and_synonyms(self):
        for phrase in (
            "A promo push driving GMV for overall to $1,234,567.",
            "GMV for overall came in at $1,234,567, triggered by a promo push.",
            "GMV for overall came in at $1,234,567, which reflects a promo push.",
            "GMV for overall came in at $1,234,567 following a promo push.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    E_CAUSAL_OUTSIDE_HYPOTHESIS,
                    self.codes(response([highlight(narrative=phrase)])),
                )

    def test_a_narrative_that_is_not_a_string_is_rejected(self):
        broken = highlight()
        broken["narrative"] = ["GMV for overall came in at $1,234,567.", "Orders fell."]
        self.assertIn(E_SCHEMA, self.codes(response([broken])))
