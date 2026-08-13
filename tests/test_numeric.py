"""Formatting and parsing. The writer and the validator must agree exactly."""

import unittest

from veritas.numeric import (
    extract_dates,
    extract_numbers,
    format_signed,
    format_value,
    parse_number,
    values_match,
)


class FormatTests(unittest.TestCase):
    def test_currency(self):
        self.assertEqual(format_value(1234567.4, "usd", 0), "$1,234,567")
        # 58.815 has no exact binary representation, so it renders down.
        self.assertEqual(format_value(58.815, "usd", 2), "$58.81")

    def test_negative_currency_keeps_the_sign_outside(self):
        self.assertEqual(format_value(-37412.0, "usd", 0), "-$37,412")

    def test_percent_and_count(self):
        self.assertEqual(format_value(-8.34, "percent", 1), "-8.3%")
        self.assertEqual(format_value(53793.0, "count", 0), "53,793")

    def test_missing_renders_as_missing_never_as_zero(self):
        self.assertEqual(format_value(None, "usd", 0), "n/a")
        self.assertEqual(format_value(None, "percent", 2), "n/a")

    def test_signed_formatting(self):
        self.assertEqual(format_signed(4.2, "percent", 1), "+4.2%")
        self.assertEqual(format_signed(-4.2, "percent", 1), "-4.2%")


class ParseTests(unittest.TestCase):
    def test_round_trip_through_the_formatter(self):
        for value, unit, precision in [
            (1234567.0, "usd", 0),
            (-37412.0, "usd", 0),
            (-8.3, "percent", 1),
            (53793.0, "count", 0),
            (2.87, "percent", 2),
        ]:
            rendered = format_value(value, unit, precision)
            self.assertAlmostEqual(parse_number(rendered), value, msg=rendered)

    def test_unicode_minus_is_understood(self):
        self.assertEqual(parse_number("−8.3%"), -8.3)

    def test_non_numbers_return_none(self):
        self.assertIsNone(parse_number("n/a"))
        self.assertIsNone(parse_number("-"))


class ExtractTests(unittest.TestCase):
    def test_extracts_currency_percent_and_plain_numbers(self):
        tokens = extract_numbers("GMV was $1,234,567, down -8.3% on 53,793 sessions.")
        self.assertEqual([t.value for t in tokens], [1234567.0, -8.3, 53793.0])

    def test_iso_dates_are_not_numbers(self):
        tokens = extract_numbers("in the week ending 2026-08-08 GMV was $12")
        self.assertEqual([t.value for t in tokens], [12.0])

    def test_dates_are_extracted_separately(self):
        self.assertEqual(
            extract_dates("2026-08-02 to 2026-08-08"), ["2026-08-02", "2026-08-08"]
        )

    def test_a_bare_number_in_prose_is_still_found(self):
        # This is what makes an invented figure catchable.
        tokens = extract_numbers("roughly 3 times the usual volume")
        self.assertEqual([t.value for t in tokens], [3.0])

    def test_words_containing_digits_do_not_split_into_numbers(self):
        self.assertEqual(extract_numbers("segment channel=organic"), [])


class MatchTests(unittest.TestCase):
    def test_within_tolerance(self):
        self.assertTrue(values_match(4.3, 4.27, 0.05))

    def test_outside_tolerance(self):
        self.assertFalse(values_match(4.4, 4.27, 0.05))

    def test_missing_matches_only_missing(self):
        self.assertTrue(values_match(None, None, 0.5))
        self.assertFalse(values_match(0.0, None, 0.5))
        self.assertFalse(values_match(None, 0.0, 0.5))


if __name__ == "__main__":
    unittest.main()
