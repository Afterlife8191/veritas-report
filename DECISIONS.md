# Decisions

Design choices this project made, why, and what would reopen them. Ordered by
how much they shape the code.

---

## D1 — The pipeline computes everything and decides nothing

The statistics layer computes every comparison for every metric and every
segment, and never judges salience. Selection is the writer's job; verification
is the validator's. Splitting it this way means "what is worth reporting" —
fuzzy, contextual, and a moving target — never has to be encoded as a threshold,
while "is this number right" never has to be trusted to a model.

*Reopen if:* a deterministic rule ever proves as good as judgement at picking
highlights. Then the LLM stops earning its place.

## D2 — The validator binds prose to facts, not the other way round

Numeric tokens in the narrative are compared against the **computed** value of
the fact each claim points at, never against the value the claim states. A
writer that fabricates a figure and then faithfully repeats it in its `claims`
array would pass a claim-only check; it fails this one
(`test_prose_is_checked_against_the_computed_value_not_the_claim`).

## D3 — Tolerance is half a unit of the last displayed decimal

A claim binds when it agrees with the fact at the precision the report prints.
Anything looser lets a wrong number through; anything tighter rejects correct
rounding. Each metric declares `display_precision`, so the tolerance is a
property of the metric rather than a global constant.

## D4 — Dates are bound too, and only in ISO form

A date in the prose must bound a period of a cited fact. That makes "in the week
ending 2026-08-08" checkable, at the cost of forbidding "last Tuesday" and
"early August". Prose loses a little; the guarantee stays total. Month-name
dates were considered and dropped — parsing them is a second, weaker binding
path for no real gain.

## D5 — Any unbound number fails, including ordinary prose numerals

"Roughly 3 times the usual level" fails, because 3 binds to nothing. This is
deliberately strict: an exception for "small integers in prose" is exactly the
hole a hallucinated figure fits through. The cost is that the writer must
express counts in words, and the system prompt says so.

## D6 — Standard library only

No pandas, no numpy, no pytest, no YAML. The dataset is ~16,000 rows and the
statistics are means, medians and a MAD; pandas would add an install step and a
version-compatibility surface to save a few lines. The registry is TOML because
`tomllib` is in the standard library, and the tests are `unittest.TestCase`
classes, which run under both `python -m unittest` and `pytest`.

The consequence a reader should notice: `make demo` and `make test` work on a
clean checkout with nothing installed.

*Reopen if:* the source grows past a few million rows, where a columnar engine
starts to matter more than the install cost.

## D7 — Two providers, and the mock is a first-class citizen

`MockProvider` receives the same prompt a real model would, recovers the pack
from it, and is bound by the same rules — it can and does fail validation when
it gets something wrong (that is how the null-value bug in
`_disappearance_highlight` was found). It is a stand-in for judgement, not a
simulation of a model.

`AnthropicProvider` is imported lazily so the optional dependency is genuinely
optional, and the API key is read from the environment and never recorded.

## D8 — The same-weekday baseline is eight observations, and the MAD is rescaled

The first implementation used four same-weekday points and a raw MAD. Measured
on the generated data, that gave a median |robust score| of 1.8 and a 90th
percentile above 7: ordinary Tuesdays looked like anomalies. Eight points plus
the standard 1.4826 consistency constant (which makes the MAD a
standard-deviation estimator) brought the median to ~1.2. It is still hotter
than a well-behaved z, and the report says so rather than pretending otherwise.

## D9 — Coverage is checked per day, not per period

An early version checked that each reported period had at least one row. A
source that stopped one day early passed it — every period still had rows —
while the partial week was silently compared against baselines a day longer than
itself. The check is now on the delivered day grid
(`test_a_source_ending_one_day_early_is_caught_by_the_day_grid`).

This assumes the source is a daily aggregate feed that emits rows even on a
quiet day. For an event stream, absence of rows would mean absence of business
and this check would be wrong.

## D10 — Segment labels are constrained at the door, not sanitized downstream

Dimension values must match a conservative character vocabulary. A label
carrying markup is a hard failure naming the column and **withholding the
value**; it is not silently stripped, because a sanitizer that quietly drops
characters hides whatever upstream defect produced them. An instruction-shaped
label made only of letters ("IGNORE ALL PREVIOUS INSTRUCTIONS…") is allowed
through as ordinary data and changes nothing — both cases are fixtures in
`tests/test_writer.py`.

## D11 — Adequacy flags are for rates only

