"""The validator: nothing reaches the report until this file agrees.

Everything upstream of here is advisory. The prompt asks the writer to quote only
computed facts; this module *checks* it, deterministically, with no model in the
loop. If a number in the prose cannot be bound to a fact that the pipeline
computed, the report does not ship.

Rules, in the order they are applied:

``structure``
    The response parses and every highlight has the fields the contract requires.
``bound claims``
    Every claim resolves to a fact **that was in the pack** and agrees with the
    computed value at the pack's display precision. A number that merely exists
    somewhere in the pack does not pass -- a correct figure attributed to the
    wrong metric, segment or period is a violation, and so is an id from another
    run.
``subject and period``
    Every claim must be about the metric and segment the highlight is headed
    with, and at least one cited fact must fall inside the period under review.
    A correct number from another series, or from a baseline week narrated as
    "this week", is a lie about what it measures.
``narrative consistency``
    Every numeric token in the prose must be the **published** rendering of a
    cited fact -- its ``display`` string, digit for digit, in the unit it was
    written in. Binding on the underlying value would let ``$1,234,567.4`` or a
    figure merely inside the tolerance band onto the page; binding without the
    unit would let a percentage-point delta be printed as a percentage. Dates
    must bound a period of a cited fact, magnitudes stated in words are refused,
    and no stray numeral may survive the scan -- the exponent in ``1.2e6``, or a
    comma in the wrong place, binds to nothing while changing what a reader sees.
``field binding``
    The fields that are printed but are not prose are bound too -- ``metric_id``
    to the registry, ``cut`` to a segment that was actually computed, ``severity``
    and each dismissal's ``reason_code`` to their vocabularies. A field that
    reaches the document unchecked is a channel for whatever the writer likes.
``causal labelling``
    Causal language may appear only in the ``hypothesis`` field. The lexicon is a
    backstop, not a guarantee: the real control is that fact and hypothesis are
    separate fields, and that a human reads the report.
``render safety``
    No HTML, script fragment, link (bare hostnames included) or block-level
    markdown survives into the document. A narrative that opens with ``## `` would
    otherwise forge a section heading at the report's own level.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .facts import Fact, FactBook, cut_label
from .numeric import (
    UNIT_KINDS,
    extract_dates,
    extract_numbers,
    residual_digits,
    values_match,
)
from .registry import Registry
from .writer import REASON_CODES, SEVERITIES, WriterOutputError, parse_output

# Violation codes. Stable identifiers -- they appear in the audit trail and in
# the feedback handed back to the writer on a retry.
E_SCHEMA = "E001_SCHEMA"
E_UNKNOWN_METRIC = "E002_UNKNOWN_METRIC"
E_BAD_SEVERITY = "E003_BAD_SEVERITY"
E_UNKNOWN_CUT = "E004_UNKNOWN_CUT"
E_DANGLING_FACT = "E010_DANGLING_FACT"
E_FACT_NOT_IN_PACK = "E011_FACT_NOT_IN_PACK"
E_VALUE_MISMATCH = "E012_VALUE_MISMATCH"
E_NULL_FACT_QUOTED = "E013_NULL_FACT_QUOTED"
E_CLAIM_OFF_SUBJECT = "E014_CLAIM_OFF_SUBJECT"
E_NO_CURRENT_PERIOD = "E015_NO_CURRENT_PERIOD"
E_UNBOUND_NUMBER = "E020_UNBOUND_NUMBER"
E_UNBOUND_DATE = "E021_UNBOUND_DATE"
E_STRAY_DIGITS = "E022_STRAY_DIGITS"
E_WRONG_UNIT = "E023_WRONG_UNIT"
E_UNQUANTIFIED_MAGNITUDE = "E024_UNQUANTIFIED_MAGNITUDE"
E_CAUSAL_OUTSIDE_HYPOTHESIS = "E030_CAUSAL_OUTSIDE_HYPOTHESIS"
E_FORBIDDEN_MARKUP = "E040_FORBIDDEN_MARKUP"
E_BAD_REASON_CODE = "E050_BAD_REASON_CODE"
E_UNKNOWN_SHORTLIST_RANK = "E051_UNKNOWN_SHORTLIST_RANK"

#: Words that assert *why* something happened. Allowed only inside `hypothesis`.
#: Participles matter as much as the base verbs: "a promo push driving GMV" is
#: the same assertion as "GMV driven by a promo push".
CAUSAL_LEXICON = re.compile(
    r"\b(because|due to|driven by|driving|drove|caused by|causing|caused|"
    r"as a result of|thanks to|owing to|led to|leading to|resulted in|"
    r"resulting from|explains|explained by|attributable to|triggered by|"
    r"triggering|sparked|sparking|stems from|stemming from|reflects|"
    r"reflecting|responsible for|blame|blamed on|the impact of|"
    r"contributed to|prompted by|following a|following the)\b",
    re.IGNORECASE,
)

#: Quantity words that state a magnitude without a bindable figure. The audience
#: brief forbids approximations; this is what enforces it.
MAGNITUDE_LEXICON = re.compile(
    r"\b(roughly|approximately|about|around|nearly|almost|some|several|"
    r"dozens|hundreds|thousands|millions|billions|halved|doubled|tripled|"
    r"quadrupled|a (?:half|third|quarter|fifth|tenth)|"
    r"(?:a|one|two|three|four|five) (?:million|billion|thousand))\b",
    re.IGNORECASE,
)

#: Anything that would stop the rendered report being inert text: HTML, scripts,
#: links (including bare hosts, which many renderers autolink), inline and
#: reference-style link syntax, and block-level markdown that would let the
#: writer forge a section heading or list at the document's own level.
FORBIDDEN_MARKUP = re.compile(
    r"(<\s*[a-zA-Z/!]"
    r"|javascript:"
    r"|https?://"
    r"|!\["
    r"|]\("
    r"|]\["
    r"|\b[\w-]+\.(?:com|net|org|io|dev|ai|co|app|xyz|example)\b"
    r"|^\s{0,3}#{1,6}\s"
    r"|^\s{0,3}[-*+>]\s"
    r"|^\s{0,3}\d+\.\s)",
    re.IGNORECASE | re.MULTILINE,
)

REQUIRED_HIGHLIGHT_FIELDS = ("title", "metric_id", "cut", "severity", "narrative", "claims")

#: Fields whose text is scanned for numbers, dates, causal language and markup.
PROSE_FIELDS = ("title", "narrative")


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    highlight_index: int | None = None
    claim_index: int | None = None
    fact_id: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class ClaimBinding:
    """One claim, resolved against the facts file. The audit trail's unit."""

    highlight_index: int
    claim_index: int
    fact_id: str
    claimed_value: float | None
    computed_value: float | None
    tolerance: float | None
    status: str  # "bound" | "mismatch" | "dangling" | "not_in_pack" | "off_subject"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    violations: list[Violation] = field(default_factory=list)
    bindings: list[ClaimBinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def failing_highlights(self) -> set[int]:
        return {v.highlight_index for v in self.violations if v.highlight_index is not None}

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "claims": [b.to_dict() for b in self.bindings],
        }

    def feedback(self) -> list[dict]:
        """The violation list handed back to the writer on a retry."""
        return [v.to_dict() for v in self.violations]


