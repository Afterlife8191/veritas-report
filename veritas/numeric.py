"""Number formatting, extraction and comparison.

One module owns how a number becomes text and how text becomes a number again, so
the writer's rendering and the validator's parsing can never drift apart. If they
did, the validator would either wave through wrong numbers or reject correct ones.

Two rules here carry more weight than they look:

* **Thousands separators must be grouped correctly.** ``-10,0%`` is not a valid
  rendering of anything. Stripping commas unconditionally would parse it as
  ``-100.0``, letting a tenfold error bind to a real fact.
* **A number carries its unit.** ``0.09pp`` and ``0.09%`` are different claims,
  and telling them apart is exactly what an unaided reader gets wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNICODE_MINUS = "−"

#: Currency/percent/point/thousands-separated numbers, signed or not. Comma
#: groups must be exactly three digits: an ungrouped comma is not part of the
#: number, so the remainder falls through to the stray-numeral check instead of
#: being silently absorbed.
NUMBER_PATTERN = re.compile(
    r"(?<![\w.,])[-+−]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|pp)?",
)

#: ISO dates only. Any other date spelling is unbindable, so it is not allowed.
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

#: How a number is written tells you what it measures.
KIND_USD = "usd"
KIND_PERCENT = "percent"
KIND_POINTS = "pp"
KIND_BARE = "bare"

#: Unit of a fact -> the way a number quoting it has to be written.
UNIT_KINDS = {
    "usd": KIND_USD,
    "percent": KIND_PERCENT,
    "pp": KIND_POINTS,
    "count": KIND_BARE,
    "score": KIND_BARE,
}


@dataclass(frozen=True)
class NumberToken:
    raw: str
    value: float
    kind: str
    start: int
    end: int

    @property
    def digits(self) -> str:
        """The token stripped of sign marks, currency and unit."""
        return normalize_digits(self.raw)


def format_value(value: float | None, unit: str, precision: int) -> str:
    """Render a number the one canonical way.

    ``None`` renders as ``n/a`` -- a missing value is shown as missing, never as
    zero.
    """
    if value is None:
        return "n/a"
    if unit == "usd":
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.{precision}f}"
    if unit == "percent":
        return f"{value:,.{precision}f}%"
    if unit == "pp":
        # The difference between two rates is percentage points, not percent.
        # Rendering "+0.16%" beside a relative change of "+5.0%" invites exactly
        # the misreading the distinction exists to prevent.
        return f"{value:,.{precision}f}pp"
    return f"{value:,.{precision}f}"


def format_signed(value: float | None, unit: str, precision: int) -> str:
    """Like :func:`format_value` but always carries an explicit sign."""
    if value is None:
        return "n/a"
    body = format_value(abs(value), unit, precision)
    return f"{'-' if value < 0 else '+'}{body}"


def normalize_digits(raw: str) -> str:
    """A token reduced to sign and digits, with separators and unit marks gone."""
    cleaned = raw.strip().replace(UNICODE_MINUS, "-").replace("$", "").replace(",", "")
    cleaned = cleaned.removesuffix("pp").removesuffix("%")
    return cleaned.lstrip("+")


def _grouping_ok(raw: str) -> bool:
    """True when any commas in ``raw`` are well-formed thousands separators."""
    body = raw.strip().replace(UNICODE_MINUS, "-").lstrip("-+$")
    body = body.removesuffix("pp").removesuffix("%")
    if "," not in body:
        return True
    return re.fullmatch(r"\d{1,3}(?:,\d{3})+", body.split(".", 1)[0]) is not None


def parse_number(raw: str) -> float | None:
    """Turn one formatted token back into a float, or ``None`` if it is not one."""
    if not _grouping_ok(raw):
        return None
    cleaned = normalize_digits(raw)
    if cleaned in ("", "-", "+"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def token_kind(raw: str, following: str = "") -> str:
    """Classify a token by how it is written, including the text right after it."""
    stripped = raw.strip().replace(UNICODE_MINUS, "-")
    if stripped.endswith("pp") or following.startswith("pp"):
        return KIND_POINTS
    if stripped.endswith("%"):
        return KIND_PERCENT
    if "$" in stripped:
        return KIND_USD
    return KIND_BARE


def extract_numbers(text: str) -> list[NumberToken]:
    """Every numeric token in ``text``, in order.

    Dates are removed first: they are bound against period bounds, not values, so
    letting them through here would report a year as an unbound number.
    """
    masked = DATE_PATTERN.sub(lambda m: " " * len(m.group(0)), text)
    tokens: list[NumberToken] = []
    for match in NUMBER_PATTERN.finditer(masked):
        raw = match.group(0)
        value = parse_number(raw)
        if value is None:
            continue
        tokens.append(
            NumberToken(
                raw=raw,
                value=value,
                kind=token_kind(raw, masked[match.end() : match.end() + 2]),
                start=match.start(),
                end=match.end(),
            )
        )
    return tokens


def extract_dates(text: str) -> list[str]:
    return DATE_PATTERN.findall(text)


def residual_digits(text: str) -> str:
    """Numerals left in ``text`` once dates and numeric tokens are removed.

    A catch-all for figures the token scanner does not read as a number of its
    own: the exponent in ``1.2e6``, a digit glued to a word, a mis-grouped
    thousands separator, a vulgar fraction. Anything left here reaches the reader
    as part of a figure while binding to nothing.
    """
    masked = DATE_PATTERN.sub(lambda m: " " * len(m.group(0)), text)
    masked = NUMBER_PATTERN.sub(lambda m: " " * len(m.group(0)), masked)
    return "".join(char for char in masked if char.isnumeric())


def values_match(claimed: float | None, computed: float | None, tolerance: float) -> bool:
    """Agreement at the precision the report prints.

    ``None`` matches only ``None``: a missing value cannot be quoted as a number.
    """
    if claimed is None or computed is None:
        return claimed is None and computed is None
    return abs(claimed - computed) <= tolerance
