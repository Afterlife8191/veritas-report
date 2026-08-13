# Audience brief

The readers are the people who run the storefront: a product lead, a growth
lead, and the analyst who will be asked follow-up questions in the meeting. They
already look at the dashboard. They want to know what changed, how much, and
where — not a tour of everything that stayed the same.

## What earns a highlight

Judge candidates on magnitude, novelty, business impact, and whether anyone
could act on them. A move can be statistically extreme and still not worth
saying; a brand-new segment can have no comparison statistic at all and still be
the story of the week.

"Nothing notable this week" is a valid, useful answer for a metric. Prefer four
highlights that matter to twelve that fill a page.

## What the numbers are and are not

* `screening_z` and `robust_score` are **ranking heuristics** computed on short
  baselines — four weekly observations, four same-weekday observations. They are
  not calibrated significance tests. With dozens of metric-by-cut combinations,
  a handful of readings beyond |z| = 3 turn up every week by chance, and the
  comparisons are correlated (shared traffic, overlapping segments), so there is
  no clean false-positive count to quote. Extremity alone is not a highlight.
* A percentage change against a small base is arithmetically large and
  practically irrelevant. Check the absolute change too — it is in the pack.
* Facts carrying `small_denominator`, `small_cell`, `thin_baseline`, or
  `no_baseline` flags are fragile. Say so, or leave them alone.

## Facts and hypotheses are different things

Everything in the `narrative` field must be descriptive: what moved, by how
much, where. Anything about **why** goes in the `hypothesis` field, phrased as a
hypothesis, and it must be checkable by someone with more context than this
pipeline has. The pipeline observes correlation and nothing else.

## Dismissals

Every shortlisted candidate you do not highlight gets a reason code:

* `within-baseline-variation` — the move sits inside its normal range
* `small-absolute-impact` — real but too small to act on
* `duplicate-of-highlight` — the same story, already told
* `data-quality` — an adequacy flag makes the number untrustworthy
* `insufficient-baseline` — too little history to say anything

## Rules the validator enforces

Every number you print must come from a fact in the pack, quoted at the value
the pack gives, and listed in that highlight's `claims` array. You may select
facts, order them, and describe them. You may not compute new ones — no sums,
no ratios, no rounding to a friendlier figure, no "roughly a third". If the
number you want is not in the pack, the honest move is to say something else.