def validate_response(text: str, book: FactBook, registry: Registry) -> tuple[dict | None, ValidationReport]:
    """Parse and validate one writer response."""
    report = ValidationReport()
    try:
        payload = parse_output(text)
    except WriterOutputError as exc:
        report.violations.append(Violation(E_SCHEMA, str(exc)))
        return None, report

    pack_ids = book.pack_fact_ids()

    for index, highlight in enumerate(payload["highlights"]):
        _validate_highlight(index, highlight, book, registry, pack_ids, report)

    _validate_dismissals(payload["dismissals"], book, report)
    return payload, report


def _validate_highlight(
    index: int,
    highlight,
    book: FactBook,
    registry: Registry,
    pack_ids: set[str],
    report: ValidationReport,
) -> None:
    if not isinstance(highlight, dict):
        report.violations.append(Violation(E_SCHEMA, "highlight is not an object", index))
        return

    missing = [f for f in REQUIRED_HIGHLIGHT_FIELDS if f not in highlight]
    if missing:
        report.violations.append(
            Violation(E_SCHEMA, f"highlight is missing fields: {', '.join(missing)}", index)
        )
        return

    metric_id = str(highlight["metric_id"])
    if not registry.has(metric_id):
        report.violations.append(
            Violation(
                E_UNKNOWN_METRIC,
                f"metric_id {metric_id!r} is not in the registry",
                index,
            )
        )
    elif str(highlight["cut"]) not in book.cut_labels(metric_id):
        # The rendered report prints this string. Checking it against the
        # segments that actually exist keeps it from becoming an unscanned
        # channel for free text.
        report.violations.append(
            Violation(
                E_UNKNOWN_CUT,
                f"cut {highlight['cut']!r} is not a segment computed for "
                f"{metric_id!r}",
                index,
            )
        )

    if highlight["severity"] not in SEVERITIES:
        report.violations.append(
            Violation(
                E_BAD_SEVERITY,
                f"severity {highlight['severity']!r} is not one of {list(SEVERITIES)}",
                index,
            )
        )

    bound_facts, bound_dates = _validate_claims(index, highlight, book, pack_ids, report)
    _validate_prose(index, highlight, bound_facts, bound_dates, report)