`min(k, n-k) < 5` is a statement about a proportion. Applying it to a mean, whose
"numerator" is money and whose "denominator" is a count, produced nonsense flags
on AOV until the rule was scoped to `statistic = "rate"`.

## D12 — Fail-closed has two exit codes

Exit 2 is a validation failure: no report is written, the audit trail is, and
the violations go to stderr. Exit 3 is an incomplete run: a required metric was
unusable, so the report ships with a banner and the coverage table, and the exit
code stops any downstream automation treating it as clean. Silence never reads
as "nothing happened" — a metric that could not be computed is listed.

## D13 — The pack is bounded by selection channels, and states its own coverage

The writer sees overall figures plus a per-dimension shortlist chosen by several
channels at once: top-|screening z|, top-|contribution|, newly appeared, and
disappeared. One channel would be a gate in disguise — a brand-new segment has
no z-score at all, and would never be seen. The pack carries the coverage list,
and the full facts file ships alongside it, so what was left out stays visible.

## D14 — The writer's derivations are not in its input

Pack entries carry id, label, value, display string and flags — not provenance.
Provenance exists for the audit trail, and sending it to the model would only
enlarge the prompt and invite the model to reason about derivations it must not
recompute.

## D15 — Every field the report prints is checked, not just the prose

The narrative and title are scanned for unbound numbers, dates, causal language
and markup. The other fields a highlight carries are *also* rendered, so each is
bound some other way: `metric_id` against the registry, `severity` against an
enum, `cut` against the segments actually computed for that metric, and each
dismissal's `reason_code` against the agreed vocabulary. Without the `cut` check
a writer could put free text — including numbers — into a field that reaches the
document unscanned.

## D16 — Prose binds to the published rendering, not to the value

A numeric token in the narrative must be a cited fact's `display` string, digit
for digit, written in that fact's unit. The earlier rule — match the value
within tolerance — let three things through, all confirmed by an independent
adversarial review: the raw float `$1,234,567.4` where the report published
`$1,234,567`; any figure inside the tolerance band of a zero-valued fact
("slipped 0.4 points" on the strength of a fact that reads `$0`); and a
percentage-point delta printed as a percentage, the exact confusion `pp` was
introduced to prevent.

Tolerance still governs **claims**, where rounding genuinely has to be allowed.
It has no business governing prose, because prose is what the reader sees.

## D17 — A claim must be about the highlight it appears under, and anchored in the week

A highlight is rendered as a row headed by its metric and segment. Claims were
previously checked only against the facts file, so a highlight titled "Orders
collapsed in country=BR" could cite a GMV-overall figure and pass with the audit
trail dutifully recording the mismatch as `bound`. Two rules close it: every
claim's fact must match the highlight's `metric_id` and `cut`, and at least one
cited fact must fall inside the period under review — otherwise nothing stops a
baseline week being narrated as "this week".

## D18 — Thousands separators must be grouped correctly

`parse_number` used to strip commas unconditionally, so `-10,0%` parsed as
`-100.0` and bound cleanly to a channel that had gone to zero — a tenfold error
rendered as a rounding. Commas are now only accepted in well-formed groups of
three; anything else falls through to the stray-numeral check.

---

## Deliberately out of scope

Controls a production system of this shape would need, left out here on purpose.
Each is listed with what it would take to make it worth building, so the absence
reads as a decision rather than an oversight.

* **Minimum-cell suppression and residual bucketing.** A privacy control for
  cells that identify people. Aggregate storefront metrics by country and
  channel identify nobody, so the machinery would have been decorative — and the
  interesting part (that suppression must compose across grains, or a withheld
  cell is recoverable as a subtraction one grain over) needs a real disclosure
  risk to be worth its complexity.
* **Outcome metrics measured at equal follow-up age.** Nothing in the toy domain
  accrues after the fact: an order is an order the day it is placed. Modelling
  maturity windows with no late-settling metric would have been ceremony.
* **Per-dependency freshness probes and SLA matrices.** One file, one freshness
  rule. Probe kinds (replica lag versus watermark) are a question about
  heterogeneous upstreams, and this demonstrator has one.
* **Month-to-date periods.** Weekly and daily comparisons already exercise the
  period machinery, including the partial-period rule; a third family would add
  volume, not insight.
* **A second model reviewing the first.** The deterministic validator is the
  interesting control, and it does not get better by adding another model in
  front of it. A challenger pass belongs in a system with a much larger pack.
* **Timezone conversion before bucketing.** The source is dated in local days
  already, so there is nothing to convert. In a system reading timestamps this
  is a real hazard — bucketing before converting is off by one for a whole
  timezone — and would need its own tests.