def _validate_claims(
    index: int,
    highlight: dict,
    book: FactBook,
    pack_ids: set[str],
    report: ValidationReport,
) -> tuple[list[Fact], set[str]]:
    """Resolve claims; return the facts and dates the prose may draw on."""
    claims = highlight["claims"]
    if not isinstance(claims, list):
        report.violations.append(Violation(E_SCHEMA, "'claims' must be a list", index))
        return [], set()

    bound_facts: list[Fact] = []
    bound_dates: set[str] = set()

    for claim_index, claim in enumerate(claims):
        if not isinstance(claim, dict) or "fact_id" not in claim or "value" not in claim:
            report.violations.append(
                Violation(E_SCHEMA, "claim needs 'fact_id' and 'value'", index, claim_index)
            )
            continue

        fact_id = str(claim["fact_id"])
        fact = book.get(fact_id)
        if fact is None:
            report.violations.append(
                Violation(
                    E_DANGLING_FACT,
                    f"no fact {fact_id!r} exists in this run",
                    index,
                    claim_index,
                    fact_id,
                )
            )
            report.bindings.append(
                ClaimBinding(index, claim_index, fact_id, _as_float(claim["value"]), None, None, "dangling")
            )
            continue

        if fact_id not in pack_ids:
            report.violations.append(
                Violation(
                    E_FACT_NOT_IN_PACK,
                    f"fact {fact_id!r} was computed but not shown to the writer",
                    index,
                    claim_index,
                    fact_id,
                )
            )
            report.bindings.append(
                ClaimBinding(
                    index, claim_index, fact_id, _as_float(claim["value"]), fact.value, fact.tolerance, "not_in_pack"
                )
            )
            continue

        claimed = _as_float(claim["value"])
        if fact.value is None:
            report.violations.append(
                Violation(
                    E_NULL_FACT_QUOTED,
                    f"fact {fact_id!r} has no value (flags: {', '.join(fact.flags) or 'none'}) "
                    "and cannot be quoted as a number",
                    index,
                    claim_index,
                    fact_id,
                )
            )
            report.bindings.append(
                ClaimBinding(index, claim_index, fact_id, claimed, None, fact.tolerance, "mismatch")
            )
            continue

        if not values_match(claimed, fact.value, fact.tolerance):
            report.violations.append(
                Violation(
                    E_VALUE_MISMATCH,
                    f"claimed {claimed} for {fact_id!r}, computed {fact.value} "
                    f"(tolerance {fact.tolerance})",
                    index,
                    claim_index,
                    fact_id,
                )
            )
            report.bindings.append(
                ClaimBinding(index, claim_index, fact_id, claimed, fact.value, fact.tolerance, "mismatch")
            )
            continue

        subject = _subject_mismatch(highlight, fact)
        if subject is not None:
            # A number can be perfectly correct and still be a lie about what it
            # measures. The highlight's own metric and segment head the report
            # row this claim is rendered under, so a claim has to be about them.
            report.violations.append(
                Violation(E_CLAIM_OFF_SUBJECT, subject, index, claim_index, fact_id)
            )
            report.bindings.append(
                ClaimBinding(index, claim_index, fact_id, claimed, fact.value, fact.tolerance, "off_subject")
            )
            continue

        report.bindings.append(
            ClaimBinding(index, claim_index, fact_id, claimed, fact.value, fact.tolerance, "bound")
        )
        bound_facts.append(fact)
        bound_dates.update({fact.period_start, fact.period_end})

    if bound_facts and not any(_is_current(book, fact) for fact in bound_facts):
        # Every fact cited is a baseline. Nothing stops the prose calling one of
        # them "this week", so the highlight has no anchor in the period the
        # report is about.
        report.violations.append(
            Violation(
                E_NO_CURRENT_PERIOD,
                "no cited fact falls in the period under review, so the highlight "
                "is not anchored to the week it is published in",
                index,
            )
        )

    return bound_facts, bound_dates


def _subject_mismatch(highlight: dict, fact: Fact) -> str | None:
    """Why this fact is not about the highlight's metric and segment."""
    if fact.metric_id != str(highlight["metric_id"]):
        return (
            f"fact {fact.id!r} measures {fact.metric_id!r}, but the highlight is "
            f"about {highlight['metric_id']!r}"
        )
    label = cut_label(fact.cut_dim, fact.cut_value)
    if label != str(highlight["cut"]):
        return (
            f"fact {fact.id!r} is for segment {label!r}, but the highlight is "
            f"about {highlight['cut']!r}"
        )
    return None


def _is_current(book: FactBook, fact: Fact) -> bool:
    """True when a fact belongs to the period the report is about."""
    latest = (book.partial_week or {}).get("end", book.week_end)
    return book.week_start <= fact.period_end <= latest


def _validate_prose(
    index: int,
    highlight: dict,
    bound_facts: list[Fact],
    bound_dates: set[str],
    report: ValidationReport,
) -> None:
    texts: dict[str, str] = {}
    for field_name in (*PROSE_FIELDS, "hypothesis"):
        value = highlight.get(field_name)
        if value is None:
            texts[field_name] = ""
            continue
        if not isinstance(value, str):
            # Anything else would be rendered as its Python repr.
            report.violations.append(
                Violation(E_SCHEMA, f"{field_name} must be a string", index)
            )
            texts[field_name] = ""
            continue
        texts[field_name] = value

    for field_name, text in texts.items():
        if FORBIDDEN_MARKUP.search(text):
            report.violations.append(
                Violation(
                    E_FORBIDDEN_MARKUP,
                    f"{field_name} contains markup, a link, a heading or a list "
                    "marker, which would not render as plain text",
                    index,
                )
            )

        for token in extract_numbers(text):
            candidates = [fact for fact in bound_facts if fact.digits == token.digits]
            if not candidates:
                report.violations.append(
                    Violation(
                        E_UNBOUND_NUMBER,
                        f"{field_name} contains {token.raw!r}, which is not the "
                        "published value of any claim in this highlight",
                        index,
                    )
                )
                continue
            if not any(UNIT_KINDS.get(fact.unit) == token.kind for fact in candidates):
                report.violations.append(
                    Violation(
                        E_WRONG_UNIT,
                        f"{field_name} writes {token.raw!r}, but that figure is in "
                        f"{candidates[0].unit!r}; the unit changes what it means",
                        index,
                    )
                )

        magnitude = MAGNITUDE_LEXICON.search(text)
        if magnitude:
            report.violations.append(
                Violation(
                    E_UNQUANTIFIED_MAGNITUDE,
                    f"{field_name} states a magnitude in words ({magnitude.group(0)!r}); "
                    "quantities must be quoted from a fact",
                    index,
                )
            )

        stray = residual_digits(text)
        if stray:
            # Digits the token scanner did not read as a number of their own --
            # an exponent, a digit glued to a word, a space-separated group.
            report.violations.append(
                Violation(
                    E_STRAY_DIGITS,
                    f"{field_name} contains numerals that are not part of any "
                    f"quotable number ({stray!r})",
                    index,
                )
            )

        for date in extract_dates(text):
            if date not in bound_dates:
                report.violations.append(
                    Violation(
                        E_UNBOUND_DATE,
                        f"{field_name} contains the date {date}, which bounds no cited fact",
                        index,
                    )
                )

    for field_name in PROSE_FIELDS:
        match = CAUSAL_LEXICON.search(texts[field_name])
        if match:
            report.violations.append(
                Violation(
                    E_CAUSAL_OUTSIDE_HYPOTHESIS,
                    f"{field_name} asserts causation ({match.group(0)!r}); causal claims "
                    "belong in the hypothesis field",
                    index,
                )
            )


def _validate_dismissals(dismissals, book: FactBook, report: ValidationReport) -> None:
    ranks = {item.rank for item in book.shortlist}
    for entry in dismissals:
        if not isinstance(entry, dict) or "reason_code" not in entry:
            report.violations.append(Violation(E_SCHEMA, "dismissal needs a 'reason_code'"))
            continue
        if entry["reason_code"] not in REASON_CODES:
            report.violations.append(
                Violation(
                    E_BAD_REASON_CODE,
                    f"reason_code {entry['reason_code']!r} is not in the agreed vocabulary",
                )
            )
        # The rank is printed in the report when it cannot be resolved, so an
        # invented one would be an unbound number in the document.
        if entry.get("shortlist_rank") not in ranks:
            report.violations.append(
                Violation(
                    E_UNKNOWN_SHORTLIST_RANK,
                    f"dismissal names shortlist rank {entry.get('shortlist_rank')!r}, "
                    "which was not offered to the writer",
                )
            )


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
